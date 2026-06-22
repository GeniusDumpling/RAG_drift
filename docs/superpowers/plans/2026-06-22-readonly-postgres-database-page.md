# Readonly PostgreSQL Database Page Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a read-only `Database / 数据库` page that visualizes PostgreSQL content, reconciles PostgreSQL chunk state with Qdrant point counts, and embeds a psql/Qdrant command handbook.

**Architecture:** Add a backend `database` API module with strict table whitelisting, read-only repository methods, and Qdrant collection status retrieval. Add frontend API types/client functions plus a `DatabasePage` that renders overview cards, a table browser, row details, and copyable command snippets. Keep the feature demonstrative and safe: no arbitrary SQL execution, no secrets, no writes.

**Tech Stack:** FastAPI, SQLAlchemy 2 async sessions, Pydantic v2, httpx, PostgreSQL, Qdrant HTTP API, React 18, TypeScript, Vite, Vitest, Testing Library.

---

## File Structure

Create or modify these files:

- Create `backend/app/schemas/database.py`: Pydantic response models for overview, Qdrant status, table metadata, row pages, and row details.
- Create `backend/app/repositories/database.py`: hard-coded table whitelist, read-only SQLAlchemy queries, safe serialization/truncation, PostgreSQL overview queries, Qdrant collection status fetch.
- Create `backend/app/api/database.py`: FastAPI routes for `/database/overview`, `/database/tables`, `/database/tables/{table_name}/rows`, and `/database/tables/{table_name}/rows/{row_id}`.
- Modify `backend/app/api/router.py`: include the database router.
- Create `backend/tests/test_database_api.py`: backend API contract tests for overview, whitelist, search, row detail, Qdrant unavailable behavior, and non-whitelisted access.
- Modify `frontend/src/api/types.ts`: add database overview/table/row TypeScript types.
- Modify `frontend/src/api/client.ts`: add database API client functions.
- Create `frontend/src/pages/DatabasePage.tsx`: read-only UI page with overview cards, table browser, row detail panel, and psql handbook.
- Modify `frontend/src/App.tsx`: add `Database` navigation item and render `DatabasePage`.
- Modify `frontend/src/styles.css`: add focused styles for status pills, database layout, table browser, command cards, copy buttons.
- Create `frontend/tests/database-page.test.tsx`: page-level tests with mocked fetch.
- Modify `frontend/tests/app-run-navigation.test.tsx` or create `frontend/tests/app-database-navigation.test.tsx`: navigation test proving the Database nav item renders the page.

---

## Task 1: Backend API Tests

**Files:**
- Create: `backend/tests/test_database_api.py`

- [ ] **Step 1: Write failing backend tests**

Create `backend/tests/test_database_api.py` with this content:

```python
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
```

- [ ] **Step 2: Run backend test to verify it fails**

Run:

```bash
cd /root/intelligence-rag
.venv/bin/python -m pytest backend/tests/test_database_api.py -q
```

Expected: FAIL with `ModuleNotFoundError` or `ImportError` for `app.repositories.database`, because the new backend API does not exist yet.

- [ ] **Step 3: Commit failing tests**

```bash
cd /root/intelligence-rag
git add backend/tests/test_database_api.py
git commit -m "test: specify readonly database API"
```

---

## Task 2: Backend Implementation

**Files:**
- Create: `backend/app/schemas/database.py`
- Create: `backend/app/repositories/database.py`
- Create: `backend/app/api/database.py`
- Modify: `backend/app/api/router.py`
- Test: `backend/tests/test_database_api.py`

- [ ] **Step 1: Implement database response schemas**

Create `backend/app/schemas/database.py` with this content:

```python
from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

JsonValue = dict[str, Any] | list[Any] | str | int | float | bool | None
ReconciliationStatus = Literal["matched", "mismatch", "unknown"]
QdrantStatus = Literal["ok", "unavailable"]


class DatabaseTableCount(BaseModel):
    table_name: str
    count: int


class ChunkVectorStatus(BaseModel):
    embed_status: str
    vector_backend: str | None
    count: int


class DatabaseRunSummary(BaseModel):
    id: str
    status: str
    created_at: datetime
    parsed_count: int
    chunked_count: int
    embedded_count: int
    error_count: int


class DatabaseSearchSummary(BaseModel):
    id: str
    raw_query: str
    mode: str
    result_count: int | None
    created_at: datetime


class PostgresOverview(BaseModel):
    status: Literal["ok"] = "ok"
    database_name: str
    server_version: str
    checked_at: datetime
    table_counts: list[DatabaseTableCount]
    latest_run: DatabaseRunSummary | None
    latest_search: DatabaseSearchSummary | None
    chunk_vector_status: list[ChunkVectorStatus]


class QdrantOverview(BaseModel):
    status: QdrantStatus
    collection: str
    points_count: int | None
    vector_size: int | None
    distance: str | None
    error: str | None = None


class DatabaseReconciliation(BaseModel):
    postgres_qdrant_chunk_count: int
    qdrant_points_count: int | None
    status: ReconciliationStatus


class DatabaseOverview(BaseModel):
    postgres: PostgresOverview
    qdrant: QdrantOverview
    reconciliation: DatabaseReconciliation


class DatabaseTableMeta(BaseModel):
    table_name: str
    label: str
    description: str
    default_sort: str
    preview_columns: list[str]
    searchable_columns: list[str]


class DatabaseTableList(BaseModel):
    items: list[DatabaseTableMeta]
    total: int


class DatabaseTableRow(BaseModel):
    id: str
    table_name: str
    preview: dict[str, JsonValue]
    detail: dict[str, JsonValue] = Field(default_factory=dict)
    related: dict[str, JsonValue] = Field(default_factory=dict)


class DatabaseTableRowsPage(BaseModel):
    items: list[DatabaseTableRow]
    total: int
    limit: int
    offset: int
```

- [ ] **Step 2: Implement read-only database repository**

Create `backend/app/repositories/database.py` with this content:

```python
from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal

import httpx
from sqlalchemy import Select, func, or_, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.models.content import ContentChunk, ContentItem, RawPage
from app.models.control import CrawlJob, CrawlRun, CrawlRunEvent, SourceSite
from app.models.search import SearchQuery
from app.schemas.database import (
    ChunkVectorStatus,
    DatabaseOverview,
    DatabaseReconciliation,
    DatabaseRunSummary,
    DatabaseSearchSummary,
    DatabaseTableCount,
    DatabaseTableMeta,
    DatabaseTableRow,
    DatabaseTableRowsPage,
    PostgresOverview,
    QdrantOverview,
)

ModelType = type[SourceSite] | type[CrawlJob] | type[CrawlRun] | type[CrawlRunEvent] | type[RawPage] | type[ContentItem] | type[ContentChunk] | type[SearchQuery]
JsonValue = dict[str, Any] | list[Any] | str | int | float | bool | None

MAX_PREVIEW_CHARS = 240
MAX_JSON_PREVIEW_CHARS = 1200


@dataclass(frozen=True)
class TableConfig:
    table_name: str
    label: str
    description: str
    model: ModelType
    preview_columns: tuple[str, ...]
    detail_columns: tuple[str, ...]
    searchable_columns: tuple[str, ...]
    default_sort_column: str
    default_sort_desc: bool = True


@dataclass(frozen=True)
class QdrantCollectionStatus:
    status: Literal["ok", "unavailable"]
    collection: str
    points_count: int | None
    vector_size: int | None
    distance: str | None
    error: str | None = None


TABLE_CONFIGS: tuple[TableConfig, ...] = (
    TableConfig(
        table_name="source_sites",
        label="数据源 source_sites",
        description="采集来源配置，是 source/job/run 的入口。",
        model=SourceSite,
        preview_columns=("id", "name", "site_type", "base_url", "active", "created_at"),
        detail_columns=("allowed_domains", "fetch_mode", "default_language", "config_json", "updated_at"),
        searchable_columns=("name", "base_url", "site_type"),
        default_sort_column="created_at",
    ),
    TableConfig(
        table_name="crawl_jobs",
        label="采集任务 crawl_jobs",
        description="手动或计划触发的采集任务配置。",
        model=CrawlJob,
        preview_columns=("id", "source_site_id", "name", "trigger_mode", "enabled", "created_at"),
        detail_columns=("cron_expr", "seed_config_json", "parser_profile", "max_pages", "agent_policy_json", "updated_at"),
        searchable_columns=("name", "parser_profile"),
        default_sort_column="created_at",
    ),
    TableConfig(
        table_name="crawl_runs",
        label="运行记录 crawl_runs",
        description="一次采集/处理 run 的状态、计数和错误摘要。",
        model=CrawlRun,
        preview_columns=("id", "status", "trigger_type", "parsed_count", "chunked_count", "embedded_count", "created_at"),
        detail_columns=("source_site_id", "crawl_job_id", "seed_url", "error_count", "error_message", "config_snapshot_json"),
        searchable_columns=("status", "seed_url", "error_message"),
        default_sort_column="created_at",
    ),
    TableConfig(
        table_name="crawl_run_events",
        label="运行事件 crawl_run_events",
        description="run 时间线事件和阶段性计数。",
        model=CrawlRunEvent,
        preview_columns=("id", "crawl_run_id", "stage", "level", "event_type", "created_at"),
        detail_columns=("message", "related_url", "related_raw_page_id", "related_content_item_id", "counters_json", "agent_trace_json"),
        searchable_columns=("stage", "level", "event_type", "message", "related_url"),
        default_sort_column="created_at",
    ),
    TableConfig(
        table_name="raw_pages",
        label="原始快照 raw_pages",
        description="抓取后、抽取前持久化的原始页面/API 响应。",
        model=RawPage,
        preview_columns=("id", "source_site_id", "crawl_run_id", "requested_url", "http_status", "parse_status", "fetched_at"),
        detail_columns=("final_url", "content_type", "raw_text", "raw_json", "fetch_error", "parse_error", "body_hash"),
        searchable_columns=("requested_url", "final_url", "raw_text", "fetch_error", "parse_error"),
        default_sort_column="fetched_at",
    ),
    TableConfig(
        table_name="content_items",
        label="内容项 content_items",
        description="归一化后的 thread/comment/article 等展示与引用单位。",
        model=ContentItem,
        preview_columns=("id", "item_type", "title", "canonical_url", "published_at", "created_at"),
        detail_columns=("source_site_id", "raw_page_id", "crawl_run_id", "language", "cleaned_text", "summary_text", "tags", "metadata_json"),
        searchable_columns=("title", "canonical_url", "cleaned_text", "summary_text"),
        default_sort_column="created_at",
    ),
    TableConfig(
        table_name="content_chunks",
        label="内容切片 content_chunks",
        description="RAG 检索单位，记录 embedding 和 Qdrant point 状态。",
        model=ContentChunk,
        preview_columns=("id", "content_item_id", "chunk_index", "embed_status", "vector_backend", "embedded_at"),
        detail_columns=("display_text", "embed_text", "token_count", "chunk_metadata_json", "qdrant_point_id", "vector_point_id", "embed_error"),
        searchable_columns=("display_text", "embed_text", "embed_status", "vector_backend", "embed_error"),
        default_sort_column="created_at",
    ),
    TableConfig(
        table_name="search_queries",
        label="检索记录 search_queries",
        description="用户 query、优化结果、检索 trace 和结果计数。",
        model=SearchQuery,
        preview_columns=("id", "raw_query", "mode", "top_k", "result_count", "created_at"),
        detail_columns=("filter_json", "used_agent", "optimized_query_text", "keyword_terms_json", "result_summary_json", "query_trace_json"),
        searchable_columns=("raw_query", "optimized_query_text", "mode"),
        default_sort_column="created_at",
    ),
)

TABLES_BY_NAME: dict[str, TableConfig] = {config.table_name: config for config in TABLE_CONFIGS}


def list_table_metadata() -> list[DatabaseTableMeta]:
    return [
        DatabaseTableMeta(
            table_name=config.table_name,
            label=config.label,
            description=config.description,
            default_sort=(
                f"{config.default_sort_column} {'desc' if config.default_sort_desc else 'asc'}"
            ),
            preview_columns=list(config.preview_columns),
            searchable_columns=list(config.searchable_columns),
        )
        for config in TABLE_CONFIGS
    ]


def get_table_config(table_name: str) -> TableConfig | None:
    return TABLES_BY_NAME.get(table_name)


async def get_database_overview(session: AsyncSession) -> DatabaseOverview:
    postgres = await _get_postgres_overview(session)
    qdrant_status = await fetch_qdrant_collection_status()
    postgres_qdrant_chunk_count = sum(
        item.count
        for item in postgres.chunk_vector_status
        if item.embed_status == "success" and item.vector_backend == "qdrant"
    )
    if qdrant_status.status != "ok" or qdrant_status.points_count is None:
        reconciliation_status: Literal["matched", "mismatch", "unknown"] = "unknown"
    elif postgres_qdrant_chunk_count == qdrant_status.points_count:
        reconciliation_status = "matched"
    else:
        reconciliation_status = "mismatch"

    return DatabaseOverview(
        postgres=postgres,
        qdrant=QdrantOverview(
            status=qdrant_status.status,
            collection=qdrant_status.collection,
            points_count=qdrant_status.points_count,
            vector_size=qdrant_status.vector_size,
            distance=qdrant_status.distance,
            error=qdrant_status.error,
        ),
        reconciliation=DatabaseReconciliation(
            postgres_qdrant_chunk_count=postgres_qdrant_chunk_count,
            qdrant_points_count=qdrant_status.points_count,
            status=reconciliation_status,
        ),
    )


async def _get_postgres_overview(session: AsyncSession) -> PostgresOverview:
    database_name = await session.scalar(text("select current_database()"))
    server_version = await session.scalar(text("select version()"))
    checked_at = await session.scalar(text("select now()"))
    if not isinstance(database_name, str):
        database_name = "unknown"
    if not isinstance(server_version, str):
        server_version = "unknown"
    if not isinstance(checked_at, datetime):
        checked_at = datetime.now().astimezone()

    table_counts: list[DatabaseTableCount] = []
    for config in TABLE_CONFIGS:
        count = await session.scalar(select(func.count()).select_from(config.model))
        table_counts.append(DatabaseTableCount(table_name=config.table_name, count=int(count or 0)))

    latest_run_model = await session.scalar(select(CrawlRun).order_by(CrawlRun.created_at.desc()).limit(1))
    latest_search_model = await session.scalar(
        select(SearchQuery).order_by(SearchQuery.created_at.desc()).limit(1)
    )
    chunk_rows = await session.execute(
        select(ContentChunk.embed_status, ContentChunk.vector_backend, func.count())
        .group_by(ContentChunk.embed_status, ContentChunk.vector_backend)
        .order_by(ContentChunk.embed_status.asc(), ContentChunk.vector_backend.asc().nullsfirst())
    )

    return PostgresOverview(
        database_name=database_name,
        server_version=server_version,
        checked_at=checked_at,
        table_counts=table_counts,
        latest_run=(
            DatabaseRunSummary(
                id=str(latest_run_model.id),
                status=latest_run_model.status,
                created_at=latest_run_model.created_at,
                parsed_count=latest_run_model.parsed_count,
                chunked_count=latest_run_model.chunked_count,
                embedded_count=latest_run_model.embedded_count,
                error_count=latest_run_model.error_count,
            )
            if latest_run_model is not None
            else None
        ),
        latest_search=(
            DatabaseSearchSummary(
                id=str(latest_search_model.id),
                raw_query=latest_search_model.raw_query,
                mode=latest_search_model.mode,
                result_count=latest_search_model.result_count,
                created_at=latest_search_model.created_at,
            )
            if latest_search_model is not None
            else None
        ),
        chunk_vector_status=[
            ChunkVectorStatus(embed_status=status, vector_backend=backend, count=int(count))
            for status, backend, count in chunk_rows.all()
        ],
    )


async def fetch_qdrant_collection_status() -> QdrantCollectionStatus:
    settings = get_settings()
    collection = settings.qdrant_collection
    if settings.qdrant_url == ":memory:" or settings.qdrant_url.startswith("memory://"):
        return QdrantCollectionStatus(
            status="unavailable",
            collection=collection,
            points_count=None,
            vector_size=None,
            distance=None,
            error="Qdrant URL uses process-local memory backend",
        )

    url = f"{settings.qdrant_url.rstrip('/')}/collections/{collection}"
    try:
        async with httpx.AsyncClient(timeout=3.0) as client:
            response = await client.get(url)
            response.raise_for_status()
            payload = response.json()
    except Exception:
        return QdrantCollectionStatus(
            status="unavailable",
            collection=collection,
            points_count=None,
            vector_size=None,
            distance=None,
            error="Qdrant request failed",
        )

    result = payload.get("result", {}) if isinstance(payload, dict) else {}
    config = result.get("config", {}) if isinstance(result, dict) else {}
    params = config.get("params", {}) if isinstance(config, dict) else {}
    vectors = params.get("vectors", {}) if isinstance(params, dict) else {}
    return QdrantCollectionStatus(
        status="ok",
        collection=collection,
        points_count=_safe_int(result.get("points_count")),
        vector_size=_safe_int(vectors.get("size")),
        distance=str(vectors.get("distance")) if vectors.get("distance") is not None else None,
        error=None,
    )


async def list_table_rows(
    session: AsyncSession,
    *,
    config: TableConfig,
    limit: int,
    offset: int,
    q: str | None,
) -> DatabaseTableRowsPage:
    statement: Select[tuple[Any]] = select(config.model)
    count_statement: Select[tuple[int]] = select(func.count()).select_from(config.model)
    filter_condition = _search_condition(config, q)
    if filter_condition is not None:
        statement = statement.where(filter_condition)
        count_statement = count_statement.where(filter_condition)

    sort_column = getattr(config.model, config.default_sort_column)
    order_expression = sort_column.desc() if config.default_sort_desc else sort_column.asc()
    rows = await session.scalars(statement.order_by(order_expression).limit(limit).offset(offset))
    total = await session.scalar(count_statement)
    return DatabaseTableRowsPage(
        items=[await _row_to_database_row(session, config, row) for row in rows.all()],
        total=int(total or 0),
        limit=limit,
        offset=offset,
    )


async def get_table_row(
    session: AsyncSession,
    *,
    config: TableConfig,
    row_id: uuid.UUID,
) -> DatabaseTableRow | None:
    row = await session.get(config.model, row_id)
    if row is None:
        return None
    return await _row_to_database_row(session, config, row)


def _search_condition(config: TableConfig, q: str | None) -> Any | None:
    if q is None or not q.strip() or not config.searchable_columns:
        return None
    pattern = f"%{_escape_like(q.strip())}%"
    return or_(
        *[
            getattr(config.model, column_name).ilike(pattern, escape="\\")
            for column_name in config.searchable_columns
        ]
    )


def _escape_like(value: str) -> str:
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


async def _row_to_database_row(
    session: AsyncSession, config: TableConfig, row: Any
) -> DatabaseTableRow:
    return DatabaseTableRow(
        id=str(row.id),
        table_name=config.table_name,
        preview={column: _serialize_value(getattr(row, column)) for column in config.preview_columns},
        detail={column: _serialize_detail(column, getattr(row, column)) for column in config.detail_columns},
        related=await _related_summary(session, config, row),
    )


async def _related_summary(
    session: AsyncSession, config: TableConfig, row: Any
) -> dict[str, JsonValue]:
    if config.table_name == "content_chunks":
        item = await session.get(ContentItem, row.content_item_id)
        return {
            "content_item_id": str(row.content_item_id),
            "content_title": item.title if item is not None else None,
            "qdrant_point_id": row.qdrant_point_id,
            "vector_backend": row.vector_backend,
        }
    if config.table_name == "content_items":
        return {
            "source_site_id": str(row.source_site_id),
            "raw_page_id": str(row.raw_page_id),
            "crawl_run_id": str(row.crawl_run_id),
        }
    if config.table_name == "raw_pages":
        return {"source_site_id": str(row.source_site_id), "crawl_run_id": str(row.crawl_run_id)}
    return {}


def _serialize_detail(column_name: str, value: Any) -> JsonValue:
    if isinstance(value, str) and len(value) > MAX_PREVIEW_CHARS:
        return {f"{column_name}_preview": value[:MAX_PREVIEW_CHARS], "truncated": True}
    if isinstance(value, (dict, list)):
        text_value = str(value)
        if len(text_value) > MAX_JSON_PREVIEW_CHARS:
            return {"preview": text_value[:MAX_JSON_PREVIEW_CHARS], "truncated": True}
    serialized = _serialize_value(value)
    if isinstance(value, str):
        return {f"{column_name}_preview": serialized}
    return serialized


def _serialize_value(value: Any) -> JsonValue:
    if isinstance(value, uuid.UUID):
        return str(value)
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, str):
        if len(value) <= MAX_PREVIEW_CHARS:
            return value
        return value[:MAX_PREVIEW_CHARS]
    if isinstance(value, (dict, list, int, float, bool)) or value is None:
        return value
    return str(value)


def _safe_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    return None
```

- [ ] **Step 3: Implement FastAPI routes**

Create `backend/app/api/database.py` with this content:

```python
from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_session
from app.repositories.database import (
    get_database_overview,
    get_table_config,
    get_table_row,
    list_table_metadata,
    list_table_rows,
)
from app.schemas.database import (
    DatabaseOverview,
    DatabaseTableList,
    DatabaseTableRow,
    DatabaseTableRowsPage,
)

router = APIRouter(prefix="/database", tags=["database"])
SessionDep = Annotated[AsyncSession, Depends(get_session)]
LimitQuery = Annotated[int, Query(ge=1, le=100)]
OffsetQuery = Annotated[int, Query(ge=0)]


@router.get("/overview", response_model=DatabaseOverview)
async def database_overview(session: SessionDep) -> DatabaseOverview:
    return await get_database_overview(session)


@router.get("/tables", response_model=DatabaseTableList)
async def database_tables() -> DatabaseTableList:
    items = list_table_metadata()
    return DatabaseTableList(items=items, total=len(items))


@router.get("/tables/{table_name}/rows", response_model=DatabaseTableRowsPage)
async def database_table_rows(
    table_name: str,
    session: SessionDep,
    q: str | None = None,
    limit: LimitQuery = 50,
    offset: OffsetQuery = 0,
) -> DatabaseTableRowsPage:
    config = get_table_config(table_name)
    if config is None:
        raise HTTPException(status_code=404, detail="Database table not found")
    return await list_table_rows(session, config=config, limit=limit, offset=offset, q=q)


@router.get("/tables/{table_name}/rows/{row_id}", response_model=DatabaseTableRow)
async def database_table_row(
    table_name: str,
    row_id: uuid.UUID,
    session: SessionDep,
) -> DatabaseTableRow:
    config = get_table_config(table_name)
    if config is None:
        raise HTTPException(status_code=404, detail="Database table not found")
    row = await get_table_row(session, config=config, row_id=row_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Database row not found")
    return row
```

- [ ] **Step 4: Register database router**

Modify `backend/app/api/router.py` from:

```python
from fastapi import APIRouter

from app.api import contents, health, search, sources

api_router = APIRouter()
api_router.include_router(health.router)
api_router.include_router(sources.router)
api_router.include_router(contents.router)
api_router.include_router(search.router)
```

to:

```python
from fastapi import APIRouter

from app.api import contents, database, health, search, sources

api_router = APIRouter()
api_router.include_router(health.router)
api_router.include_router(sources.router)
api_router.include_router(contents.router)
api_router.include_router(search.router)
api_router.include_router(database.router)
```

- [ ] **Step 5: Run backend tests**

Run:

```bash
cd /root/intelligence-rag
.venv/bin/python -m pytest backend/tests/test_database_api.py -q
```

Expected: PASS for all tests in `backend/tests/test_database_api.py`.

- [ ] **Step 6: Run focused backend regression tests**

Run:

```bash
cd /root/intelligence-rag
.venv/bin/python -m pytest backend/tests/test_health.py backend/tests/test_control_api.py backend/tests/test_content_api.py -q
```

Expected: PASS.

- [ ] **Step 7: Commit backend implementation**

```bash
cd /root/intelligence-rag
git add backend/app/schemas/database.py backend/app/repositories/database.py backend/app/api/database.py backend/app/api/router.py backend/tests/test_database_api.py
git commit -m "feat: add readonly database API"
```

---

## Task 3: Frontend API Types and Tests

**Files:**
- Modify: `frontend/src/api/types.ts`
- Modify: `frontend/src/api/client.ts`
- Create: `frontend/tests/database-page.test.tsx`
- Create: `frontend/tests/app-database-navigation.test.tsx`

- [ ] **Step 1: Write failing frontend page tests**

Create `frontend/tests/database-page.test.tsx` with this content:

```tsx
import '@testing-library/jest-dom/vitest';
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { DatabasePage } from '../src/pages/DatabasePage';

function jsonResponse(body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status: 200,
    headers: { 'Content-Type': 'application/json' },
  });
}

const overview = {
  postgres: {
    status: 'ok',
    database_name: 'intelligence_rag',
    server_version: 'PostgreSQL 16',
    checked_at: '2026-06-22T12:00:00Z',
    table_counts: [
      { table_name: 'source_sites', count: 3 },
      { table_name: 'content_chunks', count: 67 },
    ],
    latest_run: {
      id: 'run-1',
      status: 'success',
      created_at: '2026-06-22T12:00:00Z',
      parsed_count: 61,
      chunked_count: 0,
      embedded_count: 67,
      error_count: 0,
    },
    latest_search: {
      id: 'query-1',
      raw_query: '报送 固件',
      mode: 'search',
      result_count: 3,
      created_at: '2026-06-22T12:01:00Z',
    },
    chunk_vector_status: [{ embed_status: 'success', vector_backend: 'qdrant', count: 67 }],
  },
  qdrant: {
    status: 'ok',
    collection: 'content_chunks_v1',
    points_count: 67,
    vector_size: 384,
    distance: 'Cosine',
    error: null,
  },
  reconciliation: {
    postgres_qdrant_chunk_count: 67,
    qdrant_points_count: 67,
    status: 'matched',
  },
};

const tables = {
  items: [
    {
      table_name: 'content_items',
      label: '内容项 content_items',
      description: '归一化后的 thread/comment/article 等展示与引用单位。',
      default_sort: 'created_at desc',
      preview_columns: ['id', 'item_type', 'title'],
      searchable_columns: ['title', 'canonical_url', 'cleaned_text'],
    },
    {
      table_name: 'content_chunks',
      label: '内容切片 content_chunks',
      description: 'RAG 检索单位，记录 embedding 和 Qdrant point 状态。',
      default_sort: 'created_at desc',
      preview_columns: ['id', 'embed_status', 'vector_backend'],
      searchable_columns: ['display_text', 'embed_text'],
    },
  ],
  total: 2,
};

const itemRows = {
  items: [
    {
      id: 'item-1',
      table_name: 'content_items',
      preview: { id: 'item-1', item_type: 'thread', title: 'Telemetry firmware report' },
      detail: { cleaned_text: { cleaned_text_preview: 'Telemetry firmware report cleaned text' } },
      related: { raw_page_id: 'raw-1', crawl_run_id: 'run-1' },
    },
  ],
  total: 1,
  limit: 50,
  offset: 0,
};

const chunkRows = {
  items: [
    {
      id: 'chunk-1',
      table_name: 'content_chunks',
      preview: { id: 'chunk-1', embed_status: 'success', vector_backend: 'qdrant' },
      detail: { embed_text: { embed_text_preview: 'Telemetry firmware report embed text' } },
      related: { content_item_id: 'item-1', content_title: 'Telemetry firmware report' },
    },
  ],
  total: 1,
  limit: 50,
  offset: 0,
};

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe('DatabasePage', () => {
  it('renders PostgreSQL/Qdrant overview, table rows, row detail, and psql handbook', async () => {
    const fetchMock = vi.fn((input: RequestInfo | URL) => {
      const url = String(input);
      if (url.endsWith('/database/overview')) {
        return Promise.resolve(jsonResponse(overview));
      }
      if (url.endsWith('/database/tables')) {
        return Promise.resolve(jsonResponse(tables));
      }
      if (url.endsWith('/database/tables/content_items/rows?limit=50&offset=0')) {
        return Promise.resolve(jsonResponse(itemRows));
      }
      if (url.endsWith('/database/tables/content_chunks/rows?limit=50&offset=0')) {
        return Promise.resolve(jsonResponse(chunkRows));
      }
      return Promise.reject(new Error(`Unexpected URL: ${url}`));
    });
    vi.stubGlobal('fetch', fetchMock);

    render(<DatabasePage />);

    expect(await screen.findByRole('heading', { name: 'PostgreSQL 数据库展示 Database' })).toBeInTheDocument();
    expect(screen.getByText('intelligence_rag')).toBeInTheDocument();
    expect(screen.getByText('content_chunks_v1')).toBeInTheDocument();
    expect(screen.getByText('matched')).toBeInTheDocument();
    expect(screen.getByText('success / qdrant')).toBeInTheDocument();
    expect(await screen.findByText('Telemetry firmware report')).toBeInTheDocument();

    const handbook = screen.getByRole('region', { name: 'psql 使用手册' });
    expect(within(handbook).getByText(/psql -h 127\.0\.0\.1 -p 54329/)).toBeInTheDocument();
    expect(within(handbook).getByText(/select embed_status, vector_backend/)).toBeInTheDocument();

    fireEvent.change(screen.getByLabelText('选择表'), { target: { value: 'content_chunks' } });
    expect(await screen.findByText('chunk-1')).toBeInTheDocument();
    expect(screen.getByText('Telemetry firmware report embed text')).toBeInTheDocument();
  });

  it('passes keyword search to the selected table rows endpoint', async () => {
    const fetchMock = vi.fn((input: RequestInfo | URL) => {
      const url = String(input);
      if (url.endsWith('/database/overview')) {
        return Promise.resolve(jsonResponse(overview));
      }
      if (url.endsWith('/database/tables')) {
        return Promise.resolve(jsonResponse(tables));
      }
      if (url.endsWith('/database/tables/content_items/rows?limit=50&offset=0')) {
        return Promise.resolve(jsonResponse(itemRows));
      }
      if (url.endsWith('/database/tables/content_items/rows?limit=50&offset=0&q=firmware')) {
        return Promise.resolve(jsonResponse(itemRows));
      }
      return Promise.reject(new Error(`Unexpected URL: ${url}`));
    });
    vi.stubGlobal('fetch', fetchMock);

    render(<DatabasePage />);

    await screen.findByText('Telemetry firmware report');
    fireEvent.change(screen.getByLabelText('表内搜索'), { target: { value: 'firmware' } });
    fireEvent.click(screen.getByRole('button', { name: '搜索表数据' }));

    await waitFor(() => {
      expect(fetchMock).toHaveBeenCalledWith(
        expect.stringContaining('/database/tables/content_items/rows?limit=50&offset=0&q=firmware'),
        undefined,
      );
    });
  });

  it('renders explicit error state when database APIs fail', async () => {
    vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new Error('backend offline')));

    render(<DatabasePage />);

    expect(await screen.findByRole('alert')).toHaveTextContent('无法加载数据库展示数据: backend offline');
    expect(screen.getByText('数据库展示暂不可用。')).toBeInTheDocument();
  });
});
```

- [ ] **Step 2: Write failing app navigation test**

Create `frontend/tests/app-database-navigation.test.tsx` with this content:

```tsx
import '@testing-library/jest-dom/vitest';
import { fireEvent, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

import App from '../src/App';

function jsonResponse(body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status: 200,
    headers: { 'Content-Type': 'application/json' },
  });
}

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe('App database navigation', () => {
  it('opens the read-only Database page from the main navigation', async () => {
    const fetchMock = vi.fn((input: RequestInfo | URL) => {
      const url = String(input);
      if (url.endsWith('/sources?limit=50&offset=0')) {
        return Promise.resolve(jsonResponse({ items: [], total: 0, limit: 50, offset: 0 }));
      }
      if (url.endsWith('/jobs?limit=50&offset=0')) {
        return Promise.resolve(jsonResponse({ items: [], total: 0, limit: 50, offset: 0 }));
      }
      if (url.endsWith('/runs?limit=50&offset=0')) {
        return Promise.resolve(jsonResponse({ items: [], total: 0, limit: 50, offset: 0 }));
      }
      if (url.endsWith('/contents?limit=10&offset=0')) {
        return Promise.resolve(jsonResponse({ items: [], total: 0, limit: 10, offset: 0 }));
      }
      if (url.endsWith('/database/overview')) {
        return Promise.resolve(
          jsonResponse({
            postgres: {
              status: 'ok',
              database_name: 'intelligence_rag',
              server_version: 'PostgreSQL 16',
              checked_at: '2026-06-22T12:00:00Z',
              table_counts: [],
              latest_run: null,
              latest_search: null,
              chunk_vector_status: [],
            },
            qdrant: {
              status: 'unavailable',
              collection: 'content_chunks_v1',
              points_count: null,
              vector_size: null,
              distance: null,
              error: 'Qdrant request failed',
            },
            reconciliation: {
              postgres_qdrant_chunk_count: 0,
              qdrant_points_count: null,
              status: 'unknown',
            },
          }),
        );
      }
      if (url.endsWith('/database/tables')) {
        return Promise.resolve(jsonResponse({ items: [], total: 0 }));
      }
      return Promise.reject(new Error(`Unexpected URL: ${url}`));
    });
    vi.stubGlobal('fetch', fetchMock);

    render(<App />);

    fireEvent.click(screen.getByRole('button', { name: '数据库 Database' }));

    expect(await screen.findByRole('heading', { name: 'PostgreSQL 数据库展示 Database' })).toBeInTheDocument();
    expect(screen.getByText('只读展示，不支持 SQL 执行或写入操作。')).toBeInTheDocument();
  });
});
```

- [ ] **Step 3: Run frontend tests to verify they fail**

Run:

```bash
cd /root/intelligence-rag/frontend
npm test -- database-page.test.tsx app-database-navigation.test.tsx
```

Expected: FAIL because `DatabasePage`, database API types, and database navigation do not exist yet.

- [ ] **Step 4: Commit failing frontend tests**

```bash
cd /root/intelligence-rag
git add frontend/tests/database-page.test.tsx frontend/tests/app-database-navigation.test.tsx
git commit -m "test: specify database frontend page"
```

---

## Task 4: Frontend Implementation

**Files:**
- Modify: `frontend/src/api/types.ts`
- Modify: `frontend/src/api/client.ts`
- Create: `frontend/src/pages/DatabasePage.tsx`
- Modify: `frontend/src/App.tsx`
- Modify: `frontend/src/styles.css`
- Test: `frontend/tests/database-page.test.tsx`
- Test: `frontend/tests/app-database-navigation.test.tsx`

- [ ] **Step 1: Add database API TypeScript types**

Append these types to `frontend/src/api/types.ts`:

```ts
export type DatabaseTableCount = {
  table_name: string;
  count: number;
};

export type ChunkVectorStatus = {
  embed_status: string;
  vector_backend: string | null;
  count: number;
};

export type DatabaseRunSummary = {
  id: UUID;
  status: string;
  created_at: ISODateTime;
  parsed_count: number;
  chunked_count: number;
  embedded_count: number;
  error_count: number;
};

export type DatabaseSearchSummary = {
  id: UUID;
  raw_query: string;
  mode: string;
  result_count: number | null;
  created_at: ISODateTime;
};

export type PostgresOverview = {
  status: 'ok';
  database_name: string;
  server_version: string;
  checked_at: ISODateTime;
  table_counts: DatabaseTableCount[];
  latest_run: DatabaseRunSummary | null;
  latest_search: DatabaseSearchSummary | null;
  chunk_vector_status: ChunkVectorStatus[];
};

export type QdrantOverview = {
  status: 'ok' | 'unavailable';
  collection: string;
  points_count: number | null;
  vector_size: number | null;
  distance: string | null;
  error: string | null;
};

export type DatabaseReconciliation = {
  postgres_qdrant_chunk_count: number;
  qdrant_points_count: number | null;
  status: 'matched' | 'mismatch' | 'unknown';
};

export type DatabaseOverview = {
  postgres: PostgresOverview;
  qdrant: QdrantOverview;
  reconciliation: DatabaseReconciliation;
};

export type DatabaseTableMeta = {
  table_name: string;
  label: string;
  description: string;
  default_sort: string;
  preview_columns: string[];
  searchable_columns: string[];
};

export type DatabaseTableList = {
  items: DatabaseTableMeta[];
  total: number;
};

export type DatabaseJsonValue = JsonObject | unknown[] | string | number | boolean | null;

export type DatabaseTableRow = {
  id: UUID;
  table_name: string;
  preview: Record<string, DatabaseJsonValue>;
  detail: Record<string, DatabaseJsonValue>;
  related: Record<string, DatabaseJsonValue>;
};

export type DatabaseTableRowsPage = {
  items: DatabaseTableRow[];
  total: number;
  limit: number;
  offset: number;
};
```

- [ ] **Step 2: Add database API client functions**

Modify the import list at the top of `frontend/src/api/client.ts` to include:

```ts
  DatabaseOverview,
  DatabaseTableList,
  DatabaseTableRow,
  DatabaseTableRowsPage,
```

Then add these functions after `getContent`:

```ts
export function getDatabaseOverview(): Promise<DatabaseOverview> {
  return request<DatabaseOverview>('/database/overview');
}

export function listDatabaseTables(): Promise<DatabaseTableList> {
  return request<DatabaseTableList>('/database/tables');
}

export function listDatabaseRows(
  tableName: string,
  params: { q?: string; limit?: number; offset?: number } = {},
): Promise<DatabaseTableRowsPage> {
  return request<DatabaseTableRowsPage>(
    buildPath(`/database/tables/${tableName}/rows`, {
      limit: params.limit ?? 50,
      offset: params.offset ?? 0,
      q: params.q,
    }),
  );
}

export function getDatabaseRow(tableName: string, rowId: UUID): Promise<DatabaseTableRow> {
  return request<DatabaseTableRow>(`/database/tables/${tableName}/rows/${rowId}`);
}
```

- [ ] **Step 3: Create DatabasePage component**

Create `frontend/src/pages/DatabasePage.tsx` with this content:

```tsx
import { FormEvent, useEffect, useMemo, useState } from 'react';

import { getDatabaseOverview, listDatabaseRows, listDatabaseTables } from '../api/client';
import type { DatabaseJsonValue, DatabaseOverview, DatabaseTableMeta, DatabaseTableRow, DatabaseTableRowsPage } from '../api/types';
import { formatErrorMessage } from '../utils/errors';

const PAGE_SIZE = 50;
const LOADING_COPY = '正在加载数据库展示数据...';
const EMPTY_COPY = '数据库展示暂不可用。';

const COMMAND_GROUPS = [
  {
    title: '检查 PostgreSQL 是否在线',
    language: 'bash',
    command: 'pg_isready -h 127.0.0.1 -p 54329 -d intelligence_rag',
  },
  {
    title: '进入 psql 交互模式',
    language: 'bash',
    command: 'psql -h 127.0.0.1 -p 54329 -U intelligence -d intelligence_rag',
  },
  {
    title: 'psql 内部常用命令',
    language: 'sql',
    command: '\\dt\n\\d content_items\n\\x auto\n\\q',
  },
  {
    title: '查看核心表数量',
    language: 'sql',
    command:
      "select 'source_sites' as table_name, count(*) from source_sites\n" +
      "union all select 'crawl_runs', count(*) from crawl_runs\n" +
      "union all select 'raw_pages', count(*) from raw_pages\n" +
      "union all select 'content_items', count(*) from content_items\n" +
      "union all select 'content_chunks', count(*) from content_chunks\n" +
      "union all select 'search_queries', count(*) from search_queries;",
  },
  {
    title: '查看 chunk 与 Qdrant 的关联状态',
    language: 'sql',
    command:
      'select embed_status, vector_backend, count(*) as chunk_count\n' +
      'from content_chunks\n' +
      'group by 1, 2\n' +
      'order by 1, 2;',
  },
  {
    title: '查看 Qdrant 健康和 collection',
    language: 'bash',
    command:
      'curl -fsS http://127.0.0.1:6333/healthz\n' +
      'curl -fsS http://127.0.0.1:6333/collections/content_chunks_v1 | python3 -m json.tool',
  },
];

export function DatabasePage() {
  const [overview, setOverview] = useState<DatabaseOverview | null>(null);
  const [tables, setTables] = useState<DatabaseTableMeta[]>([]);
  const [selectedTable, setSelectedTable] = useState('');
  const [rowsPage, setRowsPage] = useState<DatabaseTableRowsPage | null>(null);
  const [selectedRow, setSelectedRow] = useState<DatabaseTableRow | null>(null);
  const [query, setQuery] = useState('');
  const [appliedQuery, setAppliedQuery] = useState('');
  const [offset, setOffset] = useState(0);
  const [loading, setLoading] = useState(true);
  const [rowsLoading, setRowsLoading] = useState(false);
  const [error, setError] = useState('');
  const [rowsError, setRowsError] = useState('');
  const [copiedTitle, setCopiedTitle] = useState('');

  useEffect(() => {
    let ignore = false;
    setLoading(true);
    setError('');
    Promise.all([getDatabaseOverview(), listDatabaseTables()])
      .then(([overviewResult, tablesResult]) => {
        if (ignore) {
          return;
        }
        setOverview(overviewResult);
        setTables(tablesResult.items);
        setSelectedTable((current) => current || tablesResult.items[0]?.table_name || '');
        setLoading(false);
      })
      .catch((reason: unknown) => {
        if (ignore) {
          return;
        }
        setError(formatErrorMessage('无法加载数据库展示数据', reason));
        setLoading(false);
      });
    return () => {
      ignore = true;
    };
  }, []);

  useEffect(() => {
    if (!selectedTable) {
      setRowsPage(null);
      setSelectedRow(null);
      return;
    }
    let ignore = false;
    setRowsLoading(true);
    setRowsError('');
    listDatabaseRows(selectedTable, { limit: PAGE_SIZE, offset, q: appliedQuery })
      .then((page) => {
        if (ignore) {
          return;
        }
        setRowsPage(page);
        setSelectedRow(page.items[0] || null);
        setRowsLoading(false);
      })
      .catch((reason: unknown) => {
        if (ignore) {
          return;
        }
        setRowsError(formatErrorMessage('无法加载表数据', reason));
        setRowsLoading(false);
      });
    return () => {
      ignore = true;
    };
  }, [selectedTable, offset, appliedQuery]);

  const selectedTableMeta = useMemo(
    () => tables.find((table) => table.table_name === selectedTable) || null,
    [selectedTable, tables],
  );

  function handleSearch(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setOffset(0);
    setAppliedQuery(query.trim());
  }

  function handleTableChange(value: string) {
    setSelectedTable(value);
    setQuery('');
    setAppliedQuery('');
    setOffset(0);
  }

  async function copyCommand(title: string, command: string) {
    if (!navigator.clipboard) {
      setCopiedTitle('当前浏览器不支持复制。');
      return;
    }
    await navigator.clipboard.writeText(command);
    setCopiedTitle(title);
  }

  const canGoPrevious = offset > 0;
  const canGoNext = rowsPage ? offset + rowsPage.limit < rowsPage.total : false;

  return (
    <section>
      <div className="page-title">
        <p className="eyebrow">只读数据库视图 Read-only Database</p>
        <h1>PostgreSQL 数据库展示 Database</h1>
        <p className="muted">只读展示，不支持 SQL 执行或写入操作。</p>
      </div>

      {error ? (
        <p className="card error-text" role="alert">
          {error}
        </p>
      ) : null}
      {loading ? (
        <p className="card status-line" role="status" aria-live="polite">
          {LOADING_COPY}
        </p>
      ) : null}
      {!loading && error ? <p className="card empty-state">{EMPTY_COPY}</p> : null}

      {overview ? <OverviewCards overview={overview} /> : null}

      <div className="grid database-layout">
        <section className="card database-browser-card">
          <div className="card-header">
            <div>
              <h2>核心表只读浏览器</h2>
              <p className="muted compact">白名单表、分页、关键词搜索；不提供任意 SQL。</p>
            </div>
            <span className="badge">read-only</span>
          </div>

          <div className="database-controls">
            <label htmlFor="database-table-select">选择表</label>
            <select
              id="database-table-select"
              value={selectedTable}
              onChange={(event) => handleTableChange(event.target.value)}
            >
              {tables.map((table) => (
                <option key={table.table_name} value={table.table_name}>
                  {table.label}
                </option>
              ))}
            </select>
          </div>

          {selectedTableMeta ? (
            <p className="muted compact">
              {selectedTableMeta.description} 默认排序：{selectedTableMeta.default_sort}
            </p>
          ) : null}

          <form className="inline-form" onSubmit={handleSearch}>
            <label className="sr-only" htmlFor="database-table-query">
              表内搜索
            </label>
            <input
              id="database-table-query"
              value={query}
              onChange={(event) => setQuery(event.target.value)}
              placeholder="按白名单字段搜索"
            />
            <button type="submit">搜索表数据</button>
          </form>

          {rowsError ? <p className="error-text" role="alert">{rowsError}</p> : null}
          {rowsLoading ? <p className="status-line">正在加载表数据...</p> : null}
          {rowsPage ? (
            <>
              <p className="muted compact">
                total={rowsPage.total} limit={rowsPage.limit} offset={rowsPage.offset}
              </p>
              <DatabaseRowsTable rows={rowsPage.items} selectedRow={selectedRow} onSelectRow={setSelectedRow} />
              <div className="button-row">
                <button type="button" disabled={!canGoPrevious} onClick={() => setOffset(Math.max(0, offset - PAGE_SIZE))}>
                  上一页
                </button>
                <button type="button" disabled={!canGoNext} onClick={() => setOffset(offset + PAGE_SIZE)}>
                  下一页
                </button>
              </div>
            </>
          ) : null}
        </section>

        <aside className="card row-detail-card">
          <h2>行详情</h2>
          {selectedRow ? <RowDetail row={selectedRow} /> : <p className="muted">选择一行查看详情。</p>}
        </aside>
      </div>

      <section className="card command-handbook" aria-label="psql 使用手册">
        <div className="card-header">
          <div>
            <h2>psql / Qdrant 命令手册</h2>
            <p className="muted compact">用于终端验证数据库和向量库接入；前端不会展示数据库密码。</p>
          </div>
          {copiedTitle ? <span className="badge">已复制：{copiedTitle}</span> : null}
        </div>
        <div className="grid command-grid">
          {COMMAND_GROUPS.map((group) => (
            <article className="command-card" key={group.title}>
              <div className="card-header compact-header">
                <h3>{group.title}</h3>
                <button type="button" onClick={() => copyCommand(group.title, group.command)}>
                  复制
                </button>
              </div>
              <p className="muted compact">{group.language}</p>
              <pre>{group.command}</pre>
            </article>
          ))}
        </div>
      </section>
    </section>
  );
}

function OverviewCards({ overview }: { overview: DatabaseOverview }) {
  const tableCounts = overview.postgres.table_counts;
  return (
    <>
      <div className="grid metric-grid">
        <div className="card metric-card">
          <span className="muted">PostgreSQL</span>
          <strong>{overview.postgres.status}</strong>
          <span>{overview.postgres.database_name}</span>
        </div>
        <div className="card metric-card">
          <span className="muted">Qdrant</span>
          <strong>{overview.qdrant.status}</strong>
          <span>{overview.qdrant.collection}</span>
        </div>
        <div className="card metric-card">
          <span className="muted">对账状态</span>
          <strong>{overview.reconciliation.status}</strong>
          <span>
            PostgreSQL {overview.reconciliation.postgres_qdrant_chunk_count} / Qdrant{' '}
            {overview.reconciliation.qdrant_points_count ?? '—'}
          </span>
        </div>
        <div className="card metric-card">
          <span className="muted">向量配置</span>
          <strong>{overview.qdrant.vector_size ?? '—'}</strong>
          <span>{overview.qdrant.distance ?? overview.qdrant.error ?? 'unknown'}</span>
        </div>
      </div>

      <div className="grid two-column">
        <section className="card">
          <h2>核心表计数</h2>
          <div className="grid counter-grid">
            {tableCounts.map((item) => (
              <div className="counter" key={item.table_name}>
                <span className="muted">{item.table_name}</span>
                <strong>{item.count}</strong>
              </div>
            ))}
          </div>
        </section>
        <section className="card">
          <h2>chunk 向量化状态</h2>
          {overview.postgres.chunk_vector_status.length ? (
            <ul className="dense-list">
              {overview.postgres.chunk_vector_status.map((item) => (
                <li key={`${item.embed_status}-${item.vector_backend ?? 'null'}`}>
                  <span className="badge">{item.count}</span>
                  <span>{item.embed_status} / {item.vector_backend ?? 'NULL'}</span>
                  <span className="muted">content_chunks</span>
                </li>
              ))}
            </ul>
          ) : (
            <p className="muted">暂无 chunk 状态。</p>
          )}
        </section>
      </div>
    </>
  );
}

function DatabaseRowsTable({
  rows,
  selectedRow,
  onSelectRow,
}: {
  rows: DatabaseTableRow[];
  selectedRow: DatabaseTableRow | null;
  onSelectRow: (row: DatabaseTableRow) => void;
}) {
  if (!rows.length) {
    return <p className="empty-state">当前表没有匹配行。</p>;
  }
  const columns = Object.keys(rows[0].preview);
  return (
    <div className="table-scroll">
      <table className="data-table">
        <thead>
          <tr>
            {columns.map((column) => <th key={column}>{column}</th>)}
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => (
            <tr
              className={selectedRow?.id === row.id ? 'selected-row' : ''}
              key={row.id}
              onClick={() => onSelectRow(row)}
            >
              {columns.map((column) => <td key={column}>{formatValue(row.preview[column])}</td>)}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function RowDetail({ row }: { row: DatabaseTableRow }) {
  return (
    <div>
      <p className="muted compact">{row.table_name}</p>
      <h3>{row.id}</h3>
      <DetailSection title="Preview" value={row.preview} />
      <DetailSection title="Detail" value={row.detail} />
      <DetailSection title="Related" value={row.related} />
    </div>
  );
}

function DetailSection({ title, value }: { title: string; value: Record<string, DatabaseJsonValue> }) {
  if (!Object.keys(value).length) {
    return null;
  }
  return (
    <section className="detail-section">
      <h4>{title}</h4>
      <pre>{JSON.stringify(value, null, 2)}</pre>
    </section>
  );
}

function formatValue(value: DatabaseJsonValue): string {
  if (value === null || value === undefined) {
    return '—';
  }
  if (typeof value === 'object') {
    return JSON.stringify(value);
  }
  return String(value);
}
```

- [ ] **Step 4: Add Database navigation**

Modify `frontend/src/App.tsx`:

1. Add import:

```ts
import { DatabasePage } from './pages/DatabasePage';
```

2. Change the `View` type to:

```ts
type View = 'Dashboard' | 'Sources' | 'Runs' | 'Search' | 'Content' | 'Database';
```

3. Change `NAV_ITEMS` to:

```ts
const NAV_ITEMS: View[] = ['Dashboard', 'Sources', 'Runs', 'Search', 'Content', 'Database'];
```

4. Add label:

```ts
  Database: '数据库 Database',
```

5. Add render branch inside `<main className="main">`:

```tsx
        {activeView === 'Database' ? <DatabasePage /> : null}
```

- [ ] **Step 5: Add styles**

Append this CSS to `frontend/src/styles.css`:

```css
.database-layout {
  grid-template-columns: minmax(0, 1.4fr) minmax(320px, 0.8fr);
  align-items: start;
}

.database-controls {
  display: grid;
  grid-template-columns: 120px minmax(0, 1fr);
  gap: 12px;
  align-items: center;
  margin-bottom: 12px;
}

.table-scroll {
  overflow-x: auto;
  border: 1px solid #1f2937;
  border-radius: 10px;
}

.data-table {
  width: 100%;
  border-collapse: collapse;
  min-width: 680px;
}

.data-table th,
.data-table td {
  max-width: 280px;
  padding: 10px;
  border-bottom: 1px solid #1f2937;
  overflow: hidden;
  text-align: left;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.data-table th {
  color: #bfdbfe;
  font-size: 0.76rem;
  letter-spacing: 0.08em;
  text-transform: uppercase;
}

.data-table tbody tr {
  cursor: pointer;
}

.data-table tbody tr:hover,
.data-table tbody tr.selected-row {
  background: #1e293b;
}

.row-detail-card {
  position: sticky;
  top: 24px;
}

.detail-section h4 {
  margin-bottom: 6px;
  color: #bfdbfe;
}

.command-grid {
  grid-template-columns: repeat(auto-fit, minmax(320px, 1fr));
}

.command-card {
  padding: 14px;
  border: 1px solid #1f2937;
  border-radius: 10px;
  background: #0f172a;
}

.command-card pre {
  max-height: 260px;
  margin-bottom: 0;
}

.sr-only {
  position: absolute;
  width: 1px;
  height: 1px;
  padding: 0;
  margin: -1px;
  overflow: hidden;
  clip: rect(0, 0, 0, 0);
  white-space: nowrap;
  border: 0;
}

@media (max-width: 960px) {
  .database-layout {
    grid-template-columns: 1fr;
  }

  .row-detail-card {
    position: static;
  }
}
```

- [ ] **Step 6: Run frontend tests**

Run:

```bash
cd /root/intelligence-rag/frontend
npm test -- database-page.test.tsx app-database-navigation.test.tsx
```

Expected: PASS.

- [ ] **Step 7: Run frontend build**

Run:

```bash
cd /root/intelligence-rag/frontend
npm run build
```

Expected: PASS with TypeScript and Vite build success.

- [ ] **Step 8: Commit frontend implementation**

```bash
cd /root/intelligence-rag
git add frontend/src/api/types.ts frontend/src/api/client.ts frontend/src/pages/DatabasePage.tsx frontend/src/App.tsx frontend/src/styles.css frontend/tests/database-page.test.tsx frontend/tests/app-database-navigation.test.tsx
git commit -m "feat: add readonly database frontend page"
```

---

## Task 5: End-to-End Verification and Demo Refresh

**Files:**
- No new source files.
- Build artifact: `frontend/dist/` remains ignored.

- [ ] **Step 1: Run backend database tests**

```bash
cd /root/intelligence-rag
.venv/bin/python -m pytest backend/tests/test_database_api.py -q
```

Expected: PASS.

- [ ] **Step 2: Run broader backend regression tests**

```bash
cd /root/intelligence-rag
.venv/bin/python -m pytest backend/tests/test_health.py backend/tests/test_control_api.py backend/tests/test_content_api.py backend/tests/test_search_pipeline.py -q
```

Expected: PASS.

- [ ] **Step 3: Run frontend tests and build**

```bash
cd /root/intelligence-rag/frontend
npm test -- database-page.test.tsx app-database-navigation.test.tsx
npm run build
```

Expected: both commands PASS.

- [ ] **Step 4: Verify local API endpoints against current services**

```bash
curl -fsS http://127.0.0.1:18000/database/overview | python3 -m json.tool | sed -n '1,160p'
curl -fsS http://127.0.0.1:18000/database/tables | python3 -m json.tool
curl -fsS 'http://127.0.0.1:18000/database/tables/content_chunks/rows?limit=5&offset=0&q=qdrant' | python3 -m json.tool | sed -n '1,180p'
```

Expected:

- `/database/overview` returns `postgres.status = "ok"`.
- `/database/overview` returns `qdrant.collection = "content_chunks_v1"`.
- `/database/overview` returns `reconciliation.status = "matched"` when PostgreSQL qdrant chunks and Qdrant points are equal.
- `/database/tables` returns the eight whitelisted table names.
- `/database/tables/content_chunks/rows` returns rows with `embed_status` and `vector_backend` fields.

- [ ] **Step 5: Refresh public frontend demo**

```bash
cd /root/intelligence-rag/frontend
VITE_API_BASE_URL= npm run build
cd /root/intelligence-rag
pkill -f 'intel_rag_demo_proxy.py --host 0.0.0.0 --port 80' || true
nohup python3 skills/dji-lito-demo-ingestion/scripts/demo_proxy.py \
  --host 0.0.0.0 --port 80 \
  --dist /root/intelligence-rag/frontend/dist \
  --api http://127.0.0.1:18000 \
  > /root/logs/intel-rag-frontend-demo-80.log 2>&1 &
```

Expected: frontend is rebuilt with same-origin API calls and port 80 proxy is running.

- [ ] **Step 6: Verify public demo health and page route manually**

```bash
curl -fsS http://62.234.15.249/health
curl -fsS http://62.234.15.249/database/overview | python3 -m json.tool | sed -n '1,80p'
```

Expected:

- `/health` returns `{"status":"ok","service":"intelligence-rag-api"}`.
- `/database/overview` returns PostgreSQL/Qdrant overview JSON through the proxy.
- In browser, open `http://62.234.15.249/`, click `数据库 Database`, and confirm the Database page renders overview cards, table browser, row detail, and psql handbook.

- [ ] **Step 7: Commit verification-only changes if any**

If no tracked source file changed during verification, do not commit. If a tracked file changed, inspect it and commit only intentional changes:

```bash
cd /root/intelligence-rag
git status --short
git diff --stat
```

Expected: no unexpected tracked changes.

---

## Self-Review Checklist

- Spec coverage:
  - PostgreSQL overview: Task 2.
  - Qdrant reconciliation: Task 2 and Task 5.
  - White-list table browser: Task 2 and Task 4.
  - psql handbook: Task 4.
  - No arbitrary SQL/no secrets: Task 2 route/repository design and Task 4 UI copy.
  - Tests/build/manual verification: Tasks 1, 3, and 5.
- Placeholder scan: this plan intentionally contains no TBD, TODO, fill-in-later, or unspecified implementation step.
- Type consistency: backend schemas use `DatabaseOverview`, `DatabaseTableList`, `DatabaseTableRowsPage`, and `DatabaseTableRow`; frontend uses matching `DatabaseOverview`, `DatabaseTableList`, `DatabaseTableRowsPage`, and `DatabaseTableRow` types.
