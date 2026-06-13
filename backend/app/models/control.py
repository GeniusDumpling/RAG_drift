import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin, UuidPrimaryKeyMixin


class SourceSite(Base, UuidPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "source_sites"

    name: Mapped[str] = mapped_column(String(255), nullable=False)
    site_type: Mapped[str] = mapped_column(String(40), nullable=False)
    base_url: Mapped[str] = mapped_column(Text, nullable=False)
    allowed_domains: Mapped[list[str]] = mapped_column(JSONB, default=list, nullable=False)
    fetch_mode: Mapped[str] = mapped_column(String(40), nullable=False)
    default_language: Mapped[str | None] = mapped_column(String(16))
    active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    config_json: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, nullable=False)

    jobs: Mapped[list["CrawlJob"]] = relationship(back_populates="source_site")


class CrawlJob(Base, UuidPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "crawl_jobs"

    source_site_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("source_sites.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    trigger_mode: Mapped[str] = mapped_column(String(40), nullable=False)
    cron_expr: Mapped[str | None] = mapped_column(String(120))
    seed_config_json: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, nullable=False)
    parser_profile: Mapped[str] = mapped_column(String(80), nullable=False)
    max_pages: Mapped[int] = mapped_column(Integer, default=20, nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    agent_policy_json: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, nullable=False)
    last_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    next_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    source_site: Mapped[SourceSite] = relationship(back_populates="jobs")


class CrawlRun(Base, UuidPrimaryKeyMixin):
    __tablename__ = "crawl_runs"

    source_site_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("source_sites.id"), nullable=False
    )
    crawl_job_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("crawl_jobs.id"), nullable=False
    )
    trigger_type: Mapped[str] = mapped_column(String(40), nullable=False)
    execution_mode: Mapped[str] = mapped_column(
        String(40), default="agent_assisted", nullable=False
    )
    seed_url: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(40), default="queued", nullable=False)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    discovered_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    fetched_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    parsed_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    extracted_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    deduped_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    chunked_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    embedded_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    error_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    config_snapshot_json: Mapped[dict[str, Any]] = mapped_column(
        JSONB, default=dict, nullable=False
    )
    error_message: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class CrawlRunEvent(Base, UuidPrimaryKeyMixin):
    __tablename__ = "crawl_run_events"

    crawl_run_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("crawl_runs.id", ondelete="CASCADE"), nullable=False
    )
    stage: Mapped[str] = mapped_column(String(40), nullable=False)
    level: Mapped[str] = mapped_column(String(20), nullable=False)
    event_type: Mapped[str] = mapped_column(String(80), nullable=False)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    related_url: Mapped[str | None] = mapped_column(Text)
    related_raw_page_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("raw_pages.id")
    )
    related_content_item_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("content_items.id")
    )
    counters_json: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, nullable=False)
    agent_trace_json: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
