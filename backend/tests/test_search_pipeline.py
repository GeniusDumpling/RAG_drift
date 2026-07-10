import uuid
from datetime import UTC, datetime, timedelta
from typing import cast

import pytest
from app.core.config import Settings
from app.main import app
from app.models.content import ContentChunk, ContentItem, RawPage
from app.models.search import SearchQuery
from app.repositories.contents import ContentsRepository, CreatedContent, stable_hash
from app.repositories.sources import SourcesRepository
from app.schemas.search import SearchFilters, SearchRequest
from app.services import search as search_service_module
from app.services.retrieval import (
    _MEMORY_COLLECTIONS,
    QdrantIndexer,
    VectorSearchHit,
    _memory_payload_matches,
    _merge_hits,
    _SearchHit,
    _vector_filter_payload,
    retrieve_evidence,
)
from app.services.search import SearchService
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession


def _create_source_job_run(client: TestClient) -> dict[str, str]:
    source = client.post(
        "/sources",
        json={
            "name": f"Docs {uuid.uuid4()}",
            "site_type": "docs",
            "base_url": "https://docs.example.com",
            "allowed_domains": ["docs.example.com"],
            "fetch_mode": "manual",
            "default_language": "en",
            "active": True,
            "config_json": {},
        },
    ).json()
    job = client.post(
        "/jobs",
        json={
            "source_site_id": source["id"],
            "name": "Docs crawl",
            "trigger_mode": "manual",
            "cron_expr": None,
            "seed_config_json": {"urls": ["https://docs.example.com/telemetry"]},
            "parser_profile": "official_site",
            "max_pages": 1,
            "enabled": True,
            "agent_policy_json": {"extraction_mode": "hybrid"},
        },
    ).json()
    run = client.post(
        f"/jobs/{job['id']}/trigger", json={"seed_url": "https://docs.example.com/telemetry"}
    ).json()
    return {"source_id": source["id"], "job_id": job["id"], "run_id": run["id"]}


def _ingest_content(
    client: TestClient,
    *,
    source_id: str,
    run_id: str,
    url_path: str,
    item_type: str,
    title: str,
    cleaned_text: str,
    summary_text: str | None,
    tags: list[str],
) -> dict[str, object]:
    return cast(
        dict[str, object],
        client.post(
            "/contents/test-ingest",
            json={
                "source_site_id": source_id,
                "crawl_run_id": run_id,
                "requested_url": f"https://docs.example.com/{url_path}",
                "final_url": f"https://docs.example.com/{url_path}",
                "raw_html": f"<p>{cleaned_text}</p>",
                "item_type": item_type,
                "title": title,
                "cleaned_text": cleaned_text,
                "summary_text": summary_text,
                "tags": tags,
            },
        ).json(),
    )


def _use_memory_vector_backend(monkeypatch: pytest.MonkeyPatch) -> Settings:
    _MEMORY_COLLECTIONS.clear()
    settings = Settings(
        QDRANT_URL=f"memory://search-pipeline-{uuid.uuid4()}",
        QDRANT_COLLECTION=f"content_chunks_{uuid.uuid4().hex}",
    )
    monkeypatch.setattr("app.services.search.get_settings", lambda: settings)
    return settings


def _first_chunk_id(ingest: dict[str, object]) -> uuid.UUID:
    chunk_ids = ingest["chunk_ids"]
    assert isinstance(chunk_ids, list)
    assert chunk_ids != []
    return uuid.UUID(str(chunk_ids[0]))


class _ControlledEmbeddingService:
    model_name = "controlled-test-embedding"
    dimension = 2

    def __init__(self, vectors: dict[str, list[float]]) -> None:
        self._vectors = vectors

    def embed(self, text: str) -> list[float]:
        return list(self._vectors.get(text, [0.0, 1.0]))


class _FixedEmbeddingService:
    model_name = "fixed-local-test-embedding"
    dimension = 16

    def embed(self, text: str) -> list[float]:
        value = 1.0 if text.strip() else 0.0
        return [value for _ in range(self.dimension)]


async def _create_retrieval_source_run(
    session: AsyncSession,
    *,
    url_path: str,
) -> tuple[uuid.UUID, uuid.UUID]:
    sources_repo = SourcesRepository(session)
    source = await sources_repo.create_source(
        {
            "name": f"Docs {uuid.uuid4()}",
            "site_type": "docs",
            "base_url": f"https://docs-{uuid.uuid4()}.example.com",
            "allowed_domains": ["docs.example.com"],
            "fetch_mode": "manual",
            "default_language": "en",
            "active": True,
            "config_json": {},
        }
    )
    job = await sources_repo.create_job(
        {
            "source_site_id": source.id,
            "name": "Docs crawl",
            "trigger_mode": "manual",
            "cron_expr": None,
            "seed_config_json": {"urls": [f"https://docs.example.com/{url_path}"]},
            "parser_profile": "official_site",
            "max_pages": 1,
            "enabled": True,
            "agent_policy_json": {"extraction_mode": "hybrid"},
        }
    )
    run = await sources_repo.create_run_with_queued_event(
        job,
        trigger_type="manual",
        status="queued",
        seed_url=f"https://docs.example.com/{url_path}",
    )
    return source.id, run.id


def _build_single_chunk_content(
    *,
    source_id: uuid.UUID,
    run_id: uuid.UUID,
    url_path: str,
    item_type: str,
    title: str,
    cleaned_text: str,
    summary_text: str | None,
    tags: list[str],
    created_at: datetime,
) -> tuple[RawPage, ContentItem, ContentChunk]:
    raw_page_id = uuid.uuid4()
    content_item_id = uuid.uuid4()
    chunk_id = uuid.uuid4()
    requested_url = f"https://docs.example.com/{url_path}"
    raw_html = f"<p>{cleaned_text}</p>"
    body_hash = stable_hash(raw_html)
    content_hash = stable_hash(cleaned_text)
    raw_page = RawPage(
        id=raw_page_id,
        source_site_id=source_id,
        crawl_run_id=run_id,
        requested_url=requested_url,
        final_url=requested_url,
        http_status=200,
        content_type="text/html",
        response_headers_json={},
        raw_html=raw_html,
        raw_text=None,
        raw_json={},
        fetched_at=created_at,
        fetch_error=None,
        parser_profile=None,
        extraction_method="test_ingest",
        extraction_confidence=None,
        parse_status="parsed",
        parse_error=None,
        body_hash=body_hash,
        created_at=created_at,
    )
    content_item = ContentItem(
        id=content_item_id,
        source_site_id=source_id,
        raw_page_id=raw_page_id,
        crawl_run_id=run_id,
        author_id=None,
        parent_item_id=None,
        thread_root_id=None,
        item_type=item_type,
        title=title,
        canonical_url=requested_url,
        source_url=requested_url,
        published_at=None,
        language=None,
        raw_text=raw_html,
        cleaned_text=cleaned_text,
        summary_text=summary_text,
        structured_by="test_ingest",
        extraction_confidence=None,
        tags=tags,
        metadata_json={"body_hash": body_hash, "ingest_route": "bulk-test-ingest"},
        content_hash=content_hash,
        dedup_key=stable_hash(f"{source_id}:{requested_url}:{item_type}:{content_hash}"),
        search_tsv=None,
        created_at=created_at,
        updated_at=created_at,
    )
    content_chunk = ContentChunk(
        id=chunk_id,
        content_item_id=content_item_id,
        chunk_index=0,
        char_start=0,
        char_end=len(cleaned_text),
        display_text=cleaned_text,
        embed_text=cleaned_text,
        token_count=len(cleaned_text.split()),
        chunk_metadata_json={},
        qdrant_point_id=None,
        vector_backend=None,
        vector_point_id=None,
        embedded_at=None,
        embed_status="pending",
        embed_error=None,
        created_at=created_at,
        updated_at=created_at,
    )
    return raw_page, content_item, content_chunk


async def _create_retrieval_content(
    session: AsyncSession,
    *,
    url_path: str,
    title: str,
    cleaned_text: str,
    summary_text: str | None = None,
    tags: list[str] | None = None,
) -> tuple[CreatedContent, uuid.UUID]:
    sources_repo = SourcesRepository(session)
    source = await sources_repo.create_source(
        {
            "name": f"Docs {uuid.uuid4()}",
            "site_type": "docs",
            "base_url": f"https://docs-{uuid.uuid4()}.example.com",
            "allowed_domains": ["docs.example.com"],
            "fetch_mode": "manual",
            "default_language": "en",
            "active": True,
            "config_json": {},
        }
    )
    job = await sources_repo.create_job(
        {
            "source_site_id": source.id,
            "name": "Docs crawl",
            "trigger_mode": "manual",
            "cron_expr": None,
            "seed_config_json": {"urls": [f"https://docs.example.com/{url_path}"]},
            "parser_profile": "official_site",
            "max_pages": 1,
            "enabled": True,
            "agent_policy_json": {"extraction_mode": "hybrid"},
        }
    )
    run = await sources_repo.create_run_with_queued_event(
        job,
        trigger_type="manual",
        status="queued",
        seed_url=f"https://docs.example.com/{url_path}",
    )
    created = await ContentsRepository(session).create_raw_page_and_item_for_test_ingest(
        source_site_id=source.id,
        crawl_run_id=run.id,
        requested_url=f"https://docs.example.com/{url_path}",
        final_url=f"https://docs.example.com/{url_path}",
        raw_html=f"<p>{cleaned_text}</p>",
        raw_text=None,
        item_type="doc_page",
        title=title,
        cleaned_text=cleaned_text,
        summary_text=summary_text,
        tags=tags or [],
        extraction_confidence=None,
    )
    await session.flush()
    return created, source.id


def _index_memory_chunk(
    *,
    settings: Settings,
    chunk_id: uuid.UUID,
    embed_text: str,
    source_id: str,
    item_type: str,
    content_item_id: object | None,
    tags: list[str] | None = None,
) -> None:
    indexer = QdrantIndexer(
        url=settings.qdrant_url,
        collection=settings.qdrant_collection,
        embedding=_FixedEmbeddingService(),
    )
    indexer.ensure_collection()
    payload = {
        "source_site_id": source_id,
        "item_type": item_type,
        "tags": tags or [],
    }
    if content_item_id is not None:
        payload["content_item_id"] = str(content_item_id)
    indexer.upsert_chunk(chunk_id=chunk_id, embed_text=embed_text, payload=payload)


def test_search_records_raw_and_optimized_query_and_returns_evidence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "app.services.retrieval.QdrantIndexer.search_chunks",
        lambda self, *, query_text, filters, top_k: [],
    )
    client = TestClient(app)
    ids = _create_source_job_run(client)
    _ingest_content(
        client,
        source_id=ids["source_id"],
        run_id=ids["run_id"],
        url_path="telemetry",
        item_type="doc_page",
        title="Telemetry settings",
        cleaned_text="Telemetry can be disabled in settings.",
        summary_text="How to disable telemetry.",
        tags=["telemetry", "settings"],
    )

    response = client.post(
        "/search",
        json={
            "query": "disable telemetry",
            "mode": "search",
            "filters": {"source_site_id": ids["source_id"], "item_type": "doc_page"},
            "top_k": 5,
        },
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["query"]["raw_query"] == "disable telemetry"
    assert payload["query"]["optimized_query_text"]
    assert payload["evidence"] != []
    assert payload["evidence"][0]["canonical_url"] == "https://docs.example.com/telemetry"
    assert payload["evidence"][0]["matched_by"] in {"keyword", "vector", "hybrid"}

    debug_response = client.get(f"/search-queries/{payload['query']['id']}")
    assert debug_response.status_code == 200
    debug_payload = debug_response.json()
    assert debug_payload["raw_query"] == "disable telemetry"
    assert debug_payload["query_trace_json"]["retrieval"]["vector"]["attempted"] is True
    assert debug_payload["query_trace_json"]["retrieval"]["vector"]["hit_count"] == 0
    assert debug_payload["query_trace_json"]["retrieval"]["keyword"]["hit_count"] >= 1
    assert debug_payload["query_trace_json"]["retrieval"]["merged_hit_count"] >= 1
    assert debug_payload["query_trace_json"]["retrieval"]["hydrated_evidence_count"] >= 1


async def test_search_persists_optimization_before_retrieval_failure(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def fail_after_optimization(*args: object, **kwargs: object) -> object:
        raise RuntimeError("retrieval exploded after optimization")

    monkeypatch.setattr(search_service_module, "retrieve_evidence", fail_after_optimization)

    with pytest.raises(RuntimeError, match="retrieval exploded"):
        await SearchService(db_session).search(
            SearchRequest(query="optimized telemetry", mode="search", top_k=3)
        )

    search_query = await db_session.scalar(select(SearchQuery))
    assert search_query is not None
    assert search_query.raw_query == "optimized telemetry"
    assert search_query.optimized_query_text == "optimized telemetry"
    assert search_query.keyword_terms_json == ["optimized", "telemetry"]
    assert search_query.used_agent is True
    assert search_query.result_count is None
    assert search_query.query_trace_json["provider"] == "fake"
    assert search_query.query_trace_json["fallback"] is False
    assert "filter_keys" in search_query.query_trace_json


def test_keyword_fallback_returns_later_matching_chunk(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "app.services.retrieval.QdrantIndexer.search_chunks",
        lambda self, *, query_text, filters, top_k: [],
    )
    client = TestClient(app)
    ids = _create_source_job_run(client)
    first_chunk_text = "alpha " * 260
    later_only_term = "laterchunkneedle"
    ingest = _ingest_content(
        client,
        source_id=ids["source_id"],
        run_id=ids["run_id"],
        url_path="multi-chunk",
        item_type="doc_page",
        title="General operations manual",
        cleaned_text=(
            f"{first_chunk_text}\n\nThis later section mentions {later_only_term} exactly once."
        ),
        summary_text="General overview without the special term.",
        tags=["operations"],
    )
    chunk_ids = ingest["chunk_ids"]
    assert isinstance(chunk_ids, list)
    assert len(chunk_ids) >= 2

    response = client.post(
        "/search",
        json={
            "query": later_only_term,
            "mode": "search",
            "filters": {"source_site_id": ids["source_id"], "item_type": "doc_page"},
            "top_k": 5,
        },
    )

    assert response.status_code == 200
    evidence = response.json()["evidence"]
    assert evidence != []
    assert evidence[0]["chunk_id"] != chunk_ids[0]
    assert evidence[0]["chunk_id"] in chunk_ids[1:]
    assert later_only_term in evidence[0]["snippet"].casefold()


async def test_keyword_fallback_scores_candidates_before_truncating_more_than_1000_recent_matches(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = _use_memory_vector_backend(monkeypatch)
    source_id, run_id = await _create_retrieval_source_run(
        db_session, url_path="older-exact-alpha-beta"
    )
    older_created_at = datetime(2025, 1, 1, tzinfo=UTC)
    recent_created_at = datetime(2025, 1, 2, tzinfo=UTC)
    exact_raw_page, exact_item, exact_chunk = _build_single_chunk_content(
        source_id=source_id,
        run_id=run_id,
        url_path="older-exact-alpha-beta",
        item_type="doc_page",
        title="Alpha beta canonical field report",
        cleaned_text="Alpha beta appears together in the older authoritative report.",
        summary_text="Alpha beta exact source of truth.",
        tags=["alpha", "beta"],
        created_at=older_created_at,
    )
    recent_records: list[tuple[RawPage, ContentItem, ContentChunk]] = []
    for index in range(1001):
        recent_records.append(
            _build_single_chunk_content(
                source_id=source_id,
                run_id=run_id,
                url_path=f"recent-weak-alpha-{index}",
                item_type="doc_page",
                title=f"Recent alpha status {index}",
                cleaned_text="Alpha appears in this recent weaker note without the second term.",
                summary_text=None,
                tags=[],
                created_at=recent_created_at + timedelta(microseconds=index),
            )
        )
    records = [(exact_raw_page, exact_item, exact_chunk), *recent_records]
    db_session.add_all([raw_page for raw_page, _, _ in records])
    await db_session.flush()
    db_session.add_all([content_item for _, content_item, _ in records])
    await db_session.flush()
    db_session.add_all([content_chunk for _, _, content_chunk in records])
    await db_session.flush()

    result = await retrieve_evidence(
        db_session,
        raw_query="alpha beta",
        optimized_query_text="alpha beta",
        keyword_terms=["alpha", "beta"],
        filters=SearchFilters(source_site_id=source_id, item_type="doc_page"),
        top_k=1,
        qdrant_url=settings.qdrant_url,
        qdrant_collection=settings.qdrant_collection,
        embedding=_FixedEmbeddingService(),
    )

    assert result.evidence != []
    assert result.evidence[0].content_item_id == exact_item.id
    assert result.evidence[0].matched_by == "keyword"
    assert result.evidence[0].keyword_score == pytest.approx(1.0)


def test_search_endpoint_returns_vector_only_evidence_from_memory_backend(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = _use_memory_vector_backend(monkeypatch)
    client = TestClient(app)
    ids = _create_source_job_run(client)
    ingest = _ingest_content(
        client,
        source_id=ids["source_id"],
        run_id=ids["run_id"],
        url_path="vector-only",
        item_type="doc_page",
        title="Ordinary maintenance bulletin",
        cleaned_text="This archived bulletin discusses ordinary maintenance procedures.",
        summary_text="Maintenance procedures.",
        tags=["maintenance"],
    )
    vector_query = "latent-vector-only-signal"
    _index_memory_chunk(
        settings=settings,
        chunk_id=_first_chunk_id(ingest),
        embed_text=vector_query,
        source_id=ids["source_id"],
        item_type="doc_page",
        content_item_id=ingest["content_item_id"],
        tags=["maintenance"],
    )

    response = client.post(
        "/search",
        json={
            "query": vector_query,
            "mode": "search",
            "filters": {"source_site_id": ids["source_id"], "item_type": "doc_page"},
            "top_k": 1,
        },
    )

    assert response.status_code == 200
    payload = response.json()
    evidence = payload["evidence"]
    assert len(evidence) == 1
    assert evidence[0]["matched_by"] == "vector"
    assert evidence[0]["content_item_id"] == ingest["content_item_id"]
    assert evidence[0]["raw_page_id"] == ingest["raw_page_id"]
    assert evidence[0]["source_site_id"] == ids["source_id"]
    assert evidence[0]["vector_score"] == pytest.approx(1.0)
    assert evidence[0]["keyword_score"] is None
    trace = payload["query"]["query_trace_json"]["retrieval"]
    assert trace["vector"]["hit_count"] == 1
    assert trace["keyword"]["hit_count"] == 0
    assert trace["component_scores"][0]["vector_score"] == pytest.approx(1.0)


async def test_low_similarity_vector_only_hit_is_not_returned_as_evidence(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = _use_memory_vector_backend(monkeypatch)
    query_text = "controlled-vector-query"
    indexed_text = "controlled-opposite-vector-document"
    embedding = _ControlledEmbeddingService(
        {
            query_text: [1.0, 0.0],
            indexed_text: [-1.0, 0.0],
        }
    )
    created, source_id = await _create_retrieval_content(
        db_session,
        url_path="low-similarity-vector",
        title="Ordinary archived bulletin",
        cleaned_text="This bulletin has no lexical overlap with the semantic probe.",
        summary_text=None,
        tags=[],
    )
    indexer = QdrantIndexer(
        url=settings.qdrant_url,
        collection=settings.qdrant_collection,
        embedding=embedding,
    )
    indexer.ensure_collection()
    indexer.upsert_chunk(
        chunk_id=created.chunks[0].id,
        embed_text=indexed_text,
        payload={
            "source_site_id": str(source_id),
            "item_type": "doc_page",
            "content_item_id": str(created.content_item.id),
            "tags": [],
        },
    )

    result = await retrieve_evidence(
        db_session,
        raw_query=query_text,
        optimized_query_text=query_text,
        keyword_terms=[],
        filters=SearchFilters(source_site_id=source_id, item_type="doc_page"),
        top_k=3,
        qdrant_url=settings.qdrant_url,
        qdrant_collection=settings.qdrant_collection,
        embedding=embedding,
    )

    assert result.trace["vector"]["hit_count"] == 0
    assert result.evidence == []
    assert result.trace["hydrated_evidence_count"] == 0


def test_search_endpoint_marks_hybrid_when_vector_and_keyword_hit_same_chunk(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = _use_memory_vector_backend(monkeypatch)
    client = TestClient(app)
    ids = _create_source_job_run(client)
    query = "hybridneedle fusion"
    ingest = _ingest_content(
        client,
        source_id=ids["source_id"],
        run_id=ids["run_id"],
        url_path="hybrid",
        item_type="doc_page",
        title="Hybridneedle fusion notes",
        cleaned_text="Hybridneedle fusion appears in both lexical and semantic evidence.",
        summary_text="Hybridneedle fusion summary.",
        tags=["hybridneedle"],
    )
    _index_memory_chunk(
        settings=settings,
        chunk_id=_first_chunk_id(ingest),
        embed_text=query,
        source_id=ids["source_id"],
        item_type="doc_page",
        content_item_id=ingest["content_item_id"],
        tags=["hybridneedle"],
    )

    response = client.post(
        "/search",
        json={
            "query": query,
            "mode": "search",
            "filters": {"source_site_id": ids["source_id"], "item_type": "doc_page"},
            "top_k": 1,
        },
    )

    assert response.status_code == 200
    payload = response.json()
    evidence = payload["evidence"]
    assert len(evidence) == 1
    assert evidence[0]["matched_by"] == "hybrid"
    assert evidence[0]["score"] <= 1.0
    assert evidence[0]["vector_score"] == pytest.approx(1.0)
    assert evidence[0]["keyword_score"] is not None
    assert evidence[0]["keyword_score"] > 0.0
    trace = payload["query"]["query_trace_json"]["retrieval"]
    assert trace["component_scores"][0]["matched_by"] == "hybrid"
    assert trace["component_scores"][0]["keyword_score"] is not None


async def test_keyword_retrieval_excludes_obsolete_chunk(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = _use_memory_vector_backend(monkeypatch)
    keyword = "obsoletekeyword"
    created, source_id = await _create_retrieval_content(
        db_session,
        url_path="obsolete-keyword",
        title="Obsolete keyword bulletin",
        cleaned_text=f"This obsolete chunk contains {keyword} and must not be evidence.",
        summary_text=None,
        tags=[],
    )
    created.chunks[0].embed_status = "obsolete"
    await db_session.flush()

    result = await retrieve_evidence(
        db_session,
        raw_query=keyword,
        optimized_query_text=keyword,
        keyword_terms=[keyword],
        filters=SearchFilters(source_site_id=source_id, item_type="doc_page"),
        top_k=5,
        qdrant_url=settings.qdrant_url,
        qdrant_collection=settings.qdrant_collection,
        embedding=_FixedEmbeddingService(),
    )

    assert result.evidence == []
    assert result.trace["keyword"]["hit_count"] == 0
    assert result.trace["hydrated_evidence_count"] == 0


async def test_stale_vector_hit_for_obsolete_chunk_is_not_hydrated(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = _use_memory_vector_backend(monkeypatch)
    vector_query = "obsolete-vector-only-signal"
    created, source_id = await _create_retrieval_content(
        db_session,
        url_path="obsolete-vector",
        title="Obsolete vector bulletin",
        cleaned_text="This retired bulletin should not hydrate from a stale vector point.",
        summary_text=None,
        tags=[],
    )
    obsolete_chunk = created.chunks[0]
    obsolete_chunk.embed_status = "obsolete"
    await db_session.flush()
    _index_memory_chunk(
        settings=settings,
        chunk_id=obsolete_chunk.id,
        embed_text=vector_query,
        source_id=str(source_id),
        item_type="doc_page",
        content_item_id=created.content_item.id,
    )

    result = await retrieve_evidence(
        db_session,
        raw_query=vector_query,
        optimized_query_text=vector_query,
        keyword_terms=[],
        filters=SearchFilters(source_site_id=source_id, item_type="doc_page"),
        top_k=3,
        qdrant_url=settings.qdrant_url,
        qdrant_collection=settings.qdrant_collection,
        embedding=_FixedEmbeddingService(),
    )

    assert result.trace["vector"]["hit_count"] == 1
    assert result.evidence == []
    assert result.trace["hydrated_evidence_count"] == 0


def test_search_endpoint_drops_vector_hit_with_wrong_content_item_payload(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = _use_memory_vector_backend(monkeypatch)
    client = TestClient(app)
    ids = _create_source_job_run(client)
    first = _ingest_content(
        client,
        source_id=ids["source_id"],
        run_id=ids["run_id"],
        url_path="wrong-vector-payload-a",
        item_type="doc_page",
        title="First ordinary bulletin",
        cleaned_text="First ordinary bulletin without the semantic-only token.",
        summary_text=None,
        tags=[],
    )
    second = _ingest_content(
        client,
        source_id=ids["source_id"],
        run_id=ids["run_id"],
        url_path="wrong-vector-payload-b",
        item_type="doc_page",
        title="Second ordinary bulletin",
        cleaned_text="Second ordinary bulletin without the semantic-only token.",
        summary_text=None,
        tags=[],
    )
    vector_query = "wrong-content-item-vector-signal"
    _index_memory_chunk(
        settings=settings,
        chunk_id=_first_chunk_id(first),
        embed_text=vector_query,
        source_id=ids["source_id"],
        item_type="doc_page",
        content_item_id=second["content_item_id"],
    )

    response = client.post(
        "/search",
        json={
            "query": vector_query,
            "mode": "search",
            "filters": {"source_site_id": ids["source_id"], "item_type": "doc_page"},
            "top_k": 3,
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["evidence"] == []
    trace = payload["query"]["query_trace_json"]["retrieval"]
    assert trace["vector"]["hit_count"] == 1
    assert trace["hydrated_evidence_count"] == 0


def test_search_endpoint_prefers_keyword_content_item_id_for_same_chunk_hybrid_hit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = _use_memory_vector_backend(monkeypatch)
    client = TestClient(app)
    ids = _create_source_job_run(client)
    keyword = "authoritativekeyword"
    first = _ingest_content(
        client,
        source_id=ids["source_id"],
        run_id=ids["run_id"],
        url_path="hybrid-wrong-vector-payload-a",
        item_type="doc_page",
        title="Authoritative keyword bulletin",
        cleaned_text=f"This authoritative chunk contains {keyword} for SQL evidence.",
        summary_text=None,
        tags=[],
    )
    second = _ingest_content(
        client,
        source_id=ids["source_id"],
        run_id=ids["run_id"],
        url_path="hybrid-wrong-vector-payload-b",
        item_type="doc_page",
        title="Unrelated ordinary bulletin",
        cleaned_text="Unrelated ordinary bulletin without the target token.",
        summary_text=None,
        tags=[],
    )
    _index_memory_chunk(
        settings=settings,
        chunk_id=_first_chunk_id(first),
        embed_text=keyword,
        source_id=ids["source_id"],
        item_type="doc_page",
        content_item_id=second["content_item_id"],
    )

    response = client.post(
        "/search",
        json={
            "query": keyword,
            "mode": "search",
            "filters": {"source_site_id": ids["source_id"], "item_type": "doc_page"},
            "top_k": 3,
        },
    )

    assert response.status_code == 200
    payload = response.json()
    evidence = payload["evidence"]
    assert len(evidence) == 1
    assert evidence[0]["content_item_id"] == first["content_item_id"]
    assert evidence[0]["matched_by"] in {"hybrid", "keyword"}
    trace = payload["query"]["query_trace_json"]["retrieval"]
    assert trace["vector"]["hit_count"] == 1
    assert trace["keyword"]["hit_count"] >= 1
    assert trace["hydrated_evidence_count"] == 1


def test_search_endpoint_reapplies_filters_to_authoritative_postgres_rows(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = _use_memory_vector_backend(monkeypatch)
    client = TestClient(app)
    allowed_ids = _create_source_job_run(client)
    other_ids = _create_source_job_run(client)
    other_ingest = _ingest_content(
        client,
        source_id=other_ids["source_id"],
        run_id=other_ids["run_id"],
        url_path="filter-reapply-other-source",
        item_type="doc_page",
        title="Other source bulletin",
        cleaned_text="Other source bulletin without the semantic-only token.",
        summary_text=None,
        tags=[],
    )
    vector_query = "spoofed-filter-vector-signal"
    _index_memory_chunk(
        settings=settings,
        chunk_id=_first_chunk_id(other_ingest),
        embed_text=vector_query,
        source_id=allowed_ids["source_id"],
        item_type="doc_page",
        content_item_id=other_ingest["content_item_id"],
    )

    response = client.post(
        "/search",
        json={
            "query": vector_query,
            "mode": "search",
            "filters": {"source_site_id": allowed_ids["source_id"], "item_type": "doc_page"},
            "top_k": 3,
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["evidence"] == []
    trace = payload["query"]["query_trace_json"]["retrieval"]
    assert trace["vector"]["hit_count"] == 1
    assert trace["hydrated_evidence_count"] == 0


def test_retrieval_trace_persists_vector_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    def raise_vector_failure(
        self: object, *, query_text: str, filters: dict[str, object], top_k: int
    ) -> object:
        raise RuntimeError("forced vector failure")

    monkeypatch.setattr(
        "app.services.retrieval.QdrantIndexer.search_chunks",
        raise_vector_failure,
    )
    client = TestClient(app)
    ids = _create_source_job_run(client)
    _ingest_content(
        client,
        source_id=ids["source_id"],
        run_id=ids["run_id"],
        url_path="vector-failure",
        item_type="doc_page",
        title="Vector fallback telemetry",
        cleaned_text="Telemetry fallback path should still find keyword evidence.",
        summary_text="Telemetry fallback.",
        tags=["telemetry"],
    )

    response = client.post(
        "/search",
        json={
            "query": "telemetry fallback",
            "mode": "search",
            "filters": {"source_site_id": ids["source_id"], "item_type": "doc_page"},
            "top_k": 5,
        },
    )

    assert response.status_code == 200
    payload = response.json()
    trace = payload["query"]["query_trace_json"]["retrieval"]
    assert trace["vector"]["attempted"] is True
    assert trace["vector"]["failed"] is True
    assert "forced vector failure" in trace["vector"]["error"]
    assert trace["vector"]["hit_count"] == 0
    assert trace["keyword"]["hit_count"] >= 1
    assert trace["merged_hit_count"] >= 1
    assert trace["hydrated_evidence_count"] >= 1

    debug_response = client.get(f"/search-queries/{payload['query']['id']}")
    assert debug_response.status_code == 200
    debug_trace = debug_response.json()["query_trace_json"]["retrieval"]
    assert debug_trace["vector"]["failed"] is True
    assert "forced vector failure" in debug_trace["vector"]["error"]


def test_blank_search_query_is_rejected() -> None:
    client = TestClient(app)

    response = client.post("/search", json={"query": " \t\n ", "mode": "search"})

    assert response.status_code == 422


def test_search_endpoint_rejects_unknown_filter_field() -> None:
    client = TestClient(app)

    response = client.post(
        "/search",
        json={
            "query": "telemetry",
            "mode": "search",
            "filters": {"source_site_uuid": str(uuid.uuid4())},
        },
    )

    assert response.status_code == 422
    assert any(
        error["type"] == "extra_forbidden"
        and error["loc"] == ["body", "filters", "source_site_uuid"]
        for error in response.json()["detail"]
    )


def test_search_endpoint_rejects_unknown_top_level_field() -> None:
    client = TestClient(app)

    response = client.post(
        "/search",
        json={"query": "telemetry", "mode": "search", "top_kk": 5},
    )

    assert response.status_code == 422
    assert any(
        error["type"] == "extra_forbidden" and error["loc"] == ["body", "top_kk"]
        for error in response.json()["detail"]
    )


def test_search_endpoint_rejects_answer_mode() -> None:
    client = TestClient(app)

    response = client.post("/search", json={"query": "telemetry", "mode": "answer"})

    assert response.status_code == 422
    assert any(
        error["type"] == "literal_error" and error["loc"] == ["body", "mode"]
        for error in response.json()["detail"]
    )


def test_merge_hits_deduplicates_by_chunk_id_when_vector_hit_lacks_content_item_id() -> None:
    chunk_id = uuid.uuid4()
    content_item_id = uuid.uuid4()

    merged = _merge_hits(
        vector_hits=[VectorSearchHit(chunk_id=chunk_id, content_item_id=None, score=0.7)],
        keyword_hits=[
            _SearchHit(
                chunk_id=chunk_id,
                content_item_id=content_item_id,
                score=0.4,
                matched_by="keyword",
            )
        ],
    )

    assert len(merged) == 1
    assert merged[0].chunk_id == chunk_id
    assert merged[0].content_item_id == content_item_id
    assert merged[0].matched_by == "hybrid"
    assert merged[0].score == pytest.approx(0.8)


def test_vector_filter_payload_and_memory_matching_include_published_dates() -> None:
    filters = SearchFilters.model_validate(
        {
            "published_after": "2025-01-01T00:00:00+00:00",
            "published_before": "2025-12-31T23:59:59+00:00",
        }
    )

    payload = _vector_filter_payload(filters)

    assert payload["published_after"] == filters.published_after
    assert payload["published_before"] == filters.published_before
    assert _memory_payload_matches(
        payload={"published_at": "2025-06-01T00:00:00+00:00"}, filters=payload
    )
    assert not _memory_payload_matches(
        payload={"published_at": "2024-12-31T23:59:59+00:00"}, filters=payload
    )
    assert not _memory_payload_matches(
        payload={"published_at": "2026-01-01T00:00:00+00:00"}, filters=payload
    )
