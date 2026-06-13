import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, UuidPrimaryKeyMixin, utcnow


class Entity(Base, UuidPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "entities"

    name: Mapped[str] = mapped_column(String(255), nullable=False)
    entity_type: Mapped[str] = mapped_column(String(80), nullable=False)
    canonical_name: Mapped[str | None] = mapped_column(String(255))
    metadata_json: Mapped[dict[str, Any]] = mapped_column(
        JSONB, default=dict, server_default=text("'{}'::jsonb"), nullable=False
    )


class ContentEntityMention(Base, UuidPrimaryKeyMixin):
    __tablename__ = "content_entity_mentions"
    __table_args__ = (
        CheckConstraint(
            "confidence IS NULL OR confidence >= 0 AND confidence <= 1",
            name="confidence_unit_interval",
        ),
        Index("ix_content_entity_mentions_content_item_id", "content_item_id"),
        Index("ix_content_entity_mentions_entity_id", "entity_id"),
        Index("ix_content_entity_mentions_content_chunk_id", "content_chunk_id"),
    )

    content_item_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("content_items.id", ondelete="CASCADE"), nullable=False
    )
    entity_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("entities.id", ondelete="CASCADE"), nullable=False
    )
    content_chunk_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("content_chunks.id", ondelete="SET NULL")
    )
    surface_text: Mapped[str | None] = mapped_column(String(255))
    start_char: Mapped[int | None] = mapped_column(Integer)
    end_char: Mapped[int | None] = mapped_column(Integer)
    confidence: Mapped[float | None] = mapped_column(Float)
    extraction_method: Mapped[str | None] = mapped_column(String(80))
    metadata_json: Mapped[dict[str, Any]] = mapped_column(
        JSONB, default=dict, server_default=text("'{}'::jsonb"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, server_default=func.now(), nullable=False
    )


class SearchQuery(Base, UuidPrimaryKeyMixin):
    __tablename__ = "search_queries"
    __table_args__ = (
        CheckConstraint("top_k > 0", name="top_k_positive"),
        Index("ix_search_queries_created_at", "created_at"),
    )

    raw_query: Mapped[str] = mapped_column(Text, nullable=False)
    filter_json: Mapped[dict[str, Any]] = mapped_column(
        JSONB, default=dict, server_default=text("'{}'::jsonb"), nullable=False
    )
    mode: Mapped[str] = mapped_column(
        String(40), default="hybrid", server_default=text("'hybrid'"), nullable=False
    )
    top_k: Mapped[int] = mapped_column(
        Integer, default=10, server_default=text("10"), nullable=False
    )
    used_agent: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default=text("false"), nullable=False
    )
    optimized_query: Mapped[str | None] = mapped_column(Text)
    keyword_terms_json: Mapped[list[str]] = mapped_column(
        JSONB, default=list, server_default=text("'[]'::jsonb"), nullable=False
    )
    entity_hints_json: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB, default=list, server_default=text("'[]'::jsonb"), nullable=False
    )
    time_hints_json: Mapped[dict[str, Any]] = mapped_column(
        JSONB, default=dict, server_default=text("'{}'::jsonb"), nullable=False
    )
    result_summary_json: Mapped[dict[str, Any]] = mapped_column(
        JSONB, default=dict, server_default=text("'{}'::jsonb"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, server_default=func.now(), nullable=False
    )


class AgentCall(Base, UuidPrimaryKeyMixin):
    __tablename__ = "agent_calls"
    __table_args__ = (
        CheckConstraint("latency_ms IS NULL OR latency_ms >= 0", name="latency_ms_non_negative"),
        Index("ix_agent_calls_related_crawl_run_id", "related_crawl_run_id"),
        Index("ix_agent_calls_related_raw_page_id", "related_raw_page_id"),
        Index("ix_agent_calls_related_content_item_id", "related_content_item_id"),
        Index("ix_agent_calls_related_search_query_id", "related_search_query_id"),
        Index("ix_agent_calls_status", "status"),
    )

    agent_role: Mapped[str] = mapped_column(String(80), nullable=False)
    caller: Mapped[str] = mapped_column(String(120), nullable=False)
    related_crawl_run_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("crawl_runs.id")
    )
    related_raw_page_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("raw_pages.id")
    )
    related_content_item_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("content_items.id")
    )
    related_search_query_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("search_queries.id")
    )
    request_schema_version: Mapped[str] = mapped_column(String(40), nullable=False)
    response_schema_version: Mapped[str] = mapped_column(String(40), nullable=False)
    status: Mapped[str] = mapped_column(
        String(40), default="pending", server_default=text("'pending'"), nullable=False
    )
    input_summary_json: Mapped[dict[str, Any]] = mapped_column(
        JSONB, default=dict, server_default=text("'{}'::jsonb"), nullable=False
    )
    output_summary_json: Mapped[dict[str, Any]] = mapped_column(
        JSONB, default=dict, server_default=text("'{}'::jsonb"), nullable=False
    )
    model_provider: Mapped[str | None] = mapped_column(String(80))
    model_name: Mapped[str | None] = mapped_column(String(120))
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    latency_ms: Mapped[int | None] = mapped_column(Integer)
    error_message: Mapped[str | None] = mapped_column(Text)
    trace_json: Mapped[dict[str, Any]] = mapped_column(
        JSONB, default=dict, server_default=text("'{}'::jsonb"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, server_default=func.now(), nullable=False
    )
