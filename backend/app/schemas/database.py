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
