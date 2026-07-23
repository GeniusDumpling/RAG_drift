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
from app.models.literature import (
    LiteratureArtifact,
    LiteratureEvidence,
    LiteraturePaper,
    LiteratureRunEvent,
    LiteratureRunPaper,
)
from app.models.literature import (
    LiteratureRun as LiteratureRunModel,
)
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

ModelType = (
    type[SourceSite]
    | type[CrawlJob]
    | type[CrawlRun]
    | type[CrawlRunEvent]
    | type[RawPage]
    | type[ContentItem]
    | type[ContentChunk]
    | type[LiteratureArtifact]
    | type[LiteratureEvidence]
    | type[LiteraturePaper]
    | type[LiteratureRunModel]
    | type[LiteratureRunEvent]
    | type[LiteratureRunPaper]
    | type[SearchQuery]
)
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
        detail_columns=(
            "allowed_domains",
            "fetch_mode",
            "default_language",
            "config_json",
            "updated_at",
        ),
        searchable_columns=("name", "base_url", "site_type"),
        default_sort_column="created_at",
    ),
    TableConfig(
        table_name="crawl_jobs",
        label="采集任务 crawl_jobs",
        description="手动或计划触发的采集任务配置。",
        model=CrawlJob,
        preview_columns=("id", "source_site_id", "name", "trigger_mode", "enabled", "created_at"),
        detail_columns=(
            "cron_expr",
            "seed_config_json",
            "parser_profile",
            "max_pages",
            "agent_policy_json",
            "updated_at",
        ),
        searchable_columns=("name", "parser_profile"),
        default_sort_column="created_at",
    ),
    TableConfig(
        table_name="crawl_runs",
        label="运行记录 crawl_runs",
        description="一次采集/处理 run 的状态、计数和错误摘要。",
        model=CrawlRun,
        preview_columns=(
            "id",
            "status",
            "trigger_type",
            "parsed_count",
            "chunked_count",
            "embedded_count",
            "created_at",
        ),
        detail_columns=(
            "source_site_id",
            "crawl_job_id",
            "seed_url",
            "error_count",
            "error_message",
            "config_snapshot_json",
        ),
        searchable_columns=("status", "seed_url", "error_message"),
        default_sort_column="created_at",
    ),
    TableConfig(
        table_name="crawl_run_events",
        label="运行事件 crawl_run_events",
        description="run 时间线事件和阶段性计数。",
        model=CrawlRunEvent,
        preview_columns=("id", "crawl_run_id", "stage", "level", "event_type", "created_at"),
        detail_columns=(
            "message",
            "related_url",
            "related_raw_page_id",
            "related_content_item_id",
            "counters_json",
            "agent_trace_json",
        ),
        searchable_columns=("stage", "level", "event_type", "message", "related_url"),
        default_sort_column="created_at",
    ),
    TableConfig(
        table_name="raw_pages",
        label="原始快照 raw_pages",
        description="抓取后、抽取前持久化的原始页面/API 响应。",
        model=RawPage,
        preview_columns=(
            "id",
            "source_site_id",
            "crawl_run_id",
            "requested_url",
            "http_status",
            "parse_status",
            "fetched_at",
        ),
        detail_columns=(
            "final_url",
            "content_type",
            "raw_text",
            "raw_json",
            "fetch_error",
            "parse_error",
            "body_hash",
        ),
        searchable_columns=("requested_url", "final_url", "raw_text", "fetch_error", "parse_error"),
        default_sort_column="fetched_at",
    ),
    TableConfig(
        table_name="content_items",
        label="内容项 content_items",
        description="归一化后的 thread/comment/article 等展示与引用单位。",
        model=ContentItem,
        preview_columns=("id", "item_type", "title", "canonical_url", "published_at", "created_at"),
        detail_columns=(
            "source_site_id",
            "raw_page_id",
            "crawl_run_id",
            "language",
            "cleaned_text",
            "summary_text",
            "tags",
            "metadata_json",
        ),
        searchable_columns=("title", "canonical_url", "cleaned_text", "summary_text"),
        default_sort_column="created_at",
    ),
    TableConfig(
        table_name="content_chunks",
        label="内容切片 content_chunks",
        description="RAG 检索单位，记录 embedding 和 Qdrant point 状态。",
        model=ContentChunk,
        preview_columns=(
            "id",
            "content_item_id",
            "chunk_index",
            "embed_status",
            "vector_backend",
            "embedded_at",
        ),
        detail_columns=(
            "display_text",
            "embed_text",
            "token_count",
            "chunk_metadata_json",
            "qdrant_point_id",
            "vector_point_id",
            "embed_error",
        ),
        searchable_columns=(
            "display_text",
            "embed_text",
            "embed_status",
            "vector_backend",
            "embed_error",
        ),
        default_sort_column="created_at",
    ),
    TableConfig(
        table_name="search_queries",
        label="检索记录 search_queries",
        description="用户 query、优化结果、检索 trace 和结果计数。",
        model=SearchQuery,
        preview_columns=("id", "raw_query", "mode", "top_k", "result_count", "created_at"),
        detail_columns=(
            "filter_json",
            "used_agent",
            "optimized_query_text",
            "keyword_terms_json",
            "result_summary_json",
            "query_trace_json",
        ),
        searchable_columns=("raw_query", "optimized_query_text", "mode"),
        default_sort_column="created_at",
    ),
    TableConfig(
        table_name="literature_runs",
        label="文献任务 literature_runs",
        description="IEEE 文献研究的异步任务记录。",
        model=LiteratureRunModel,
        preview_columns=(
            "id",
            "query",
            "status",
            "current_stage",
            "progress_current",
            "created_at",
        ),
        detail_columns=(
            "progress_total",
            "progress_message",
            "options_json",
            "directions_json",
            "error_message",
            "started_at",
            "finished_at",
        ),
        searchable_columns=("query", "status", "error_message"),
        default_sort_column="created_at",
    ),
    TableConfig(
        table_name="literature_run_events",
        label="文献事件 literature_run_events",
        description="文献研究任务的时间线事件。",
        model=LiteratureRunEvent,
        preview_columns=("id", "literature_run_id", "stage", "level", "event_type", "created_at"),
        detail_columns=(
            "message",
            "counters_json",
            "trace_json",
        ),
        searchable_columns=("stage", "level", "event_type", "message"),
        default_sort_column="created_at",
    ),
    TableConfig(
        table_name="literature_papers",
        label="文献元数据 literature_papers",
        description="IEEE 论文的元数据、摘要、作者。",
        model=LiteraturePaper,
        preview_columns=(
            "id",
            "title",
            "publication_year",
            "citation_count",
            "access_type",
            "created_at",
        ),
        detail_columns=(
            "ieee_article_number",
            "doi",
            "authors_json",
            "abstract",
            "publication_title",
            "document_url",
            "pdf_url",
            "raw_metadata_json",
        ),
        searchable_columns=("title", "ieee_article_number", "doi"),
        default_sort_column="created_at",
    ),
    TableConfig(
        table_name="literature_run_papers",
        label="任务入选论文 literature_run_papers",
        description="研究任务与论文的关系，含分析状态和结论。",
        model=LiteratureRunPaper,
        preview_columns=(
            "id",
            "literature_run_id",
            "paper_id",
            "direction_id",
            "selected",
            "analysis_status",
            "created_at",
        ),
        detail_columns=(
            "direction_title",
            "search_query",
            "candidate_rank",
            "selected_rank",
            "score",
            "score_detail_json",
            "abstract_zh",
            "match_how",
            "match_use",
            "conclusion",
            "analysis_json",
        ),
        searchable_columns=("direction_id", "analysis_status", "search_query"),
        default_sort_column="created_at",
    ),
    TableConfig(
        table_name="literature_artifacts",
        label="文献制品 literature_artifacts",
        description="论文的 PDF、JSON、全文、报告等制品。",
        model=LiteratureArtifact,
        preview_columns=(
            "id",
            "literature_run_id",
            "paper_id",
            "artifact_type",
            "mime_type",
            "created_at",
        ),
        detail_columns=(
            "byte_size",
            "sha256",
            "source_url",
        ),
        searchable_columns=("artifact_type", "mime_type", "source_url"),
        default_sort_column="created_at",
    ),
    TableConfig(
        table_name="literature_evidence",
        label="文献证据 literature_evidence",
        description="论文分析中抽取的原文证据子串和页码。",
        model=LiteratureEvidence,
        preview_columns=(
            "id",
            "literature_run_paper_id",
            "matched_term",
            "match_level",
            "verified",
            "created_at",
        ),
        detail_columns=(
            "evidence_text",
            "evidence_source",
            "page_number",
            "section_name",
            "char_start",
            "char_end",
        ),
        searchable_columns=("matched_term", "match_level", "evidence_text"),
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

    latest_run_model = await session.scalar(
        select(CrawlRun).order_by(CrawlRun.created_at.desc()).limit(1)
    )
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
        preview={
            column: _serialize_value(getattr(row, column)) for column in config.preview_columns
        },
        detail=_serialize_detail_columns(config, row),
        related=await _related_summary(session, config, row),
    )


def _serialize_detail_columns(config: TableConfig, row: Any) -> dict[str, JsonValue]:
    detail: dict[str, JsonValue] = {}
    for column in config.detail_columns:
        serialized = _serialize_detail(column, getattr(row, column))
        preview_key = f"{column}_preview"
        if isinstance(serialized, dict) and preview_key in serialized:
            detail[preview_key] = serialized[preview_key]
            if serialized.get("truncated") is True:
                detail[f"{column}_truncated"] = True
        else:
            detail[column] = serialized
    return detail


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
    if isinstance(value, dict | list):
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
    if isinstance(value, dict | list | int | float | bool) or value is None:
        return value
    return str(value)


def _safe_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    return None
