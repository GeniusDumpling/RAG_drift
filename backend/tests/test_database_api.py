from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, cast

import pytest
from app.main import app
from app.models.content import ContentChunk, ContentItem, RawPage
from app.models.control import CrawlJob, CrawlRun, CrawlRunEvent, SourceSite
from app.models.search import SearchQuery
from app.repositories import database as database_repo
from fastapi.testclient import TestClient
from httpx import Response
from sqlalchemy.ext.asyncio import AsyncSession

JsonObject = dict[str, Any]


def _json_object(response: Response) -> JsonObject:
    payload = response.json()
    assert isinstance(payload, dict)
    return cast(JsonObject, payload)


async def _seed_database_rows(session: AsyncSession) -> dict[str, str]:
    now = datetime(2026, 6, 22, 12, 0, tzinfo=UTC)
    source = SourceSite(
        name="DJI Forum",
        site_type="forum",
        base_url="https://bbs.dji.com",
        allowed_domains=["bbs.dji.com"],
        fetch_mode="http",
        default_language="zh",
        active=True,
        config_json={"series": "lito"},
    )
    session.add(source)
    await session.flush()

    job = CrawlJob(
        source_site_id=source.id,
        name="Lito demo crawl",
        trigger_mode="manual",
        cron_expr=None,
        seed_config_json={"thread_limit": 5},
        parser_profile="dji_bbs_lito",
        max_pages=5,
        enabled=True,
        agent_policy_json={"provider": "fake"},
    )
    session.add(job)
    await session.flush()

    run = CrawlRun(
        source_site_id=source.id,
        crawl_job_id=job.id,
        trigger_type="manual",
        execution_mode="agent_assisted",
        seed_url="https://bbs.dji.com/pro/list?series=lito",
        status="success",
        started_at=now,
        finished_at=now,
        discovered_count=1,
        fetched_count=1,
        parsed_count=1,
        extracted_count=1,
        deduped_count=0,
        chunked_count=1,
        embedded_count=1,
        error_count=0,
        config_snapshot_json={"demo": True},
        error_message=None,
        created_at=now,
    )
    session.add(run)
    await session.flush()

    event = CrawlRunEvent(
        crawl_run_id=run.id,
        stage="fetch",
        level="info",
        event_type="raw_page_saved",
        message="Saved raw DJI page",
        related_url="https://bbs.dji.com/thread/1",
        related_raw_page_id=None,
        related_content_item_id=None,
        counters_json={"fetched": 1},
        agent_trace_json={},
        created_at=now,
    )
    session.add(event)

    raw_page = RawPage(
        source_site_id=source.id,
        crawl_run_id=run.id,
        requested_url="https://bbs.dji.com/thread/1",
        final_url="https://bbs.dji.com/thread/1",
        http_status=200,
        content_type="application/json",
        response_headers_json={"content-type": "application/json"},
        raw_html=None,
        raw_text="Telemetry firmware report raw text",
        raw_json={"title": "Telemetry firmware report"},
        fetched_at=now,
        fetch_error=None,
        parser_profile="dji_bbs_lito",
        extraction_method="agent",
        extraction_confidence=0.9,
        parse_status="success",
        parse_error=None,
        body_hash="raw-hash",
        created_at=now,
    )
    session.add(raw_page)
    await session.flush()

    item = ContentItem(
        source_site_id=source.id,
        raw_page_id=raw_page.id,
        crawl_run_id=run.id,
        author_id=None,
        parent_item_id=None,
        thread_root_id=None,
        item_type="thread",
        title="Telemetry firmware report",
        canonical_url="https://bbs.dji.com/thread/1",
        source_url="https://bbs.dji.com/thread/1",
        published_at=now,
        language="zh",
        raw_text="Telemetry firmware report raw text",
        cleaned_text="Telemetry firmware report cleaned text",
        summary_text="Telemetry summary",
        structured_by="fake-agent",
        extraction_confidence=0.9,
        tags=["dji", "lito"],
        metadata_json={"kind": "thread"},
        content_hash="content-hash",
        dedup_key="dji-thread-1",
        search_tsv=None,
    )
    session.add(item)
    await session.flush()

    chunk = ContentChunk(
        content_item_id=item.id,
        chunk_index=0,
        char_start=0,
        char_end=42,
        display_text="Telemetry firmware report display text",
        embed_text="Telemetry firmware report embed text",
        token_count=5,
        chunk_metadata_json={"chunker_version": "v1", "vector_collection": "content_chunks_v1"},
        qdrant_point_id="point-1",
        vector_backend="qdrant",
        vector_point_id="point-1",
        embedded_at=now,
        embed_status="success",
        embed_error=None,
    )
    session.add(chunk)

    query = SearchQuery(
        raw_query="Telemetry firmware",
        filter_json={"tags": ["dji"]},
        mode="search",
        top_k=3,
        used_agent=True,
        optimized_query_text="Telemetry firmware",
        keyword_terms_json=["Telemetry", "firmware"],
        entity_hints_json=[],
        time_hints_json={},
        result_summary_json={"result_count": 1},
        query_trace_json={"retrieval": {"vector": {"attempted": True, "failed": False, "hit_count": 1}}},
        result_count=1,
        created_at=now,
    )
    session.add(query)
    await session.commit()

    return {
        "source_id": str(source.id),
        "job_id": str(job.id),
        "run_id": str(run.id),
        "event_id": str(event.id),
        "raw_page_id": str(raw_page.id),
        "content_item_id": str(item.id),
        "chunk_id": str(chunk.id),
        "query_id": str(query.id),
    }


@pytest.mark.asyncio
async def test_database_overview_returns_postgres_and_qdrant_reconciliation(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    ids = await _seed_database_rows(db_session)

    async def fake_qdrant_status() -> database_repo.QdrantCollectionStatus:
        return database_repo.QdrantCollectionStatus(
            status="ok",
            collection="content_chunks_v1",
            points_count=1,
            vector_size=384,
            distance="Cosine",
            error=None,
        )

    monkeypatch.setattr(database_repo, "fetch_qdrant_collection_status", fake_qdrant_status)

    response = TestClient(app).get("/database/overview")

    assert response.status_code == 200
    payload = _json_object(response)
    assert payload["postgres"]["status"] == "ok"
    assert payload["postgres"]["database_name"]
    table_counts = {item["table_name"]: item["count"] for item in payload["postgres"]["table_counts"]}
    assert table_counts["source_sites"] == 1
    assert table_counts["crawl_jobs"] == 1
    assert table_counts["crawl_runs"] == 1
    assert table_counts["raw_pages"] == 1
    assert table_counts["content_items"] == 1
    assert table_counts["content_chunks"] == 1
    assert table_counts["search_queries"] == 1
    assert payload["postgres"]["latest_run"]["id"] == ids["run_id"]
    assert payload["postgres"]["latest_search"]["raw_query"] == "Telemetry firmware"
    assert payload["postgres"]["chunk_vector_status"] == [
        {"embed_status": "success", "vector_backend": "qdrant", "count": 1}
    ]
    assert payload["qdrant"] == {
        "status": "ok",
        "collection": "content_chunks_v1",
        "points_count": 1,
        "vector_size": 384,
        "distance": "Cosine",
        "error": None,
    }
    assert payload["reconciliation"] == {
        "postgres_qdrant_chunk_count": 1,
        "qdrant_points_count": 1,
        "status": "matched",
    }


@pytest.mark.asyncio
async def test_database_overview_keeps_postgres_visible_when_qdrant_is_unavailable(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    await _seed_database_rows(db_session)

    async def fake_qdrant_status() -> database_repo.QdrantCollectionStatus:
        return database_repo.QdrantCollectionStatus(
            status="unavailable",
            collection="content_chunks_v1",
            points_count=None,
            vector_size=None,
            distance=None,
            error="Qdrant request failed",
        )

    monkeypatch.setattr(database_repo, "fetch_qdrant_collection_status", fake_qdrant_status)

    response = TestClient(app).get("/database/overview")

    assert response.status_code == 200
    payload = _json_object(response)
    assert payload["postgres"]["status"] == "ok"
    assert payload["qdrant"]["status"] == "unavailable"
    assert payload["qdrant"]["error"] == "Qdrant request failed"
    assert payload["reconciliation"]["status"] == "unknown"


@pytest.mark.asyncio
async def test_database_tables_and_rows_are_whitelisted_and_searchable(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    ids = await _seed_database_rows(db_session)

    response = TestClient(app).get("/database/tables")

    assert response.status_code == 200
    payload = _json_object(response)
    table_names = [item["table_name"] for item in payload["items"]]
    assert table_names == [
        "source_sites",
        "crawl_jobs",
        "crawl_runs",
        "crawl_run_events",
        "raw_pages",
        "content_items",
        "content_chunks",
        "search_queries",
    ]
    assert payload["total"] == 8

    rows_response = TestClient(app).get("/database/tables/content_items/rows?q=firmware")
    assert rows_response.status_code == 200
    rows_payload = _json_object(rows_response)
    assert rows_payload["total"] == 1
    row = rows_payload["items"][0]
    assert row["id"] == ids["content_item_id"]
    assert row["table_name"] == "content_items"
    assert row["preview"]["title"] == "Telemetry firmware report"
    assert row["detail"]["cleaned_text_preview"] == "Telemetry firmware report cleaned text"

    missing_response = TestClient(app).get("/database/tables/users/rows")
    assert missing_response.status_code == 404
    assert _json_object(missing_response) == {"detail": "Database table not found"}


@pytest.mark.asyncio
async def test_database_row_detail_returns_safe_related_summary(db_session: AsyncSession) -> None:
    ids = await _seed_database_rows(db_session)

    response = TestClient(app).get(f"/database/tables/content_chunks/rows/{ids['chunk_id']}")

    assert response.status_code == 200
    payload = _json_object(response)
    assert payload["id"] == ids["chunk_id"]
    assert payload["table_name"] == "content_chunks"
    assert payload["preview"]["embed_status"] == "success"
    assert payload["preview"]["vector_backend"] == "qdrant"
    assert payload["detail"]["embed_text_preview"] == "Telemetry firmware report embed text"
    assert payload["related"] == {
        "content_item_id": ids["content_item_id"],
        "content_title": "Telemetry firmware report",
        "qdrant_point_id": "point-1",
        "vector_backend": "qdrant",
    }


@pytest.mark.asyncio
async def test_database_rows_validate_limit_and_row_ids(db_session: AsyncSession) -> None:
    await _seed_database_rows(db_session)
    client = TestClient(app)

    too_large_response = client.get("/database/tables/content_items/rows?limit=101")
    assert too_large_response.status_code == 422

    missing_row_response = client.get(
        "/database/tables/content_items/rows/00000000-0000-0000-0000-000000000999"
    )
    assert missing_row_response.status_code == 404
    assert _json_object(missing_row_response) == {"detail": "Database row not found"}
