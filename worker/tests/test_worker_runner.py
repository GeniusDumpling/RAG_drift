import hashlib
import json
import os
import subprocess
import sys
import uuid
from pathlib import Path
from typing import Any, cast

import app.db.session as db_session_module
import pytest
from app.agents.contracts import ExtractionAgentRequest, ExtractionAgentResponse, ExtractionItem
from app.core.config import get_settings
from app.db.base import utcnow
from app.main import app
from app.models.content import ContentChunk, ContentItem, RawPage
from app.models.control import CrawlJob, CrawlRun, CrawlRunEvent, SourceSite
from app.services.chunking import build_chunks
from app.services.embeddings import DeterministicEmbeddingService
from app.services.retrieval import _MEMORY_COLLECTIONS, QdrantIndexer
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event, insert, select, text
from sqlalchemy.orm import Session

import worker.app.chunk_indexer as chunk_indexer_module
import worker.app.runner as runner_module
from worker.app.adapters import DiscoveredPage, FetchedPage, OfficialSiteAdapter
from worker.app.normalizer import normalize_extraction_response
from worker.app.runner import run_once

REPO_ROOT = Path(__file__).resolve().parents[2]


def _create_worker_job(
    client: TestClient,
    *,
    parser_profile: str = "official_site",
    urls: list[str] | None = None,
    max_pages: int = 1,
    seed_url: str = "https://example.com/a",
) -> dict[str, object]:
    source = cast(
        dict[str, object],
        client.post(
            "/sources",
            json={
                "name": "Demo Official Site",
                "site_type": "docs",
                "base_url": "https://example.com",
                "allowed_domains": ["example.com"],
                "fetch_mode": "manual",
                "default_language": "en",
                "active": True,
                "config_json": {},
            },
        ).json(),
    )
    configured_urls = [seed_url] if urls is None else urls
    return cast(
        dict[str, object],
        client.post(
            "/jobs",
            json={
                "source_site_id": source["id"],
                "name": "Manual official crawl",
                "trigger_mode": "manual",
                "cron_expr": None,
                "seed_config_json": {"urls": configured_urls},
                "parser_profile": parser_profile,
                "max_pages": max_pages,
                "enabled": True,
                "agent_policy_json": {"extraction_mode": "hybrid"},
            },
        ).json(),
    )


def _trigger_worker_job(
    client: TestClient, job: dict[str, object], *, seed_url: str = "https://example.com/a"
) -> dict[str, object]:
    return cast(
        dict[str, object],
        client.post(f"/jobs/{job['id']}/trigger", json={"seed_url": seed_url}).json(),
    )


def _queue_worker_run(
    client: TestClient,
    *,
    parser_profile: str = "official_site",
    urls: list[str] | None = None,
    max_pages: int = 1,
    seed_url: str = "https://example.com/a",
) -> dict[str, object]:
    job = _create_worker_job(
        client,
        parser_profile=parser_profile,
        urls=urls,
        max_pages=max_pages,
        seed_url=seed_url,
    )
    return _trigger_worker_job(client, job, seed_url=seed_url)


def _fetch_db_rows(query: str, params: dict[str, object]) -> list[dict[str, object]]:
    engine = create_engine(os.environ["SYNC_DATABASE_URL"], pool_pre_ping=True)
    try:
        with engine.connect() as connection:
            rows = connection.execute(text(query), params).mappings().all()
            return [dict(row) for row in rows]
    finally:
        engine.dispose()


def _current_vector_metadata() -> dict[str, object]:
    embedding = DeterministicEmbeddingService()
    return {
        "vector_collection": get_settings().qdrant_collection,
        "vector_store_id": hashlib.sha256(
            get_settings().qdrant_url.encode("utf-8")
        ).hexdigest(),
        "embedding_model": embedding.model_name,
        "embedding_dimension": embedding.dimension,
    }


async def _create_indexable_content_item(
    session: Any,
    *,
    item_type: str,
    cleaned_text: str,
    title: str | None = None,
    tags: list[str] | None = None,
) -> tuple[uuid.UUID, uuid.UUID]:
    unique = uuid.uuid4().hex
    source_site = SourceSite(
        name=f"Chunk Index Source {unique}",
        site_type="docs",
        base_url="https://example.com",
        allowed_domains=["example.com"],
        fetch_mode="manual",
        default_language="en",
        active=True,
        config_json={},
    )
    session.add(source_site)
    await session.flush()

    crawl_job = CrawlJob(
        source_site_id=source_site.id,
        name=f"Chunk index job {unique}",
        trigger_mode="manual",
        cron_expr=None,
        seed_config_json={"urls": [f"https://example.com/{unique}"]},
        parser_profile="official_site",
        max_pages=1,
        enabled=True,
        agent_policy_json={},
    )
    session.add(crawl_job)
    await session.flush()

    crawl_run = CrawlRun(
        source_site_id=source_site.id,
        crawl_job_id=crawl_job.id,
        trigger_type="manual",
        seed_url=f"https://example.com/{unique}",
        status="running",
        config_snapshot_json={},
    )
    session.add(crawl_run)
    await session.flush()

    raw_page = RawPage(
        source_site_id=source_site.id,
        crawl_run_id=crawl_run.id,
        requested_url=f"https://example.com/{unique}",
        final_url=f"https://example.com/{unique}",
        http_status=200,
        content_type="text/html",
        response_headers_json={},
        raw_html="<html></html>",
        raw_text=cleaned_text,
        raw_json={},
        fetched_at=utcnow(),
        fetch_error=None,
        parser_profile="official_site",
        extraction_method="extraction_agent",
        extraction_confidence=0.9,
        parse_status="parsed",
        parse_error=None,
        body_hash=f"body-hash-{unique}",
    )
    session.add(raw_page)
    await session.flush()

    content_item = ContentItem(
        source_site_id=source_site.id,
        raw_page_id=raw_page.id,
        crawl_run_id=crawl_run.id,
        author_id=None,
        parent_item_id=None,
        thread_root_id=None,
        item_type=item_type,
        title=title,
        canonical_url=f"https://example.com/{unique}",
        source_url=f"https://example.com/{unique}",
        published_at=None,
        language="en",
        raw_text=cleaned_text,
        cleaned_text=cleaned_text,
        summary_text=None,
        structured_by="extraction_agent",
        extraction_confidence=0.9,
        tags=[] if tags is None else tags,
        metadata_json={},
        content_hash=f"content-hash-{unique}",
        dedup_key=f"dedup-key-{unique}",
        search_tsv=None,
    )
    session.add(content_item)
    await session.commit()
    return crawl_run.id, content_item.id


def test_run_worker_once_script_executes_directly_with_no_queued_runs() -> None:
    env = os.environ.copy()
    env.pop("PYTHONPATH", None)

    completed = subprocess.run(
        [sys.executable, "scripts/run_worker_once.py"],
        cwd=REPO_ROOT,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    assert "WorkerRunResult(claimed=0, succeeded=0, partial=0, failed=0)" in completed.stdout


def test_seed_demo_reuses_existing_queued_run_unless_new_run_is_requested() -> None:
    env = os.environ.copy()
    env.pop("PYTHONPATH", None)

    first = subprocess.run(
        [sys.executable, "scripts/seed_demo.py"],
        cwd=REPO_ROOT,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )
    second = subprocess.run(
        [sys.executable, "scripts/seed_demo.py"],
        cwd=REPO_ROOT,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )
    third = subprocess.run(
        [sys.executable, "scripts/seed_demo.py", "--new-run"],
        cwd=REPO_ROOT,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )

    assert first.returncode == 0, first.stderr
    assert second.returncode == 0, second.stderr
    assert third.returncode == 0, third.stderr
    first_seed = json.loads(first.stdout)
    second_seed = json.loads(second.stdout)
    third_seed = json.loads(third.stdout)
    assert first_seed["source_id"] == second_seed["source_id"] == third_seed["source_id"]
    assert first_seed["job_id"] == second_seed["job_id"] == third_seed["job_id"]
    assert first_seed["run_id"] == second_seed["run_id"]
    assert second_seed["reused_run"] is True
    assert third_seed["run_id"] != first_seed["run_id"]
    assert third_seed["reused_run"] is False


def test_run_worker_once_script_with_run_id_processes_only_that_queued_run() -> None:
    client = TestClient(app)
    older_run = _queue_worker_run(client, seed_url="https://example.com/older-run")
    target_run = _queue_worker_run(client, seed_url="https://example.com/target-run")
    env = os.environ.copy()
    env.pop("PYTHONPATH", None)

    completed = subprocess.run(
        [sys.executable, "scripts/run_worker_once.py", "--run-id", str(target_run["id"])],
        cwd=REPO_ROOT,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    assert "WorkerRunResult(claimed=1, succeeded=1, partial=0, failed=0)" in completed.stdout
    assert client.get(f"/runs/{older_run['id']}").json()["status"] == "queued"
    assert client.get(f"/runs/{target_run['id']}").json()["status"] == "success"


def test_run_once_with_run_id_claims_only_requested_queued_run() -> None:
    client = TestClient(app)
    older_run = _queue_worker_run(client, seed_url="https://example.com/oldest")
    target_run = _queue_worker_run(client, seed_url="https://example.com/requested")

    result = run_once(run_id=uuid.UUID(str(target_run["id"])))

    assert result.claimed == 1
    assert result.succeeded == 1
    assert result.partial == 0
    assert result.failed == 0
    assert client.get(f"/runs/{older_run['id']}").json()["status"] == "queued"
    assert client.get(f"/runs/{target_run['id']}").json()["status"] == "success"


def test_worker_persists_raw_page_before_extraction_and_marks_success() -> None:
    client = TestClient(app)
    run = _queue_worker_run(client)

    result = run_once(run_limit=1)
    assert result.claimed == 1
    assert result.succeeded == 1

    detail = client.get(f"/runs/{run['id']}").json()
    assert detail["status"] == "success"
    assert detail["fetched_count"] == 1
    assert detail["extracted_count"] >= 1

    events = client.get(f"/runs/{run['id']}/events").json()["items"]
    event_types = [event["event_type"] for event in events]
    assert event_types.index("raw_page_persisted") < event_types.index(
        "extraction_agent_called"
    )

    agent_calls = _fetch_db_rows(
        "select status from agent_calls where related_crawl_run_id = CAST(:run_id AS uuid)",
        {"run_id": run["id"]},
    )
    assert [agent_call["status"] for agent_call in agent_calls] == ["success"]

    chunks = _fetch_db_rows(
        """
        select embed_status, vector_backend, vector_point_id, qdrant_point_id,
               embedded_at, token_count
        from content_chunks
        order by chunk_index asc
        """,
        {},
    )
    assert len(chunks) >= 1
    assert {chunk["embed_status"] for chunk in chunks} == {"success"}
    assert {chunk["vector_backend"] for chunk in chunks} == {"memory"}
    assert all(chunk["vector_point_id"] for chunk in chunks)
    assert all(chunk["qdrant_point_id"] is None for chunk in chunks)
    assert all(chunk["embedded_at"] is not None for chunk in chunks)
    assert all(
        isinstance(chunk["token_count"], int) and chunk["token_count"] >= 1
        for chunk in chunks
    )

    detail_after_indexing = client.get(f"/runs/{run['id']}").json()
    assert detail_after_indexing["chunked_count"] == len(chunks)
    assert detail_after_indexing["embedded_count"] == len(chunks)


def test_worker_commits_source_of_truth_before_vector_writes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    observed_committed_rows: list[dict[str, object]] = []

    class FakeEmbedding:
        model_name = "commit-probe-model"
        dimension = 8

    class CommitProbeIndexer:
        backend_name = "qdrant"
        collection = "commit_probe_collection"
        embedding = FakeEmbedding()

        def ensure_collection(self) -> None:
            observed_committed_rows.extend(
                _fetch_db_rows(
                    """
                    select rp.parse_status,
                           rp.extraction_method,
                           rp.extraction_confidence,
                           count(distinct ci.id) as content_item_count,
                           count(cc.id) as chunk_count
                    from raw_pages rp
                    left join content_items ci on ci.raw_page_id = rp.id
                    left join content_chunks cc on cc.content_item_id = ci.id
                    where rp.crawl_run_id = CAST(:run_id AS uuid)
                    group by rp.parse_status, rp.extraction_method, rp.extraction_confidence
                    """,
                    {"run_id": run["id"]},
                )
            )

        def upsert_chunk(
            self,
            *,
            chunk_id: uuid.UUID,
            embed_text: str,
            payload: dict[str, Any],
        ) -> str:
            del embed_text, payload
            return str(chunk_id)

    monkeypatch.setattr(chunk_indexer_module, "_build_qdrant_indexer", CommitProbeIndexer)
    client = TestClient(app)
    run = _queue_worker_run(client, seed_url="https://example.com/commit-before-vector")

    result = run_once(run_limit=1)

    assert result.claimed == 1
    assert result.succeeded == 1
    assert len(observed_committed_rows) == 1
    committed_row = observed_committed_rows[0]
    assert committed_row["parse_status"] == "parsed"
    assert committed_row["extraction_method"] == "extraction_agent"
    assert committed_row["extraction_confidence"] is not None
    assert committed_row["content_item_count"] == 1
    assert isinstance(committed_row["chunk_count"], int)
    assert committed_row["chunk_count"] >= 1


def test_success_chunks_with_stale_vector_collection_are_reindexed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    first_collection = "content_chunks_collection_a"
    second_collection = "content_chunks_collection_b"
    monkeypatch.setenv("QDRANT_COLLECTION", first_collection)
    get_settings.cache_clear()

    client = TestClient(app)
    seed_url = "https://example.com/reindex-stale-collection"
    job = _create_worker_job(client, urls=[seed_url], seed_url=seed_url)
    first_run = _trigger_worker_job(client, job, seed_url=seed_url)

    first_result = run_once(run_limit=1)
    assert first_result.claimed == 1
    assert first_result.succeeded == 1

    first_detail = client.get(f"/runs/{first_run['id']}").json()
    first_chunks = _fetch_db_rows(
        """
        select id, vector_backend, vector_point_id, qdrant_point_id, chunk_metadata_json
        from content_chunks
        order by chunk_index asc
        """,
        {},
    )
    assert first_detail["embedded_count"] == len(first_chunks)
    assert len(first_chunks) >= 1
    assert {chunk["vector_backend"] for chunk in first_chunks} == {"memory"}
    assert all(chunk["vector_point_id"] == str(chunk["id"]) for chunk in first_chunks)
    assert all(chunk["qdrant_point_id"] is None for chunk in first_chunks)
    first_chunk_metadata = [
        cast(dict[str, object], chunk["chunk_metadata_json"]) for chunk in first_chunks
    ]
    assert {metadata["vector_collection"] for metadata in first_chunk_metadata} == {
        first_collection
    }
    assert {metadata["embedding_model"] for metadata in first_chunk_metadata} == {
        "deterministic-hash-v1"
    }
    assert {metadata["embedding_dimension"] for metadata in first_chunk_metadata} == {384}

    monkeypatch.setenv("QDRANT_COLLECTION", second_collection)
    get_settings.cache_clear()
    second_run = _trigger_worker_job(client, job, seed_url=seed_url)

    second_result = run_once(run_limit=1)

    assert second_result.claimed == 1
    assert second_result.succeeded == 1
    second_detail = client.get(f"/runs/{second_run['id']}").json()
    assert second_detail["status"] == "success"
    assert second_detail["deduped_count"] == 1
    assert second_detail["chunked_count"] == 0
    assert second_detail["embedded_count"] == len(first_chunks)

    refreshed_chunks = _fetch_db_rows(
        """
        select id, vector_backend, vector_point_id, qdrant_point_id, chunk_metadata_json
        from content_chunks
        order by chunk_index asc
        """,
        {},
    )
    assert [chunk["id"] for chunk in refreshed_chunks] == [
        chunk["id"] for chunk in first_chunks
    ]
    assert {chunk["vector_backend"] for chunk in refreshed_chunks} == {"memory"}
    assert all(chunk["vector_point_id"] == str(chunk["id"]) for chunk in refreshed_chunks)
    assert all(chunk["qdrant_point_id"] is None for chunk in refreshed_chunks)
    refreshed_chunk_metadata = [
        cast(dict[str, object], chunk["chunk_metadata_json"])
        for chunk in refreshed_chunks
    ]
    assert {metadata["vector_collection"] for metadata in refreshed_chunk_metadata} == {
        second_collection
    }
    assert {metadata["embedding_model"] for metadata in refreshed_chunk_metadata} == {
        "deterministic-hash-v1"
    }
    assert {metadata["embedding_dimension"] for metadata in refreshed_chunk_metadata} == {384}


def test_success_chunks_with_stale_vector_store_identity_are_reindexed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    collection = "content_chunks_same_collection"
    first_url = "memory://worker-vector-store-a"
    second_url = "memory://worker-vector-store-b"
    monkeypatch.setenv("QDRANT_COLLECTION", collection)
    monkeypatch.setenv("QDRANT_URL", first_url)
    get_settings.cache_clear()

    client = TestClient(app)
    seed_url = "https://example.com/reindex-stale-vector-store"
    job = _create_worker_job(client, urls=[seed_url], seed_url=seed_url)
    first_run = _trigger_worker_job(client, job, seed_url=seed_url)

    first_result = run_once(run_limit=1)
    assert first_result.claimed == 1
    assert first_result.succeeded == 1

    first_detail = client.get(f"/runs/{first_run['id']}").json()
    first_chunks = _fetch_db_rows(
        """
        select id, vector_backend, vector_point_id, qdrant_point_id, chunk_metadata_json
        from content_chunks
        order by chunk_index asc
        """,
        {},
    )
    assert first_detail["embedded_count"] == len(first_chunks)
    assert len(first_chunks) >= 1
    assert {chunk["vector_backend"] for chunk in first_chunks} == {"memory"}
    assert all(chunk["vector_point_id"] == str(chunk["id"]) for chunk in first_chunks)
    first_chunk_metadata = [
        cast(dict[str, object], chunk["chunk_metadata_json"]) for chunk in first_chunks
    ]
    assert {metadata["vector_collection"] for metadata in first_chunk_metadata} == {
        collection
    }
    assert {metadata["vector_store_id"] for metadata in first_chunk_metadata} == {
        hashlib.sha256(first_url.encode("utf-8")).hexdigest()
    }

    monkeypatch.setenv("QDRANT_URL", second_url)
    get_settings.cache_clear()
    second_run = _trigger_worker_job(client, job, seed_url=seed_url)

    second_result = run_once(run_limit=1)

    assert second_result.claimed == 1
    assert second_result.succeeded == 1
    second_detail = client.get(f"/runs/{second_run['id']}").json()
    assert second_detail["status"] == "success"
    assert second_detail["deduped_count"] == 1
    assert second_detail["chunked_count"] == 0
    assert second_detail["embedded_count"] == len(first_chunks)

    refreshed_chunks = _fetch_db_rows(
        """
        select id, vector_backend, vector_point_id, qdrant_point_id, chunk_metadata_json
        from content_chunks
        order by chunk_index asc
        """,
        {},
    )
    assert [chunk["id"] for chunk in refreshed_chunks] == [
        chunk["id"] for chunk in first_chunks
    ]
    assert {chunk["vector_backend"] for chunk in refreshed_chunks} == {"memory"}
    assert all(chunk["vector_point_id"] == str(chunk["id"]) for chunk in refreshed_chunks)
    assert all(chunk["qdrant_point_id"] is None for chunk in refreshed_chunks)
    refreshed_chunk_metadata = [
        cast(dict[str, object], chunk["chunk_metadata_json"])
        for chunk in refreshed_chunks
    ]
    assert {metadata["vector_collection"] for metadata in refreshed_chunk_metadata} == {
        collection
    }
    assert {metadata["vector_store_id"] for metadata in refreshed_chunk_metadata} == {
        hashlib.sha256(second_url.encode("utf-8")).hexdigest()
    }
    assert {metadata["embedding_model"] for metadata in refreshed_chunk_metadata} == {
        "deterministic-hash-v1"
    }
    assert {metadata["embedding_dimension"] for metadata in refreshed_chunk_metadata} == {384}


async def test_success_chunks_with_stale_chunker_metadata_are_updated_and_reindexed() -> None:
    _MEMORY_COLLECTIONS.clear()
    cleaned_text = "I reproduced the issue on v1.2."
    async with db_session_module.AsyncSessionLocal() as session:
        run_id, content_item_id = await _create_indexable_content_item(
            session,
            item_type="comment",
            cleaned_text=cleaned_text,
            tags=["bug"],
        )
        content_item = await session.get(ContentItem, content_item_id)
        assert content_item is not None
        expected_chunk = build_chunks(
            item_type=content_item.item_type,
            title=content_item.title,
            cleaned_text=content_item.cleaned_text,
            summary_text=content_item.summary_text,
            tags=content_item.tags,
        )[0]
        stale_chunk_id = uuid.uuid4()
        old_vector_point_uuid = uuid.uuid4()
        old_vector_point_id = str(old_vector_point_uuid)
        memory_indexer = QdrantIndexer(
            url=get_settings().qdrant_url,
            collection=get_settings().qdrant_collection,
            embedding=DeterministicEmbeddingService(),
        )
        memory_indexer.ensure_collection()
        assert memory_indexer.upsert_chunk(
            chunk_id=old_vector_point_uuid,
            embed_text="old embed text",
            payload={"content_item_id": str(content_item_id)},
        ) == old_vector_point_id
        memory_collection = _MEMORY_COLLECTIONS[
            (memory_indexer.url, memory_indexer.collection)
        ]
        assert old_vector_point_id in memory_collection.points
        assert old_vector_point_id != str(stale_chunk_id)
        session.add(
            ContentChunk(
                id=stale_chunk_id,
                content_item_id=content_item_id,
                chunk_index=expected_chunk.chunk_index,
                char_start=0,
                char_end=3,
                display_text="old display",
                embed_text="old embed text",
                token_count=3,
                chunk_metadata_json={
                    **expected_chunk.chunk_metadata_json,
                    **_current_vector_metadata(),
                    "chunker_version": "old-version",
                    "embed_text_hash": "0" * 64,
                },
                qdrant_point_id=None,
                vector_backend="memory",
                vector_point_id=old_vector_point_id,
                embedded_at=utcnow(),
                embed_status="success",
                embed_error=None,
            )
        )
        await session.commit()

        result = await chunk_indexer_module.chunk_and_index_content_items(
            session=session,
            run_id=run_id,
            content_item_ids=[content_item_id],
        )
        await session.commit()

        chunks = (
            await session.scalars(
                select(ContentChunk).order_by(ContentChunk.chunk_index.asc())
            )
        ).all()

    assert result.chunked_count == 0
    assert result.embedded_count == 1
    assert result.failed_count == 0
    assert len(chunks) == 1
    refreshed_chunk = chunks[0]
    assert refreshed_chunk.id == stale_chunk_id
    assert refreshed_chunk.display_text == expected_chunk.display_text
    assert refreshed_chunk.embed_text == expected_chunk.embed_text
    assert refreshed_chunk.char_start == expected_chunk.start_char
    assert refreshed_chunk.char_end == expected_chunk.end_char
    assert refreshed_chunk.token_count == expected_chunk.token_count
    assert refreshed_chunk.embed_status == "success"
    assert refreshed_chunk.embed_error is None
    assert refreshed_chunk.vector_backend == "memory"
    assert refreshed_chunk.vector_point_id == str(stale_chunk_id)
    assert refreshed_chunk.qdrant_point_id is None
    assert refreshed_chunk.embedded_at is not None
    assert (
        refreshed_chunk.chunk_metadata_json["chunker_version"]
        == expected_chunk.chunk_metadata_json["chunker_version"]
    )
    assert (
        refreshed_chunk.chunk_metadata_json["embed_text_hash"]
        == expected_chunk.chunk_metadata_json["embed_text_hash"]
    )
    assert old_vector_point_id not in memory_collection.points
    assert str(stale_chunk_id) in memory_collection.points


async def test_chunk_sync_creates_missing_chunks_and_obsoletes_extra_chunks() -> None:
    _MEMORY_COLLECTIONS.clear()
    cleaned_text = "x" * 1305
    async with db_session_module.AsyncSessionLocal() as session:
        run_id, content_item_id = await _create_indexable_content_item(
            session,
            item_type="comment",
            cleaned_text=cleaned_text,
        )
        content_item = await session.get(ContentItem, content_item_id)
        assert content_item is not None
        expected_chunks = build_chunks(
            item_type=content_item.item_type,
            title=content_item.title,
            cleaned_text=content_item.cleaned_text,
            summary_text=content_item.summary_text,
            tags=content_item.tags,
        )
        assert [chunk.chunk_index for chunk in expected_chunks] == [0, 1]
        current_chunk_id = uuid.uuid4()
        obsolete_chunk_id = uuid.uuid4()
        obsolete_point_id = str(obsolete_chunk_id)
        memory_indexer = QdrantIndexer(
            url=get_settings().qdrant_url,
            collection=get_settings().qdrant_collection,
            embedding=DeterministicEmbeddingService(),
        )
        memory_indexer.ensure_collection()
        assert memory_indexer.upsert_chunk(
            chunk_id=obsolete_chunk_id,
            embed_text="obsolete embed",
            payload={"content_item_id": str(content_item_id)},
        ) == obsolete_point_id
        memory_collection = _MEMORY_COLLECTIONS[
            (memory_indexer.url, memory_indexer.collection)
        ]
        assert obsolete_point_id in memory_collection.points
        session.add_all(
            [
                ContentChunk(
                    id=current_chunk_id,
                    content_item_id=content_item_id,
                    chunk_index=0,
                    char_start=expected_chunks[0].start_char,
                    char_end=expected_chunks[0].end_char,
                    display_text=expected_chunks[0].display_text,
                    embed_text=expected_chunks[0].embed_text,
                    token_count=expected_chunks[0].token_count,
                    chunk_metadata_json={
                        **expected_chunks[0].chunk_metadata_json,
                        **_current_vector_metadata(),
                    },
                    qdrant_point_id=None,
                    vector_backend="memory",
                    vector_point_id="current-vector-point",
                    embedded_at=utcnow(),
                    embed_status="success",
                    embed_error=None,
                ),
                ContentChunk(
                    id=obsolete_chunk_id,
                    content_item_id=content_item_id,
                    chunk_index=2,
                    char_start=9999,
                    char_end=10009,
                    display_text="obsolete display",
                    embed_text="obsolete embed",
                    token_count=2,
                    chunk_metadata_json={
                        "chunker_version": "obsolete-version",
                        "embed_text_hash": "1" * 64,
                        **_current_vector_metadata(),
                    },
                    qdrant_point_id=None,
                    vector_backend="memory",
                    vector_point_id=obsolete_point_id,
                    embedded_at=utcnow(),
                    embed_status="success",
                    embed_error=None,
                ),
            ]
        )
        await session.commit()

        result = await chunk_indexer_module.chunk_and_index_content_items(
            session=session,
            run_id=run_id,
            content_item_ids=[content_item_id],
        )
        await session.commit()

        chunks = (
            await session.scalars(
                select(ContentChunk).order_by(ContentChunk.chunk_index.asc())
            )
        ).all()

    chunks_by_index = {chunk.chunk_index: chunk for chunk in chunks}
    assert result.chunked_count == 1
    assert result.embedded_count == 1
    assert result.failed_count == 0
    assert set(chunks_by_index) == {0, 1, 2}
    assert chunks_by_index[0].id == current_chunk_id
    assert chunks_by_index[0].embed_status == "success"
    assert chunks_by_index[0].vector_point_id == "current-vector-point"
    assert chunks_by_index[1].display_text == expected_chunks[1].display_text
    assert chunks_by_index[1].embed_text == expected_chunks[1].embed_text
    assert chunks_by_index[1].embed_status == "success"
    assert chunks_by_index[1].vector_backend == "memory"
    assert chunks_by_index[1].vector_point_id == str(chunks_by_index[1].id)
    assert chunks_by_index[1].chunk_metadata_json["embed_text_hash"] == expected_chunks[
        1
    ].chunk_metadata_json["embed_text_hash"]
    assert chunks_by_index[2].id == obsolete_chunk_id
    assert chunks_by_index[2].embed_status == "obsolete"
    assert chunks_by_index[2].embed_error == "obsolete chunk"
    assert obsolete_point_id not in memory_collection.points
    assert chunks_by_index[2].vector_backend is None
    assert chunks_by_index[2].vector_point_id is None
    assert chunks_by_index[2].qdrant_point_id is None
    assert chunks_by_index[2].embedded_at is None


async def test_helper_commits_final_vector_status_and_events_before_return(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class DeleteFailingMemoryIndexer:
        backend_name = "memory"
        url = get_settings().qdrant_url
        collection = get_settings().qdrant_collection
        embedding = DeterministicEmbeddingService()

        def __init__(self) -> None:
            self._delegate = QdrantIndexer(
                url=self.url,
                collection=self.collection,
                embedding=self.embedding,
            )

        def ensure_collection(self) -> None:
            self._delegate.ensure_collection()

        def delete_chunk(self, *, point_id: str) -> None:
            del point_id
            raise RuntimeError("simulated vector delete outage")

        def upsert_chunk(
            self,
            *,
            chunk_id: uuid.UUID,
            embed_text: str,
            payload: dict[str, Any],
        ) -> str:
            return self._delegate.upsert_chunk(
                chunk_id=chunk_id,
                embed_text=embed_text,
                payload=payload,
            )

    monkeypatch.setattr(
        chunk_indexer_module, "_build_qdrant_indexer", DeleteFailingMemoryIndexer
    )

    cleaned_text = "fresh chunk text"
    async with db_session_module.AsyncSessionLocal() as session:
        run_id, content_item_id = await _create_indexable_content_item(
            session,
            item_type="article",
            cleaned_text=cleaned_text,
            title="Final status commit",
        )
        obsolete_chunk_id = uuid.uuid4()
        obsolete_point_id = str(obsolete_chunk_id)
        session.add(
            ContentChunk(
                id=obsolete_chunk_id,
                content_item_id=content_item_id,
                chunk_index=1,
                char_start=9999,
                char_end=10009,
                display_text="obsolete display",
                embed_text="obsolete embed",
                token_count=2,
                chunk_metadata_json={
                    "chunker_version": "obsolete-version",
                    "embed_text_hash": "1" * 64,
                    **_current_vector_metadata(),
                },
                qdrant_point_id=None,
                vector_backend="memory",
                vector_point_id=obsolete_point_id,
                embedded_at=utcnow(),
                embed_status="success",
                embed_error=None,
            )
        )
        await session.commit()

        result = await chunk_indexer_module.chunk_and_index_content_items(
            session=session,
            run_id=run_id,
            content_item_ids=[content_item_id],
        )

        async with db_session_module.AsyncSessionLocal() as verifier_session:
            committed_chunks = (
                await verifier_session.scalars(
                    select(ContentChunk).order_by(ContentChunk.chunk_index.asc())
                )
            ).all()
            committed_events = (
                await verifier_session.scalars(
                    select(CrawlRunEvent).where(
                        CrawlRunEvent.crawl_run_id == run_id,
                        CrawlRunEvent.event_type == "vector_delete_failed",
                    )
                )
            ).all()

    chunks_by_index = {chunk.chunk_index: chunk for chunk in committed_chunks}
    assert result.chunked_count == 1
    assert result.embedded_count == 1
    assert result.failed_count == 1
    assert chunks_by_index[0].embed_status == "success"
    assert chunks_by_index[0].vector_backend == "memory"
    assert chunks_by_index[0].vector_point_id == str(chunks_by_index[0].id)
    assert len(committed_events) == 1
    assert "simulated vector delete outage" in committed_events[0].message
    assert committed_events[0].related_content_item_id == content_item_id


async def test_obsolete_vector_delete_failure_records_event_and_counts_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class DeleteFailingIndexer:
        backend_name = "memory"
        url = "memory://worker-tests"
        collection = "content_chunks_worker_tests"
        embedding = DeterministicEmbeddingService()

        def ensure_collection(self) -> None:
            return None

        def delete_chunk(self, *, point_id: str) -> None:
            del point_id
            raise RuntimeError("simulated vector delete outage")

        def upsert_chunk(
            self,
            *,
            chunk_id: uuid.UUID,
            embed_text: str,
            payload: dict[str, Any],
        ) -> str:
            del chunk_id, embed_text, payload
            raise AssertionError("obsolete chunks must not be reindexed")

    def _successful_indexer() -> QdrantIndexer:
        return QdrantIndexer(
            url=get_settings().qdrant_url,
            collection=get_settings().qdrant_collection,
            embedding=DeterministicEmbeddingService(),
        )

    monkeypatch.setattr(chunk_indexer_module, "_build_qdrant_indexer", DeleteFailingIndexer)

    cleaned_text = "short body for a single chunk"
    async with db_session_module.AsyncSessionLocal() as session:
        run_id, content_item_id = await _create_indexable_content_item(
            session,
            item_type="article",
            cleaned_text=cleaned_text,
            title="Single chunk",
        )
        content_item = await session.get(ContentItem, content_item_id)
        assert content_item is not None
        expected_chunk = build_chunks(
            item_type=content_item.item_type,
            title=content_item.title,
            cleaned_text=content_item.cleaned_text,
            summary_text=content_item.summary_text,
            tags=content_item.tags,
        )[0]
        current_chunk_id = uuid.uuid4()
        obsolete_chunk_id = uuid.uuid4()
        obsolete_point_id = str(obsolete_chunk_id)
        memory_indexer = QdrantIndexer(
            url=get_settings().qdrant_url,
            collection=get_settings().qdrant_collection,
            embedding=DeterministicEmbeddingService(),
        )
        memory_indexer.ensure_collection()
        assert memory_indexer.upsert_chunk(
            chunk_id=obsolete_chunk_id,
            embed_text="obsolete embed",
            payload={"content_item_id": str(content_item_id)},
        ) == obsolete_point_id
        memory_collection = _MEMORY_COLLECTIONS[
            (memory_indexer.url, memory_indexer.collection)
        ]
        assert obsolete_point_id in memory_collection.points
        session.add_all(
            [
                ContentChunk(
                    id=current_chunk_id,
                    content_item_id=content_item_id,
                    chunk_index=0,
                    char_start=expected_chunk.start_char,
                    char_end=expected_chunk.end_char,
                    display_text=expected_chunk.display_text,
                    embed_text=expected_chunk.embed_text,
                    token_count=expected_chunk.token_count,
                    chunk_metadata_json={
                        **expected_chunk.chunk_metadata_json,
                        **_current_vector_metadata(),
                    },
                    qdrant_point_id=None,
                    vector_backend="memory",
                    vector_point_id="current-vector-point",
                    embedded_at=utcnow(),
                    embed_status="success",
                    embed_error=None,
                ),
                ContentChunk(
                    id=obsolete_chunk_id,
                    content_item_id=content_item_id,
                    chunk_index=1,
                    char_start=9999,
                    char_end=10009,
                    display_text="obsolete display",
                    embed_text="obsolete embed",
                    token_count=2,
                    chunk_metadata_json={
                        "chunker_version": "obsolete-version",
                        "embed_text_hash": "1" * 64,
                        **_current_vector_metadata(),
                    },
                    qdrant_point_id=None,
                    vector_backend="memory",
                    vector_point_id=obsolete_point_id,
                    embedded_at=utcnow(),
                    embed_status="success",
                    embed_error=None,
                ),
            ]
        )
        await session.commit()

        result = await chunk_indexer_module.chunk_and_index_content_items(
            session=session,
            run_id=run_id,
            content_item_ids=[content_item_id],
        )
        await session.commit()

        obsolete_chunk = await session.get(ContentChunk, obsolete_chunk_id)
        events = (
            await session.scalars(
                select(CrawlRunEvent).where(
                    CrawlRunEvent.crawl_run_id == run_id,
                    CrawlRunEvent.event_type == "vector_delete_failed",
                )
            )
        ).all()
        assert obsolete_chunk is not None
        failed_delete_embed_status = obsolete_chunk.embed_status
        failed_delete_embed_error = obsolete_chunk.embed_error
        failed_delete_vector_backend = obsolete_chunk.vector_backend
        failed_delete_vector_point_id = obsolete_chunk.vector_point_id
        failed_delete_qdrant_point_id = obsolete_chunk.qdrant_point_id
        failed_delete_embedded_at = obsolete_chunk.embedded_at
        failed_delete_point_still_indexed = obsolete_point_id in memory_collection.points

        monkeypatch.setattr(
            chunk_indexer_module, "_build_qdrant_indexer", _successful_indexer
        )
        retry_result = await chunk_indexer_module.chunk_and_index_content_items(
            session=session,
            run_id=run_id,
            content_item_ids=[content_item_id],
        )
        await session.commit()
        retried_obsolete_chunk = await session.get(ContentChunk, obsolete_chunk_id)
        retry_point_still_indexed = obsolete_point_id in memory_collection.points

    assert result.chunked_count == 0
    assert result.embedded_count == 0
    assert result.failed_count == 1
    assert failed_delete_embed_status == "obsolete"
    assert failed_delete_embed_error == "obsolete chunk"
    assert failed_delete_vector_backend == "memory"
    assert failed_delete_vector_point_id == obsolete_point_id
    assert failed_delete_qdrant_point_id is None
    assert failed_delete_embedded_at is not None
    assert failed_delete_point_still_indexed
    assert len(events) == 1
    assert "simulated vector delete outage" in events[0].message
    assert events[0].related_content_item_id == content_item_id

    assert retry_result.chunked_count == 0
    assert retry_result.embedded_count == 0
    assert retry_result.failed_count == 0
    assert retried_obsolete_chunk is not None
    assert retried_obsolete_chunk.embed_status == "obsolete"
    assert retried_obsolete_chunk.embed_error == "obsolete chunk"
    assert retried_obsolete_chunk.vector_backend is None
    assert retried_obsolete_chunk.vector_point_id is None
    assert retried_obsolete_chunk.qdrant_point_id is None
    assert retried_obsolete_chunk.embedded_at is None
    assert retried_obsolete_chunk.chunk_metadata_json["vector_cleanup_status"] == "deleted"
    assert not retry_point_still_indexed


def test_vector_upsert_failure_marks_run_partial_and_failed_chunks_are_retried(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FlakyIndexer:
        backend_name = "qdrant"

        def __init__(self, *, fail_upserts: bool) -> None:
            self.fail_upserts = fail_upserts

        def ensure_collection(self) -> None:
            return None

        def upsert_chunk(
            self,
            *,
            chunk_id: uuid.UUID,
            embed_text: str,
            payload: dict[str, Any],
        ) -> str:
            del embed_text, payload
            if self.fail_upserts:
                raise RuntimeError("simulated qdrant upsert outage")
            return str(chunk_id)

    indexer_build_count = 0

    def build_flaky_indexer() -> FlakyIndexer:
        nonlocal indexer_build_count
        indexer_build_count += 1
        return FlakyIndexer(fail_upserts=indexer_build_count == 1)

    monkeypatch.setattr(chunk_indexer_module, "_build_qdrant_indexer", build_flaky_indexer)
    client = TestClient(app)
    seed_url = "https://example.com/vector-retry"
    job = _create_worker_job(client, urls=[seed_url], seed_url=seed_url)
    first_run = _trigger_worker_job(client, job, seed_url=seed_url)

    first_result = run_once(run_limit=1)
    assert first_result.claimed == 1
    assert first_result.succeeded == 0
    assert first_result.partial == 1
    assert first_result.failed == 0

    first_detail = client.get(f"/runs/{first_run['id']}").json()
    assert first_detail["status"] == "partial"
    assert first_detail["fetched_count"] == 1
    assert first_detail["parsed_count"] == 1
    assert first_detail["extracted_count"] == 1
    assert first_detail["embedded_count"] == 0
    assert first_detail["error_count"] >= 1
    assert "chunk(s) failed during vector indexing" in first_detail["error_message"]

    failed_chunks = _fetch_db_rows(
        """
        select id, embed_status, embed_error, vector_backend, vector_point_id, embedded_at
        from content_chunks
        order by chunk_index asc
        """,
        {},
    )
    assert len(failed_chunks) >= 1
    assert {chunk["embed_status"] for chunk in failed_chunks} == {"failed"}
    assert all(chunk["embed_error"] for chunk in failed_chunks)
    assert all(
        "simulated qdrant upsert outage" in str(chunk["embed_error"])
        for chunk in failed_chunks
    )
    assert {chunk["vector_backend"] for chunk in failed_chunks} == {"qdrant"}
    assert all(chunk["vector_point_id"] is None for chunk in failed_chunks)
    assert all(chunk["embedded_at"] is None for chunk in failed_chunks)

    failure_events = _fetch_db_rows(
        """
        select event_type, message, related_content_item_id, counters_json
        from crawl_run_events
        where crawl_run_id = CAST(:run_id AS uuid)
          and event_type = 'vector_index_failed'
        order by created_at asc
        """,
        {"run_id": first_run["id"]},
    )
    assert len(failure_events) == len(failed_chunks)
    assert all(event["event_type"] == "vector_index_failed" for event in failure_events)
    assert all(
        "simulated qdrant upsert outage" in str(event["message"])
        for event in failure_events
    )

    second_run = _trigger_worker_job(client, job, seed_url=seed_url)
    second_result = run_once(run_limit=1)
    assert second_result.claimed == 1
    assert second_result.succeeded == 1
    assert second_result.partial == 0
    assert second_result.failed == 0

    second_detail = client.get(f"/runs/{second_run['id']}").json()
    assert second_detail["status"] == "success"
    assert second_detail["deduped_count"] == 1
    assert second_detail["chunked_count"] == 0
    assert second_detail["embedded_count"] == len(failed_chunks)

    retried_chunks = _fetch_db_rows(
        """
        select id, embed_status, embed_error, vector_backend, vector_point_id, embedded_at
        from content_chunks
        order by chunk_index asc
        """,
        {},
    )
    assert [chunk["id"] for chunk in retried_chunks] == [
        chunk["id"] for chunk in failed_chunks
    ]
    assert {chunk["embed_status"] for chunk in retried_chunks} == {"success"}
    assert all(chunk["embed_error"] is None for chunk in retried_chunks)
    assert {chunk["vector_backend"] for chunk in retried_chunks} == {"qdrant"}
    assert all(chunk["vector_point_id"] == str(chunk["id"]) for chunk in retried_chunks)
    assert all(chunk["embedded_at"] is not None for chunk in retried_chunks)


def test_deduped_retry_vector_failure_events_use_active_run_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class AlwaysFailingIndexer:
        backend_name = "qdrant"

        def __init__(self, *, build_number: int) -> None:
            self.build_number = build_number

        def ensure_collection(self) -> None:
            return None

        def upsert_chunk(
            self,
            *,
            chunk_id: uuid.UUID,
            embed_text: str,
            payload: dict[str, Any],
        ) -> str:
            del chunk_id, embed_text, payload
            raise RuntimeError(f"simulated qdrant outage build {self.build_number}")

    indexer_build_count = 0

    def build_failing_indexer() -> AlwaysFailingIndexer:
        nonlocal indexer_build_count
        indexer_build_count += 1
        return AlwaysFailingIndexer(build_number=indexer_build_count)

    monkeypatch.setattr(chunk_indexer_module, "_build_qdrant_indexer", build_failing_indexer)
    client = TestClient(app)
    seed_url = "https://example.com/vector-active-run"
    job = _create_worker_job(client, urls=[seed_url], seed_url=seed_url)
    first_run = _trigger_worker_job(client, job, seed_url=seed_url)

    first_result = run_once(run_limit=1)
    assert first_result.claimed == 1
    assert first_result.partial == 1

    failed_chunks = _fetch_db_rows(
        """
        select id, embed_status
        from content_chunks
        order by chunk_index asc
        """,
        {},
    )
    assert len(failed_chunks) >= 1
    assert {chunk["embed_status"] for chunk in failed_chunks} == {"failed"}

    first_failure_events = _fetch_db_rows(
        """
        select id, message
        from crawl_run_events
        where crawl_run_id = CAST(:run_id AS uuid)
          and event_type = 'vector_index_failed'
        order by created_at asc
        """,
        {"run_id": first_run["id"]},
    )
    assert len(first_failure_events) == len(failed_chunks)
    assert all(
        "simulated qdrant outage build 1" in str(event["message"])
        for event in first_failure_events
    )

    second_run = _trigger_worker_job(client, job, seed_url=seed_url)
    second_result = run_once(run_limit=1)
    assert second_result.claimed == 1
    assert second_result.partial == 1

    second_detail = client.get(f"/runs/{second_run['id']}").json()
    assert second_detail["status"] == "partial"
    assert second_detail["deduped_count"] == 1
    assert second_detail["chunked_count"] == 0
    assert second_detail["embedded_count"] == 0
    assert second_detail["error_count"] == len(failed_chunks)

    second_failure_events = _fetch_db_rows(
        """
        select id, message
        from crawl_run_events
        where crawl_run_id = CAST(:run_id AS uuid)
          and event_type = 'vector_index_failed'
        order by created_at asc
        """,
        {"run_id": second_run["id"]},
    )
    assert len(second_failure_events) == len(failed_chunks)
    assert all(
        "simulated qdrant outage build 2" in str(event["message"])
        for event in second_failure_events
    )

    misattributed_second_run_events = _fetch_db_rows(
        """
        select id
        from crawl_run_events
        where crawl_run_id = CAST(:run_id AS uuid)
          and event_type = 'vector_index_failed'
          and message like '%simulated qdrant outage build 2%'
        """,
        {"run_id": first_run["id"]},
    )
    assert misattributed_second_run_events == []


def test_qdrant_indexer_construction_failure_is_recoverable_vector_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail_build_indexer() -> object:
        raise RuntimeError("simulated qdrant construction outage")

    monkeypatch.setattr(chunk_indexer_module, "_build_qdrant_indexer", fail_build_indexer)
    client = TestClient(app)
    seed_url = "https://example.com/vector-construction"
    run = _queue_worker_run(client, urls=[seed_url], seed_url=seed_url)

    result = run_once(run_limit=1)
    assert result.claimed == 1
    assert result.succeeded == 0
    assert result.partial == 1
    assert result.failed == 0

    detail = client.get(f"/runs/{run['id']}").json()
    assert detail["status"] == "partial"
    assert detail["fetched_count"] == 1
    assert detail["parsed_count"] == 1
    assert detail["extracted_count"] == 1
    assert detail["embedded_count"] == 0
    assert "chunk(s) failed during vector indexing" in detail["error_message"]

    raw_pages = _fetch_db_rows(
        """
        select parse_status, parse_error
        from raw_pages
        where crawl_run_id = CAST(:run_id AS uuid)
        """,
        {"run_id": run["id"]},
    )
    assert raw_pages == [{"parse_status": "parsed", "parse_error": None}]

    content_items = _fetch_db_rows(
        """
        select id
        from content_items
        where crawl_run_id = CAST(:run_id AS uuid)
        """,
        {"run_id": run["id"]},
    )
    assert len(content_items) == 1

    failed_chunks = _fetch_db_rows(
        """
        select embed_status, embed_error, vector_backend, vector_point_id, embedded_at
        from content_chunks
        order by chunk_index asc
        """,
        {},
    )
    assert len(failed_chunks) >= 1
    assert detail["chunked_count"] == len(failed_chunks)
    assert detail["error_count"] == len(failed_chunks)
    assert {chunk["embed_status"] for chunk in failed_chunks} == {"failed"}
    assert all(
        "simulated qdrant construction outage" in str(chunk["embed_error"])
        for chunk in failed_chunks
    )
    assert {chunk["vector_backend"] for chunk in failed_chunks} == {"qdrant"}
    assert all(chunk["vector_point_id"] is None for chunk in failed_chunks)
    assert all(chunk["embedded_at"] is None for chunk in failed_chunks)

    failure_events = _fetch_db_rows(
        """
        select event_type, message
        from crawl_run_events
        where crawl_run_id = CAST(:run_id AS uuid)
          and event_type = 'vector_index_failed'
        """,
        {"run_id": run["id"]},
    )
    assert len(failure_events) == len(failed_chunks)
    assert all(
        "simulated qdrant construction outage" in str(event["message"])
        for event in failure_events
    )

    page_failed_events = _fetch_db_rows(
        """
        select id
        from crawl_run_events
        where crawl_run_id = CAST(:run_id AS uuid)
          and event_type = 'page_failed'
        """,
        {"run_id": run["id"]},
    )
    assert page_failed_events == []


async def test_chunk_creation_race_reloads_existing_chunks_and_indexes_them() -> None:
    async with db_session_module.AsyncSessionLocal() as session:
        source_site = SourceSite(
            name="Chunk Race Source",
            site_type="docs",
            base_url="https://example.com",
            allowed_domains=["example.com"],
            fetch_mode="manual",
            default_language="en",
            active=True,
            config_json={},
        )
        session.add(source_site)
        await session.flush()

        crawl_job = CrawlJob(
            source_site_id=source_site.id,
            name="Chunk race job",
            trigger_mode="manual",
            cron_expr=None,
            seed_config_json={"urls": ["https://example.com/chunk-race"]},
            parser_profile="official_site",
            max_pages=1,
            enabled=True,
            agent_policy_json={},
        )
        session.add(crawl_job)
        await session.flush()

        crawl_run = CrawlRun(
            source_site_id=source_site.id,
            crawl_job_id=crawl_job.id,
            trigger_type="manual",
            seed_url="https://example.com/chunk-race",
            status="running",
            config_snapshot_json={},
        )
        session.add(crawl_run)
        await session.flush()

        raw_page = RawPage(
            source_site_id=source_site.id,
            crawl_run_id=crawl_run.id,
            requested_url="https://example.com/chunk-race",
            final_url="https://example.com/chunk-race",
            http_status=200,
            content_type="text/html",
            response_headers_json={},
            raw_html="<html></html>",
            raw_text="Chunk race body",
            raw_json={},
            fetched_at=utcnow(),
            fetch_error=None,
            parser_profile="official_site",
            extraction_method="extraction_agent",
            extraction_confidence=0.9,
            parse_status="parsed",
            parse_error=None,
            body_hash="chunk-race-body-hash",
        )
        session.add(raw_page)
        await session.flush()

        content_item = ContentItem(
            source_site_id=source_site.id,
            raw_page_id=raw_page.id,
            crawl_run_id=crawl_run.id,
            author_id=None,
            parent_item_id=None,
            thread_root_id=None,
            item_type="article",
            title="Chunk race",
            canonical_url="https://example.com/chunk-race",
            source_url="https://example.com/chunk-race",
            published_at=None,
            language="en",
            raw_text="Chunk race body",
            cleaned_text="Chunk race body",
            summary_text="Chunk race summary",
            structured_by="extraction_agent",
            extraction_confidence=0.9,
            tags=["race"],
            metadata_json={},
            content_hash="chunk-race-content-hash",
            dedup_key="chunk-race-dedup-key",
            search_tsv=None,
        )
        session.add(content_item)
        await session.commit()

        run_id = crawl_run.id
        content_item_id = content_item.id
        winner_chunk_ids: list[uuid.UUID] = []
        conflict_inserted = False

        def insert_conflicting_chunks(sync_session: Session, *_args: object) -> None:
            nonlocal conflict_inserted
            if conflict_inserted:
                return

            pending_chunks = [
                chunk for chunk in sync_session.new if isinstance(chunk, ContentChunk)
            ]
            if not pending_chunks:
                return

            conflict_inserted = True
            engine = create_engine(os.environ["SYNC_DATABASE_URL"], pool_pre_ping=True)
            try:
                with engine.begin() as connection:
                    for pending_chunk in pending_chunks:
                        winner_chunk_id = uuid.uuid4()
                        winner_chunk_ids.append(winner_chunk_id)
                        connection.execute(
                            insert(ContentChunk).values(
                                id=winner_chunk_id,
                                content_item_id=pending_chunk.content_item_id,
                                chunk_index=pending_chunk.chunk_index,
                                char_start=pending_chunk.char_start,
                                char_end=pending_chunk.char_end,
                                display_text=(
                                    f"winner display {pending_chunk.chunk_index}"
                                ),
                                embed_text=f"winner embed {pending_chunk.chunk_index}",
                                token_count=2,
                                chunk_metadata_json={
                                    "winner": True,
                                    "chunk_index": pending_chunk.chunk_index,
                                },
                                qdrant_point_id=None,
                                vector_backend=None,
                                vector_point_id=None,
                                embedded_at=None,
                                embed_status="pending",
                                embed_error=None,
                            )
                        )
            finally:
                engine.dispose()

        event.listen(session.sync_session, "before_flush", insert_conflicting_chunks)
        try:
            result = await chunk_indexer_module.chunk_and_index_content_items(
                session=session,
                run_id=run_id,
                content_item_ids=[content_item_id],
            )
            await session.commit()
        finally:
            event.remove(session.sync_session, "before_flush", insert_conflicting_chunks)

        chunks = (
            await session.scalars(
                select(ContentChunk).order_by(ContentChunk.chunk_index.asc())
            )
        ).all()

    assert conflict_inserted is True
    assert result.chunked_count == 0
    assert result.embedded_count == len(winner_chunk_ids)
    assert result.failed_count == 0
    assert [chunk.id for chunk in chunks] == winner_chunk_ids
    assert {chunk.embed_status for chunk in chunks} == {"success"}
    assert {chunk.vector_backend for chunk in chunks} == {"memory"}
    assert all(chunk.vector_point_id == str(chunk.id) for chunk in chunks)
    assert all(chunk.qdrant_point_id is None for chunk in chunks)
    assert all(chunk.embedded_at is not None for chunk in chunks)


def test_unsupported_parser_profile_marks_run_failed_without_raising() -> None:
    client = TestClient(app)
    run = _queue_worker_run(client, parser_profile="unsupported_profile")

    result = run_once(run_limit=1)
    assert result.claimed == 1
    assert result.succeeded == 0
    assert result.partial == 0
    assert result.failed == 1

    detail = client.get(f"/runs/{run['id']}").json()
    assert detail["status"] == "failed"
    assert detail["discovered_count"] == 0
    assert detail["fetched_count"] == 0
    assert detail["extracted_count"] == 0
    assert detail["error_count"] == 1
    assert "Unsupported parser_profile: unsupported_profile" in detail["error_message"]

    events = client.get(f"/runs/{run['id']}/events").json()["items"]
    run_failed_events = [event for event in events if event["event_type"] == "run_failed"]
    assert len(run_failed_events) == 1
    assert "Unsupported parser_profile: unsupported_profile" in run_failed_events[0]["message"]


def test_unexpected_process_run_exception_marks_claimed_run_failed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fail_after_claim(run_id: uuid.UUID) -> runner_module.RunOutcome:
        raise RuntimeError(f"unexpected finalization failure for {run_id}")

    monkeypatch.setattr(runner_module, "_process_run", fail_after_claim)
    client = TestClient(app)
    run = _queue_worker_run(client)

    result = run_once(run_limit=1)
    assert result.claimed == 1
    assert result.succeeded == 0
    assert result.partial == 0
    assert result.failed == 1

    detail = client.get(f"/runs/{run['id']}").json()
    assert detail["status"] == "failed"
    assert detail["error_count"] == 1
    assert "unexpected finalization failure" in detail["error_message"]

    events = client.get(f"/runs/{run['id']}/events").json()["items"]
    run_failed_events = [event for event in events if event["event_type"] == "run_failed"]
    assert len(run_failed_events) == 1
    assert "unexpected finalization failure" in run_failed_events[0]["message"]


def test_zero_discovered_pages_marks_run_failed_with_explicit_message() -> None:
    client = TestClient(app)
    run = _queue_worker_run(client, urls=[], seed_url="")

    result = run_once(run_limit=1)
    assert result.claimed == 1
    assert result.succeeded == 0
    assert result.partial == 0
    assert result.failed == 1

    detail = client.get(f"/runs/{run['id']}").json()
    assert detail["status"] == "failed"
    assert detail["discovered_count"] == 0
    assert detail["fetched_count"] == 0
    assert detail["parsed_count"] == 0
    assert detail["extracted_count"] == 0
    assert detail["deduped_count"] == 0
    assert detail["error_count"] == 1
    assert detail["error_message"] == "No pages were discovered."

    events = client.get(f"/runs/{run['id']}/events").json()["items"]
    run_failed_events = [event for event in events if event["event_type"] == "run_failed"]
    assert len(run_failed_events) == 1
    assert run_failed_events[0]["message"] == "No pages were discovered."


def test_agent_fallback_response_records_fallback_used_status(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fallback_agent(
        request: ExtractionAgentRequest,
    ) -> tuple[ExtractionAgentResponse, int]:
        return (
            ExtractionAgentResponse(
                page_kind="other",
                items=[
                    ExtractionItem(
                        item_type="article",
                        external_item_id=None,
                        title=None,
                        author=None,
                        published_at=None,
                        body_text=request.raw_markdown or "fallback body",
                        summary_text=None,
                        tags=[],
                        parent_ref=None,
                        thread_root_ref=None,
                        metadata_json={"fallback": True},
                    )
                ],
                extraction_confidence=0.1,
                warnings=["agent_fallback_used"],
                trace_summary_json={"fallback": True, "provider": "fake-failing"},
            ),
            7,
        )

    monkeypatch.setattr(runner_module, "_call_extraction_agent", fallback_agent)
    client = TestClient(app)
    run = _queue_worker_run(client)

    result = run_once(run_limit=1)
    assert result.claimed == 1
    assert result.succeeded == 1

    agent_calls = _fetch_db_rows(
        "select status from agent_calls where related_crawl_run_id = CAST(:run_id AS uuid)",
        {"run_id": run["id"]},
    )
    assert [agent_call["status"] for agent_call in agent_calls] == ["fallback_used"]


def test_post_raw_page_failure_marks_raw_page_failed_and_links_event(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail_after_raw_page_persistence(
        request: ExtractionAgentRequest,
    ) -> tuple[ExtractionAgentResponse, int]:
        raise RuntimeError(f"agent exploded for raw page {request.raw_page_id}")

    monkeypatch.setattr(runner_module, "_call_extraction_agent", fail_after_raw_page_persistence)
    client = TestClient(app)
    run = _queue_worker_run(client)

    result = run_once(run_limit=1)
    assert result.claimed == 1
    assert result.succeeded == 0
    assert result.partial == 0
    assert result.failed == 1

    detail = client.get(f"/runs/{run['id']}").json()
    assert detail["status"] == "failed"
    assert detail["fetched_count"] == 1
    assert detail["parsed_count"] == 0
    assert detail["error_count"] == 1

    raw_pages = _fetch_db_rows(
        """
        select id, parse_status, parse_error
        from raw_pages
        where crawl_run_id = CAST(:run_id AS uuid)
        """,
        {"run_id": run["id"]},
    )
    assert len(raw_pages) == 1
    raw_page = raw_pages[0]
    assert raw_page["parse_status"] == "failed"
    assert "agent exploded for raw page" in str(raw_page["parse_error"])

    failure_events = _fetch_db_rows(
        """
        select related_raw_page_id, message
        from crawl_run_events
        where crawl_run_id = CAST(:run_id AS uuid)
          and event_type = 'page_failed'
        """,
        {"run_id": run["id"]},
    )
    assert len(failure_events) == 1
    assert str(failure_events[0]["related_raw_page_id"]) == str(raw_page["id"])
    assert "agent exploded for raw page" in str(failure_events[0]["message"])


def test_replaying_same_deterministic_page_reuses_existing_content_item() -> None:
    client = TestClient(app)
    seed_url = "https://example.com/replay"
    job = _create_worker_job(client, urls=[seed_url], seed_url=seed_url)
    first_run = _trigger_worker_job(client, job, seed_url=seed_url)
    second_run = _trigger_worker_job(client, job, seed_url=seed_url)

    first_result = run_once(run_limit=1)
    second_result = run_once(run_limit=1)
    assert first_result.claimed == 1
    assert first_result.succeeded == 1
    assert second_result.claimed == 1
    assert second_result.succeeded == 1

    first_detail = client.get(f"/runs/{first_run['id']}").json()
    second_detail = client.get(f"/runs/{second_run['id']}").json()
    assert first_detail["status"] == "success"
    assert first_detail["extracted_count"] == 1
    assert first_detail["deduped_count"] == 0
    assert second_detail["status"] == "success"
    assert second_detail["extracted_count"] == 0
    assert second_detail["deduped_count"] >= 1

    content_items = _fetch_db_rows(
        "select id, dedup_key from content_items order by created_at asc",
        {},
    )
    assert len(content_items) == 1

    raw_pages = _fetch_db_rows(
        """
        select parse_status
        from raw_pages
        order by fetched_at asc
        """,
        {},
    )
    assert [raw_page["parse_status"] for raw_page in raw_pages] == ["parsed", "parsed"]


async def test_normalizer_does_not_mutate_reused_item_relationships_on_replay() -> None:
    async with db_session_module.AsyncSessionLocal() as session:
        source_site = SourceSite(
            name="Replay Source",
            site_type="forum",
            base_url="https://example.com",
            allowed_domains=["example.com"],
            fetch_mode="manual",
            default_language="en",
            active=True,
            config_json={},
        )
        session.add(source_site)
        await session.flush()

        crawl_job = CrawlJob(
            source_site_id=source_site.id,
            name="Replay job",
            trigger_mode="manual",
            cron_expr=None,
            seed_config_json={"urls": ["https://example.com/replay-thread"]},
            parser_profile="forum_thread",
            max_pages=1,
            enabled=True,
            agent_policy_json={},
        )
        session.add(crawl_job)
        await session.flush()

        crawl_run = CrawlRun(
            source_site_id=source_site.id,
            crawl_job_id=crawl_job.id,
            trigger_type="manual",
            seed_url="https://example.com/replay-thread",
            status="running",
            config_snapshot_json={},
        )
        session.add(crawl_run)
        await session.flush()

        raw_page = RawPage(
            source_site_id=source_site.id,
            crawl_run_id=crawl_run.id,
            requested_url="https://example.com/replay-thread",
            final_url="https://example.com/replay-thread",
            http_status=200,
            content_type="text/html",
            response_headers_json={},
            raw_html="<html></html>",
            raw_text="stable replay body",
            raw_json={},
            fetched_at=utcnow(),
            fetch_error=None,
            parser_profile="forum_thread",
            extraction_method=None,
            extraction_confidence=None,
            parse_status="pending",
            parse_error=None,
            body_hash="stable-replay-body-hash",
        )
        session.add(raw_page)
        await session.flush()

        first_response = ExtractionAgentResponse(
            page_kind="forum_thread",
            items=[
                ExtractionItem(
                    item_type="comment",
                    external_item_id="stable-comment",
                    title=None,
                    author=None,
                    published_at=None,
                    body_text="stable replay body",
                    summary_text=None,
                    tags=[],
                    parent_ref=None,
                    thread_root_ref=None,
                    metadata_json={},
                )
            ],
            extraction_confidence=0.8,
            warnings=[],
            trace_summary_json={"provider": "test"},
        )
        first_normalization = await normalize_extraction_response(
            session,
            source_site=source_site,
            raw_page=raw_page,
            response=first_response,
        )
        await session.flush()

        reused_item_id = first_normalization.content_item_ids[0]
        reused_item = await session.get(ContentItem, reused_item_id)
        assert reused_item is not None
        original_parent_item_id = reused_item.parent_item_id
        original_thread_root_id = reused_item.thread_root_id
        assert original_parent_item_id is None
        assert original_thread_root_id is None

        second_response = ExtractionAgentResponse(
            page_kind="forum_thread",
            items=[
                ExtractionItem(
                    item_type="thread",
                    external_item_id="conflicting-thread",
                    title="Conflicting thread",
                    author=None,
                    published_at=None,
                    body_text="new conflicting root body",
                    summary_text=None,
                    tags=[],
                    parent_ref=None,
                    thread_root_ref=None,
                    metadata_json={},
                ),
                ExtractionItem(
                    item_type="comment",
                    external_item_id="stable-comment",
                    title=None,
                    author=None,
                    published_at=None,
                    body_text="stable replay body",
                    summary_text=None,
                    tags=[],
                    parent_ref="conflicting-thread",
                    thread_root_ref="conflicting-thread",
                    metadata_json={},
                ),
            ],
            extraction_confidence=0.8,
            warnings=[],
            trace_summary_json={"provider": "test"},
        )
        second_normalization = await normalize_extraction_response(
            session,
            source_site=source_site,
            raw_page=raw_page,
            response=second_response,
        )
        await session.flush()
        await session.refresh(reused_item)

    assert second_normalization.reused_item_ids == [reused_item_id]
    assert second_normalization.deduped_count == 1
    assert reused_item.parent_item_id == original_parent_item_id
    assert reused_item.thread_root_id == original_thread_root_id


async def test_normalizer_duplicate_occurrence_does_not_overwrite_creator_relationships() -> None:
    async with db_session_module.AsyncSessionLocal() as session:
        source_site = SourceSite(
            name="Duplicate Occurrence Source",
            site_type="forum",
            base_url="https://example.com",
            allowed_domains=["example.com"],
            fetch_mode="manual",
            default_language="en",
            active=True,
            config_json={},
        )
        session.add(source_site)
        await session.flush()

        crawl_job = CrawlJob(
            source_site_id=source_site.id,
            name="Duplicate occurrence job",
            trigger_mode="manual",
            cron_expr=None,
            seed_config_json={"urls": ["https://example.com/duplicate-thread"]},
            parser_profile="forum_thread",
            max_pages=1,
            enabled=True,
            agent_policy_json={},
        )
        session.add(crawl_job)
        await session.flush()

        crawl_run = CrawlRun(
            source_site_id=source_site.id,
            crawl_job_id=crawl_job.id,
            trigger_type="manual",
            seed_url="https://example.com/duplicate-thread",
            status="running",
            config_snapshot_json={},
        )
        session.add(crawl_run)
        await session.flush()

        raw_page = RawPage(
            source_site_id=source_site.id,
            crawl_run_id=crawl_run.id,
            requested_url="https://example.com/duplicate-thread",
            final_url="https://example.com/duplicate-thread",
            http_status=200,
            content_type="text/html",
            response_headers_json={},
            raw_html="<html></html>",
            raw_text="duplicate body",
            raw_json={},
            fetched_at=utcnow(),
            fetch_error=None,
            parser_profile="forum_thread",
            extraction_method=None,
            extraction_confidence=None,
            parse_status="pending",
            parse_error=None,
            body_hash="duplicate-body-hash",
        )
        session.add(raw_page)
        await session.flush()

        response = ExtractionAgentResponse(
            page_kind="forum_thread",
            items=[
                ExtractionItem(
                    item_type="thread",
                    external_item_id="thread-a",
                    title="Thread A",
                    author=None,
                    published_at=None,
                    body_text="thread a body",
                    summary_text=None,
                    tags=[],
                    parent_ref=None,
                    thread_root_ref=None,
                    metadata_json={},
                ),
                ExtractionItem(
                    item_type="comment",
                    external_item_id="duplicate-comment",
                    title=None,
                    author=None,
                    published_at=None,
                    body_text="duplicate comment body",
                    summary_text=None,
                    tags=[],
                    parent_ref="thread-a",
                    thread_root_ref="thread-a",
                    metadata_json={},
                ),
                ExtractionItem(
                    item_type="thread",
                    external_item_id="thread-b",
                    title="Thread B",
                    author=None,
                    published_at=None,
                    body_text="thread b body",
                    summary_text=None,
                    tags=[],
                    parent_ref=None,
                    thread_root_ref=None,
                    metadata_json={},
                ),
                ExtractionItem(
                    item_type="comment",
                    external_item_id="duplicate-comment",
                    title=None,
                    author=None,
                    published_at=None,
                    body_text="duplicate comment body",
                    summary_text=None,
                    tags=[],
                    parent_ref="thread-b",
                    thread_root_ref="thread-b",
                    metadata_json={},
                ),
            ],
            extraction_confidence=0.8,
            warnings=[],
            trace_summary_json={"provider": "test"},
        )

        normalization = await normalize_extraction_response(
            session,
            source_site=source_site,
            raw_page=raw_page,
            response=response,
        )
        await session.flush()

        first_thread_id = normalization.content_item_ids[0]
        first_comment_id = normalization.content_item_ids[1]
        second_thread_id = normalization.content_item_ids[2]
        second_comment_id = normalization.content_item_ids[3]
        duplicate_comment = await session.get(ContentItem, first_comment_id)
        assert duplicate_comment is not None
        same_dedup_key_comments = (
            await session.scalars(
                select(ContentItem).where(ContentItem.dedup_key == duplicate_comment.dedup_key)
            )
        ).all()

    assert first_comment_id == second_comment_id
    assert len(same_dedup_key_comments) == 1
    assert normalization.reused_item_ids == [first_comment_id]
    assert normalization.deduped_count == 1
    assert duplicate_comment.parent_item_id == first_thread_id
    assert duplicate_comment.thread_root_id == first_thread_id
    assert duplicate_comment.parent_item_id != second_thread_id
    assert duplicate_comment.thread_root_id != second_thread_id


async def test_normalizer_recovers_when_dedup_insert_loses_race() -> None:
    async with db_session_module.AsyncSessionLocal() as session:
        source_site = SourceSite(
            name="Race Source",
            site_type="forum",
            base_url="https://example.com",
            allowed_domains=["example.com"],
            fetch_mode="manual",
            default_language="en",
            active=True,
            config_json={},
        )
        session.add(source_site)
        await session.flush()

        crawl_job = CrawlJob(
            source_site_id=source_site.id,
            name="Race job",
            trigger_mode="manual",
            cron_expr=None,
            seed_config_json={"urls": ["https://example.com/race"]},
            parser_profile="forum_thread",
            max_pages=1,
            enabled=True,
            agent_policy_json={},
        )
        session.add(crawl_job)
        await session.flush()

        crawl_run = CrawlRun(
            source_site_id=source_site.id,
            crawl_job_id=crawl_job.id,
            trigger_type="manual",
            seed_url="https://example.com/race",
            status="running",
            config_snapshot_json={},
        )
        session.add(crawl_run)
        await session.flush()

        raw_page = RawPage(
            source_site_id=source_site.id,
            crawl_run_id=crawl_run.id,
            requested_url="https://example.com/race",
            final_url="https://example.com/race",
            http_status=200,
            content_type="text/html",
            response_headers_json={},
            raw_html="<html></html>",
            raw_text="race body",
            raw_json={},
            fetched_at=utcnow(),
            fetch_error=None,
            parser_profile="forum_thread",
            extraction_method=None,
            extraction_confidence=None,
            parse_status="pending",
            parse_error=None,
            body_hash="race-body-hash",
        )
        session.add(raw_page)
        await session.commit()

        conflict_item_id = uuid.uuid4()
        conflict_inserted = False

        def insert_conflicting_winner(sync_session: Session, *_args: object) -> None:
            nonlocal conflict_inserted
            if conflict_inserted:
                return
            pending_item = next(
                (
                    item
                    for item in sync_session.new
                    if isinstance(item, ContentItem)
                ),
                None,
            )
            if pending_item is None:
                return

            conflict_inserted = True
            engine = create_engine(os.environ["SYNC_DATABASE_URL"], pool_pre_ping=True)
            try:
                with engine.begin() as connection:
                    connection.execute(
                        insert(ContentItem).values(
                            id=conflict_item_id,
                            source_site_id=pending_item.source_site_id,
                            raw_page_id=pending_item.raw_page_id,
                            crawl_run_id=pending_item.crawl_run_id,
                            author_id=pending_item.author_id,
                            parent_item_id=None,
                            thread_root_id=None,
                            item_type=pending_item.item_type,
                            title=pending_item.title,
                            canonical_url=pending_item.canonical_url,
                            source_url=pending_item.source_url,
                            published_at=pending_item.published_at,
                            language=pending_item.language,
                            raw_text=pending_item.raw_text,
                            cleaned_text=pending_item.cleaned_text,
                            summary_text=pending_item.summary_text,
                            structured_by=pending_item.structured_by,
                            extraction_confidence=pending_item.extraction_confidence,
                            tags=pending_item.tags,
                            metadata_json=pending_item.metadata_json,
                            content_hash=pending_item.content_hash,
                            dedup_key=pending_item.dedup_key,
                            search_tsv=None,
                        )
                    )
            finally:
                engine.dispose()

        event.listen(session.sync_session, "before_flush", insert_conflicting_winner)
        try:
            response = ExtractionAgentResponse(
                page_kind="forum_thread",
                items=[
                    ExtractionItem(
                        item_type="comment",
                        external_item_id="race-comment-1",
                        title=None,
                        author=None,
                        published_at=None,
                        body_text="race body",
                        summary_text=None,
                        tags=[],
                        parent_ref=None,
                        thread_root_ref=None,
                        metadata_json={},
                    )
                ],
                extraction_confidence=0.8,
                warnings=[],
                trace_summary_json={"provider": "test"},
            )

            normalization = await normalize_extraction_response(
                session,
                source_site=source_site,
                raw_page=raw_page,
                response=response,
            )
            await session.commit()
        finally:
            event.remove(session.sync_session, "before_flush", insert_conflicting_winner)

        content_items = (await session.scalars(select(ContentItem))).all()

    assert conflict_inserted is True
    assert len(content_items) == 1
    assert content_items[0].id == conflict_item_id
    assert normalization.content_item_ids == [conflict_item_id]
    assert normalization.created_item_ids == []
    assert normalization.reused_item_ids == [conflict_item_id]
    assert normalization.deduped_count == 1


async def test_normalizer_distinguishes_same_body_items_by_type_and_external_id() -> None:
    async with db_session_module.AsyncSessionLocal() as session:
        source_site = SourceSite(
            name="Normalizer Source",
            site_type="forum",
            base_url="https://example.com",
            allowed_domains=["example.com"],
            fetch_mode="manual",
            default_language="en",
            active=True,
            config_json={},
        )
        session.add(source_site)
        await session.flush()

        crawl_job = CrawlJob(
            source_site_id=source_site.id,
            name="Normalizer job",
            trigger_mode="manual",
            cron_expr=None,
            seed_config_json={"urls": ["https://example.com/thread"]},
            parser_profile="forum_thread",
            max_pages=1,
            enabled=True,
            agent_policy_json={},
        )
        session.add(crawl_job)
        await session.flush()

        crawl_run = CrawlRun(
            source_site_id=source_site.id,
            crawl_job_id=crawl_job.id,
            trigger_type="manual",
            seed_url="https://example.com/thread",
            status="running",
            config_snapshot_json={},
        )
        session.add(crawl_run)
        await session.flush()

        raw_page = RawPage(
            source_site_id=source_site.id,
            crawl_run_id=crawl_run.id,
            requested_url="https://example.com/thread",
            final_url="https://example.com/thread",
            http_status=200,
            content_type="text/html",
            response_headers_json={},
            raw_html="<html></html>",
            raw_text="same body",
            raw_json={},
            fetched_at=utcnow(),
            fetch_error=None,
            parser_profile="forum_thread",
            extraction_method=None,
            extraction_confidence=None,
            parse_status="pending",
            parse_error=None,
            body_hash="body-hash",
        )
        session.add(raw_page)
        await session.flush()

        response = ExtractionAgentResponse(
            page_kind="forum_thread",
            items=[
                ExtractionItem(
                    item_type="article",
                    external_item_id="item-1",
                    title="Shared body article",
                    author=None,
                    published_at=None,
                    body_text="same body",
                    summary_text=None,
                    tags=[],
                    parent_ref=None,
                    thread_root_ref=None,
                    metadata_json={},
                ),
                ExtractionItem(
                    item_type="comment",
                    external_item_id="item-2",
                    title=None,
                    author=None,
                    published_at=None,
                    body_text="same body",
                    summary_text=None,
                    tags=[],
                    parent_ref=None,
                    thread_root_ref=None,
                    metadata_json={},
                ),
                ExtractionItem(
                    item_type="comment",
                    external_item_id="item-3",
                    title=None,
                    author=None,
                    published_at=None,
                    body_text="same body",
                    summary_text=None,
                    tags=[],
                    parent_ref=None,
                    thread_root_ref=None,
                    metadata_json={},
                ),
            ],
            extraction_confidence=0.8,
            warnings=[],
            trace_summary_json={"provider": "test"},
        )

        normalization = await normalize_extraction_response(
            session,
            source_site=source_site,
            raw_page=raw_page,
            response=response,
        )
        content_items = (
            await session.scalars(select(ContentItem).order_by(ContentItem.created_at.asc()))
        ).all()

    assert len(content_items) == 3
    assert len({item.id for item in content_items}) == 3
    assert len({item.dedup_key for item in content_items}) == 3
    assert set(normalization.content_item_ids) == {item.id for item in content_items}
    assert set(normalization.created_item_ids) == {item.id for item in content_items}
    assert normalization.reused_item_ids == []
    assert normalization.deduped_count == 0


def test_page_level_failure_for_one_page_marks_run_partial(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail_second_fetch(self: OfficialSiteAdapter, page: DiscoveredPage) -> FetchedPage:
        if page.requested_url == "https://example.com/b":
            raise RuntimeError("test fetch failure for second page")
        return original_fetch(self, page)

    original_fetch = OfficialSiteAdapter.fetch
    monkeypatch.setattr(OfficialSiteAdapter, "fetch", fail_second_fetch)
    client = TestClient(app)
    run = _queue_worker_run(
        client,
        urls=["https://example.com/a", "https://example.com/b"],
        max_pages=2,
    )

    result = run_once(run_limit=1)
    assert result.claimed == 1
    assert result.succeeded == 0
    assert result.partial == 1
    assert result.failed == 0

    detail = client.get(f"/runs/{run['id']}").json()
    assert detail["status"] == "partial"
    assert detail["discovered_count"] == 2
    assert detail["fetched_count"] == 1
    assert detail["parsed_count"] == 1
    assert detail["extracted_count"] == 1
    assert detail["deduped_count"] == 0
    assert detail["error_count"] == 1
    assert detail["error_message"] == "1 page(s) failed during worker processing."

    events = client.get(f"/runs/{run['id']}/events").json()["items"]
    page_failed_events = [event for event in events if event["event_type"] == "page_failed"]
    assert len(page_failed_events) == 1
    assert page_failed_events[0]["related_url"] == "https://example.com/b"
    assert "test fetch failure for second page" in page_failed_events[0]["message"]


def test_page_level_failure_for_all_pages_marks_run_failed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail_fetch(self: object, page: object) -> None:
        raise RuntimeError("test fetch failure")

    monkeypatch.setattr(OfficialSiteAdapter, "fetch", fail_fetch)
    client = TestClient(app)
    run = _queue_worker_run(
        client,
        urls=["https://example.com/a", "https://example.com/b"],
        max_pages=2,
    )

    result = run_once(run_limit=1)
    assert result.claimed == 1
    assert result.succeeded == 0
    assert result.partial == 0
    assert result.failed == 1

    detail = client.get(f"/runs/{run['id']}").json()
    assert detail["status"] == "failed"
    assert detail["discovered_count"] == 2
    assert detail["fetched_count"] == 0
    assert detail["parsed_count"] == 0
    assert detail["extracted_count"] == 0
    assert detail["error_count"] == 2
    assert detail["error_message"] == "2 page(s) failed during worker processing."

    events = client.get(f"/runs/{run['id']}/events").json()["items"]
    page_failed_events = [event for event in events if event["event_type"] == "page_failed"]
    assert len(page_failed_events) == 2
    assert {event["related_url"] for event in page_failed_events} == {
        "https://example.com/a",
        "https://example.com/b",
    }
