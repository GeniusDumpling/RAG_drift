import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, UuidPrimaryKeyMixin, utcnow


class LiteratureRun(Base, UuidPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "literature_runs"
    __table_args__ = (
        CheckConstraint("progress_current >= 0", name="progress_current_non_negative"),
        CheckConstraint("progress_total >= 0", name="progress_total_non_negative"),
        Index("ix_literature_runs_status_created_at", "status", "created_at"),
    )

    query: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(
        String(40), default="queued", server_default=text("'queued'"), nullable=False
    )
    current_stage: Mapped[str] = mapped_column(
        String(40), default="queued", server_default=text("'queued'"), nullable=False
    )
    progress_current: Mapped[int] = mapped_column(
        Integer, default=0, server_default=text("0"), nullable=False
    )
    progress_total: Mapped[int] = mapped_column(
        Integer, default=1, server_default=text("1"), nullable=False
    )
    progress_message: Mapped[str | None] = mapped_column(Text)
    options_json: Mapped[dict[str, Any]] = mapped_column(
        JSONB, default=dict, server_default=text("'{}'::jsonb"), nullable=False
    )
    directions_json: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB, default=list, server_default=text("'[]'::jsonb"), nullable=False
    )
    raw_search_results_json: Mapped[dict[str, Any]] = mapped_column(
        JSONB, default=dict, server_default=text("'{}'::jsonb"), nullable=False
    )
    picks_json: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB, default=list, server_default=text("'[]'::jsonb"), nullable=False
    )
    analyses_json: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB, default=list, server_default=text("'[]'::jsonb"), nullable=False
    )
    final_report_markdown: Mapped[str | None] = mapped_column(Text)
    error_message: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class LiteratureRunEvent(Base, UuidPrimaryKeyMixin):
    __tablename__ = "literature_run_events"
    __table_args__ = (
        Index("ix_literature_run_events_run_created", "literature_run_id", "created_at"),
    )

    literature_run_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("literature_runs.id", ondelete="CASCADE"), nullable=False
    )
    stage: Mapped[str] = mapped_column(String(40), nullable=False)
    level: Mapped[str] = mapped_column(
        String(20), default="info", server_default=text("'info'"), nullable=False
    )
    event_type: Mapped[str] = mapped_column(String(80), nullable=False)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    counters_json: Mapped[dict[str, Any]] = mapped_column(
        JSONB, default=dict, server_default=text("'{}'::jsonb"), nullable=False
    )
    trace_json: Mapped[dict[str, Any]] = mapped_column(
        JSONB, default=dict, server_default=text("'{}'::jsonb"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, server_default=func.now(), nullable=False
    )


class LiteraturePaper(Base, UuidPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "literature_papers"
    __table_args__ = (
        UniqueConstraint("ieee_article_number"),
        UniqueConstraint("doi"),
        Index("ix_literature_papers_title_hash", "title_hash"),
    )

    ieee_article_number: Mapped[str | None] = mapped_column(String(80))
    doi: Mapped[str | None] = mapped_column(String(255))
    title: Mapped[str] = mapped_column(Text, nullable=False)
    title_hash: Mapped[str] = mapped_column(String(128), nullable=False)
    authors_json: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB, default=list, server_default=text("'[]'::jsonb"), nullable=False
    )
    abstract: Mapped[str | None] = mapped_column(Text)
    publication_title: Mapped[str | None] = mapped_column(Text)
    publication_year: Mapped[int | None] = mapped_column(Integer)
    citation_count: Mapped[int] = mapped_column(
        Integer, default=0, server_default=text("0"), nullable=False
    )
    access_type: Mapped[str | None] = mapped_column(String(80))
    document_url: Mapped[str | None] = mapped_column(Text)
    pdf_url: Mapped[str | None] = mapped_column(Text)
    raw_metadata_json: Mapped[dict[str, Any]] = mapped_column(
        JSONB, default=dict, server_default=text("'{}'::jsonb"), nullable=False
    )
    metadata_hash: Mapped[str] = mapped_column(String(128), nullable=False)


class LiteratureRunPaper(Base, UuidPrimaryKeyMixin):
    __tablename__ = "literature_run_papers"
    __table_args__ = (
        UniqueConstraint(
            "literature_run_id",
            "paper_id",
            "direction_id",
            name="uq_lit_run_papers_run_paper_direction",
        ),
        Index("ix_literature_run_papers_run_selected", "literature_run_id", "selected"),
    )

    literature_run_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("literature_runs.id", ondelete="CASCADE"), nullable=False
    )
    paper_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("literature_papers.id", ondelete="CASCADE"), nullable=False
    )
    direction_id: Mapped[str] = mapped_column(String(40), nullable=False)
    direction_title: Mapped[str | None] = mapped_column(Text)
    search_query: Mapped[str] = mapped_column(Text, nullable=False)
    candidate_rank: Mapped[int | None] = mapped_column(Integer)
    selected_rank: Mapped[int | None] = mapped_column(Integer)
    score: Mapped[int | None] = mapped_column(BigInteger)
    score_detail_json: Mapped[dict[str, Any]] = mapped_column(
        JSONB, default=dict, server_default=text("'{}'::jsonb"), nullable=False
    )
    selected: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default=text("false"), nullable=False
    )
    analysis_status: Mapped[str] = mapped_column(
        String(40), default="pending", server_default=text("'pending'"), nullable=False
    )
    abstract_zh: Mapped[str | None] = mapped_column(Text)
    match_how: Mapped[str | None] = mapped_column(Text)
    match_use: Mapped[str | None] = mapped_column(Text)
    conclusion: Mapped[str | None] = mapped_column(Text)
    analysis_json: Mapped[dict[str, Any]] = mapped_column(
        JSONB, default=dict, server_default=text("'{}'::jsonb"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utcnow,
        onupdate=utcnow,
        server_default=func.now(),
        nullable=False,
    )


class LiteratureArtifact(Base, UuidPrimaryKeyMixin):
    __tablename__ = "literature_artifacts"
    __table_args__ = (
        CheckConstraint("byte_size IS NULL OR byte_size >= 0", name="byte_size_non_negative"),
        Index("ix_literature_artifacts_run_kind", "literature_run_id", "artifact_type"),
        Index("ix_literature_artifacts_paper_kind", "paper_id", "artifact_type"),
    )

    literature_run_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("literature_runs.id", ondelete="CASCADE"), nullable=False
    )
    paper_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("literature_papers.id", ondelete="CASCADE")
    )
    artifact_type: Mapped[str] = mapped_column(String(80), nullable=False)
    json_data: Mapped[dict[str, Any] | list[Any]] = mapped_column(
        JSONB, default=dict, server_default=text("'{}'::jsonb"), nullable=False
    )
    text_data: Mapped[str | None] = mapped_column(Text)
    binary_data: Mapped[bytes | None] = mapped_column(LargeBinary)
    mime_type: Mapped[str | None] = mapped_column(String(255))
    byte_size: Mapped[int | None] = mapped_column(BigInteger)
    sha256: Mapped[str | None] = mapped_column(String(64))
    source_url: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, server_default=func.now(), nullable=False
    )


class LiteratureEvidence(Base, UuidPrimaryKeyMixin):
    __tablename__ = "literature_evidence"
    __table_args__ = (Index("ix_literature_evidence_run_paper", "literature_run_paper_id"),)

    literature_run_paper_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(
            "literature_run_papers.id",
            ondelete="CASCADE",
            name="fk_lit_evidence_run_paper",
        ),
        nullable=False,
    )
    matched_term: Mapped[str] = mapped_column(Text, nullable=False)
    match_level: Mapped[str] = mapped_column(String(40), nullable=False)
    evidence_text: Mapped[str] = mapped_column(
        Text, default="", server_default=text("''"), nullable=False
    )
    evidence_source: Mapped[str] = mapped_column(
        String(40), default="", server_default=text("''"), nullable=False
    )
    page_number: Mapped[int | None] = mapped_column(Integer)
    section_name: Mapped[str | None] = mapped_column(String(120))
    char_start: Mapped[int | None] = mapped_column(Integer)
    char_end: Mapped[int | None] = mapped_column(Integer)
    verified: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default=text("false"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, server_default=func.now(), nullable=False
    )
