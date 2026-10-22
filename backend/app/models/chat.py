import uuid
from typing import Any

from sqlalchemy import CheckConstraint, ForeignKey, Index, String, Text, text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, UuidPrimaryKeyMixin


class ChatSession(Base, UuidPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "chat_sessions"

    title: Mapped[str] = mapped_column(String(120), nullable=False, default="新对话")


class ChatTurn(Base, UuidPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "chat_turns"
    __table_args__ = (
        CheckConstraint(
            "status IN ('pending', 'completed', 'cancelled', 'error')", name="status_valid"
        ),
        Index("ix_chat_turns_session_created", "session_id", "created_at"),
        Index(
            "uq_chat_turns_pending_session",
            "session_id",
            unique=True,
            postgresql_where=text("status = 'pending'"),
        ),
    )

    session_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("chat_sessions.id", ondelete="CASCADE"), nullable=False
    )
    search_query_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("search_queries.id", ondelete="SET NULL")
    )
    query: Mapped[str] = mapped_column(Text, nullable=False)
    answer: Mapped[str] = mapped_column(Text, nullable=False, default="")
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="pending")
    supporting_evidence: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB, default=list, nullable=False
    )
    generation: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
