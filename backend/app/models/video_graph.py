"""Video graph projections. Active evidence is selected through a per-video build pointer."""

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
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, UuidPrimaryKeyMixin


class VideoGraphBuild(Base, UuidPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "video_graph_builds"
    __table_args__ = (
        UniqueConstraint("content_item_id", "fingerprint", "extractor_version"),
        CheckConstraint(
            "status IN ('pending','running','success','partial_success','failed','obsolete')",
            name="status_valid",
        ),
        CheckConstraint("attempts >= 0", name="attempts_non_negative"),
        Index("ix_video_graph_builds_queue", "status", "lease_until", "created_at"),
    )

    content_item_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("content_items.id", ondelete="CASCADE"), nullable=False
    )
    fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    extractor_version: Mapped[str] = mapped_column(String(160), nullable=False)
    status: Mapped[str] = mapped_column(
        String(20), default="pending", server_default="pending", nullable=False
    )
    attempts: Mapped[int] = mapped_column(Integer, default=0, server_default="0", nullable=False)
    lease_token: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error_message: Mapped[str | None] = mapped_column(Text)
    output_json: Mapped[dict[str, Any]] = mapped_column(
        JSONB, default=dict, server_default=text("'{}'::jsonb"), nullable=False
    )


class VideoGraphState(Base, TimestampMixin):
    __tablename__ = "video_graph_states"

    content_item_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("content_items.id", ondelete="CASCADE"), primary_key=True
    )
    source_key: Mapped[str] = mapped_column(String(512), nullable=False)
    withdrawn: Mapped[bool] = mapped_column(
        default=False, server_default=text("false"), nullable=False
    )
    active_build_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("video_graph_builds.id", ondelete="SET NULL")
    )
    desired_build_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("video_graph_builds.id", ondelete="SET NULL")
    )


class VideoGraphAlias(Base, UuidPrimaryKeyMixin):
    __tablename__ = "video_graph_aliases"
    __table_args__ = (UniqueConstraint("entity_type", "normalized_alias"),)

    entity_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("entities.id", ondelete="CASCADE"), nullable=False
    )
    entity_type: Mapped[str] = mapped_column(String(80), nullable=False)
    surface_text: Mapped[str] = mapped_column(String(255), nullable=False)
    normalized_alias: Mapped[str] = mapped_column(String(255), nullable=False)


class VideoGraphRelation(Base, UuidPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "video_graph_relations"
    __table_args__ = (
        UniqueConstraint("relation_key"),
        Index("ix_video_graph_relations_subject", "subject_id"),
        Index("ix_video_graph_relations_object", "object_id"),
    )

    relation_key: Mapped[str] = mapped_column(String(64), nullable=False)
    subject_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("entities.id"), nullable=False
    )
    object_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("entities.id"), nullable=False
    )
    predicate: Mapped[str] = mapped_column(String(40), nullable=False)
    qualifiers_json: Mapped[dict[str, str]] = mapped_column(
        JSONB, default=dict, server_default=text("'{}'::jsonb"), nullable=False
    )


class VideoGraphEvidence(Base, UuidPrimaryKeyMixin):
    __tablename__ = "video_graph_evidence"
    __table_args__ = (
        UniqueConstraint("build_id", "relation_id", "content_chunk_id", "quote_hash", "assertion"),
        CheckConstraint("assertion IN ('asserted','uncertain','negated')", name="assertion_valid"),
        CheckConstraint("confidence >= 0 AND confidence <= 1", name="confidence_valid"),
        Index("ix_video_graph_evidence_relation", "relation_id"),
        Index("ix_video_graph_evidence_build", "build_id"),
    )

    build_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("video_graph_builds.id", ondelete="CASCADE"), nullable=False
    )
    relation_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("video_graph_relations.id", ondelete="CASCADE"),
        nullable=False,
    )
    content_chunk_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("content_chunks.id", ondelete="SET NULL")
    )
    quote: Mapped[str] = mapped_column(Text, nullable=False)
    quote_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    char_start: Mapped[int] = mapped_column(Integer, nullable=False)
    char_end: Mapped[int] = mapped_column(Integer, nullable=False)
    start_seconds: Mapped[float | None] = mapped_column(Float)
    end_seconds: Mapped[float | None] = mapped_column(Float)
    assertion: Mapped[str] = mapped_column(String(20), nullable=False)
    confidence: Mapped[float] = mapped_column(Float, nullable=False)
    caption_source: Mapped[str | None] = mapped_column(String(80))
