import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, TSVECTOR, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, UuidPrimaryKeyMixin, utcnow


class RawPage(Base, UuidPrimaryKeyMixin):
    __tablename__ = "raw_pages"
    __table_args__ = (
        CheckConstraint(
            "extraction_confidence IS NULL OR "
            "extraction_confidence >= 0 AND "
            "extraction_confidence <= 1",
            name="extraction_confidence_unit_interval",
        ),
        Index("ix_raw_pages_source_site_id", "source_site_id"),
        Index("ix_raw_pages_crawl_run_id", "crawl_run_id"),
        Index("ix_raw_pages_body_hash", "body_hash"),
    )

    source_site_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("source_sites.id"), nullable=False
    )
    crawl_run_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("crawl_runs.id"), nullable=False
    )
    requested_url: Mapped[str] = mapped_column(Text, nullable=False)
    final_url: Mapped[str | None] = mapped_column(Text)
    http_status: Mapped[int | None] = mapped_column(Integer)
    content_type: Mapped[str | None] = mapped_column(String(255))
    response_headers_json: Mapped[dict[str, Any]] = mapped_column(
        JSONB, default=dict, server_default=text("'{}'::jsonb"), nullable=False
    )
    raw_html: Mapped[str | None] = mapped_column(Text)
    raw_text: Mapped[str | None] = mapped_column(Text)
    raw_json: Mapped[dict[str, Any]] = mapped_column(
        JSONB, default=dict, server_default=text("'{}'::jsonb"), nullable=False
    )
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    fetch_error: Mapped[str | None] = mapped_column(Text)
    parser_profile: Mapped[str | None] = mapped_column(String(80))
    extraction_method: Mapped[str | None] = mapped_column(String(80))
    extraction_confidence: Mapped[float | None] = mapped_column(Float)
    parse_status: Mapped[str] = mapped_column(
        String(40), default="pending", server_default=text("'pending'"), nullable=False
    )
    parse_error: Mapped[str | None] = mapped_column(Text)
    body_hash: Mapped[str | None] = mapped_column(String(128))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, server_default=func.now(), nullable=False
    )


class Author(Base, UuidPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "authors"
    __table_args__ = (Index("ix_authors_source_site_id", "source_site_id"),)

    source_site_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("source_sites.id")
    )
    display_name: Mapped[str] = mapped_column(String(255), nullable=False)
    handle: Mapped[str | None] = mapped_column(String(255))
    profile_url: Mapped[str | None] = mapped_column(Text)
    external_author_id: Mapped[str | None] = mapped_column(String(255))
    raw_json: Mapped[dict[str, Any]] = mapped_column(
        JSONB, default=dict, server_default=text("'{}'::jsonb"), nullable=False
    )


class ContentItem(Base, UuidPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "content_items"
    __table_args__ = (
        CheckConstraint(
            "extraction_confidence IS NULL OR "
            "extraction_confidence >= 0 AND "
            "extraction_confidence <= 1",
            name="extraction_confidence_unit_interval",
        ),
        UniqueConstraint("dedup_key"),
        Index("ix_content_items_source_site_id", "source_site_id"),
        Index("ix_content_items_raw_page_id", "raw_page_id"),
        Index("ix_content_items_crawl_run_id", "crawl_run_id"),
        Index("ix_content_items_author_id", "author_id"),
        Index("ix_content_items_parent_item_id", "parent_item_id"),
        Index("ix_content_items_thread_root_id", "thread_root_id"),
        Index("ix_content_items_item_type", "item_type"),
        Index("ix_content_items_search_tsv_gin", "search_tsv", postgresql_using="gin"),
    )

    source_site_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("source_sites.id"), nullable=False
    )
    raw_page_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("raw_pages.id"), nullable=False
    )
    crawl_run_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("crawl_runs.id"), nullable=False
    )
    author_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("authors.id")
    )
    parent_item_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("content_items.id")
    )
    thread_root_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("content_items.id")
    )
    item_type: Mapped[str] = mapped_column(String(40), nullable=False)
    title: Mapped[str | None] = mapped_column(Text)
    canonical_url: Mapped[str] = mapped_column(Text, nullable=False)
    source_url: Mapped[str | None] = mapped_column(Text)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    language: Mapped[str | None] = mapped_column(String(16))
    raw_text: Mapped[str | None] = mapped_column(Text)
    cleaned_text: Mapped[str] = mapped_column(Text, nullable=False)
    summary_text: Mapped[str | None] = mapped_column(Text)
    structured_by: Mapped[str | None] = mapped_column(String(80))
    extraction_confidence: Mapped[float | None] = mapped_column(Float)
    tags: Mapped[list[str]] = mapped_column(
        JSONB, default=list, server_default=text("'[]'::jsonb"), nullable=False
    )
    metadata_json: Mapped[dict[str, Any]] = mapped_column(
        JSONB, default=dict, server_default=text("'{}'::jsonb"), nullable=False
    )
    content_hash: Mapped[str] = mapped_column(String(128), nullable=False)
    dedup_key: Mapped[str] = mapped_column(String(512), nullable=False)
    search_tsv: Mapped[str | None] = mapped_column(TSVECTOR)


class ContentChunk(Base, UuidPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "content_chunks"
    __table_args__ = (
        CheckConstraint("chunk_index >= 0", name="chunk_index_non_negative"),
        CheckConstraint(
            "token_count IS NULL OR token_count >= 0", name="token_count_non_negative"
        ),
        UniqueConstraint("content_item_id", "chunk_index"),
    )

    content_item_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("content_items.id", ondelete="CASCADE"), nullable=False
    )
    chunk_index: Mapped[int] = mapped_column(Integer, nullable=False)
    char_start: Mapped[int | None] = mapped_column(Integer)
    char_end: Mapped[int | None] = mapped_column(Integer)
    display_text: Mapped[str] = mapped_column(Text, nullable=False)
    embed_text: Mapped[str] = mapped_column(Text, nullable=False)
    token_count: Mapped[int | None] = mapped_column(Integer)
    chunk_metadata_json: Mapped[dict[str, Any]] = mapped_column(
        JSONB, default=dict, server_default=text("'{}'::jsonb"), nullable=False
    )
    qdrant_point_id: Mapped[str | None] = mapped_column(String(255))
    vector_backend: Mapped[str | None] = mapped_column(String(40))
    vector_point_id: Mapped[str | None] = mapped_column(String(255))
    embedded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    embed_status: Mapped[str] = mapped_column(
        String(40), default="pending", server_default=text("'pending'"), nullable=False
    )
    embed_error: Mapped[str | None] = mapped_column(Text)
