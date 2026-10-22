"""Persist chat sessions, turns, evidence snapshots and generation diagnostics."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0007_chat_sessions"
down_revision = "0006_video_graph_partial_success"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "chat_sessions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("title", sa.String(120), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )
    op.create_table(
        "chat_turns",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "session_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("chat_sessions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "search_query_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("search_queries.id", ondelete="SET NULL"),
        ),
        sa.Column("query", sa.Text(), nullable=False),
        sa.Column("answer", sa.Text(), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("supporting_evidence", postgresql.JSONB(), nullable=False),
        sa.Column("generation", postgresql.JSONB()),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint(
            "status IN ('pending', 'completed', 'cancelled', 'error')",
            name=op.f("ck_chat_turns_status_valid"),
        ),
    )
    op.create_index("ix_chat_turns_session_created", "chat_turns", ["session_id", "created_at"])
    op.create_index(
        "uq_chat_turns_pending_session",
        "chat_turns",
        ["session_id"],
        unique=True,
        postgresql_where=sa.text("status = 'pending'"),
    )


def downgrade() -> None:
    op.drop_table("chat_turns")
    op.drop_table("chat_sessions")
