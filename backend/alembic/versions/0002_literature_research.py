"""Add traceable IEEE literature research tables.

Revision ID: 0002_literature_research
Revises: 0001_intel_rag_schema
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0002_literature_research"
down_revision: str | None = "0001_intel_rag_schema"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "literature_runs",
        sa.Column("query", sa.Text(), nullable=False),
        sa.Column("status", sa.String(length=40), server_default="queued", nullable=False),
        sa.Column("current_stage", sa.String(length=40), server_default="queued", nullable=False),
        sa.Column("progress_current", sa.Integer(), server_default="0", nullable=False),
        sa.Column("progress_total", sa.Integer(), server_default="1", nullable=False),
        sa.Column("progress_message", sa.Text(), nullable=True),
        sa.Column(
            "options_json",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default="{}",
            nullable=False,
        ),
        sa.Column(
            "directions_json",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default="[]",
            nullable=False,
        ),
        sa.Column(
            "raw_search_results_json",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default="{}",
            nullable=False,
        ),
        sa.Column(
            "picks_json",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default="[]",
            nullable=False,
        ),
        sa.Column(
            "analyses_json",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default="[]",
            nullable=False,
        ),
        sa.Column("final_report_markdown", sa.Text(), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "progress_current >= 0", name="ck_literature_runs_progress_current_non_negative"
        ),
        sa.CheckConstraint(
            "progress_total >= 0", name="ck_literature_runs_progress_total_non_negative"
        ),
        sa.PrimaryKeyConstraint("id", name="pk_literature_runs"),
    )
    op.create_index(
        "ix_literature_runs_status_created_at",
        "literature_runs",
        ["status", "created_at"],
        unique=False,
    )

    op.create_table(
        "literature_papers",
        sa.Column("ieee_article_number", sa.String(length=80), nullable=True),
        sa.Column("doi", sa.String(length=255), nullable=True),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("title_hash", sa.String(length=128), nullable=False),
        sa.Column(
            "authors_json",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default="[]",
            nullable=False,
        ),
        sa.Column("abstract", sa.Text(), nullable=True),
        sa.Column("publication_title", sa.Text(), nullable=True),
        sa.Column("publication_year", sa.Integer(), nullable=True),
        sa.Column("citation_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("access_type", sa.String(length=80), nullable=True),
        sa.Column("document_url", sa.Text(), nullable=True),
        sa.Column("pdf_url", sa.Text(), nullable=True),
        sa.Column(
            "raw_metadata_json",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default="{}",
            nullable=False,
        ),
        sa.Column("metadata_hash", sa.String(length=128), nullable=False),
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name="pk_literature_papers"),
        sa.UniqueConstraint("doi", name="uq_literature_papers_doi"),
        sa.UniqueConstraint("ieee_article_number", name="uq_literature_papers_ieee_article_number"),
    )
    op.create_index(
        "ix_literature_papers_title_hash", "literature_papers", ["title_hash"], unique=False
    )

    op.create_table(
        "literature_run_events",
        sa.Column("literature_run_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("stage", sa.String(length=40), nullable=False),
        sa.Column("level", sa.String(length=20), server_default="info", nullable=False),
        sa.Column("event_type", sa.String(length=80), nullable=False),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column(
            "counters_json",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default="{}",
            nullable=False,
        ),
        sa.Column(
            "trace_json",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default="{}",
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["literature_run_id"],
            ["literature_runs.id"],
            name="fk_literature_run_events_literature_run_id_literature_runs",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_literature_run_events"),
    )
    op.create_index(
        "ix_literature_run_events_run_created",
        "literature_run_events",
        ["literature_run_id", "created_at"],
        unique=False,
    )

    op.create_table(
        "literature_run_papers",
        sa.Column("literature_run_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("paper_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("direction_id", sa.String(length=40), nullable=False),
        sa.Column("direction_title", sa.Text(), nullable=True),
        sa.Column("search_query", sa.Text(), nullable=False),
        sa.Column("candidate_rank", sa.Integer(), nullable=True),
        sa.Column("selected_rank", sa.Integer(), nullable=True),
        sa.Column("score", sa.BigInteger(), nullable=True),
        sa.Column(
            "score_detail_json",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default="{}",
            nullable=False,
        ),
        sa.Column("selected", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column(
            "analysis_status", sa.String(length=40), server_default="pending", nullable=False
        ),
        sa.Column("abstract_zh", sa.Text(), nullable=True),
        sa.Column("match_how", sa.Text(), nullable=True),
        sa.Column("match_use", sa.Text(), nullable=True),
        sa.Column("conclusion", sa.Text(), nullable=True),
        sa.Column(
            "analysis_json",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default="{}",
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["literature_run_id"],
            ["literature_runs.id"],
            name="fk_literature_run_papers_literature_run_id_literature_runs",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["paper_id"],
            ["literature_papers.id"],
            name="fk_literature_run_papers_paper_id_literature_papers",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_literature_run_papers"),
        sa.UniqueConstraint(
            "literature_run_id",
            "paper_id",
            "direction_id",
            name="uq_lit_run_papers_run_paper_direction",
        ),
    )
    op.create_index(
        "ix_literature_run_papers_run_selected",
        "literature_run_papers",
        ["literature_run_id", "selected"],
        unique=False,
    )

    op.create_table(
        "literature_artifacts",
        sa.Column("literature_run_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("paper_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("artifact_type", sa.String(length=80), nullable=False),
        sa.Column(
            "json_data",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default="{}",
            nullable=False,
        ),
        sa.Column("text_data", sa.Text(), nullable=True),
        sa.Column("binary_data", sa.LargeBinary(), nullable=True),
        sa.Column("mime_type", sa.String(length=255), nullable=True),
        sa.Column("byte_size", sa.BigInteger(), nullable=True),
        sa.Column("sha256", sa.String(length=64), nullable=True),
        sa.Column("source_url", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.CheckConstraint(
            "byte_size IS NULL OR byte_size >= 0",
            name="ck_literature_artifacts_byte_size_non_negative",
        ),
        sa.ForeignKeyConstraint(
            ["literature_run_id"],
            ["literature_runs.id"],
            name="fk_literature_artifacts_literature_run_id_literature_runs",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["paper_id"],
            ["literature_papers.id"],
            name="fk_literature_artifacts_paper_id_literature_papers",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_literature_artifacts"),
    )
    op.create_index(
        "ix_literature_artifacts_run_kind",
        "literature_artifacts",
        ["literature_run_id", "artifact_type"],
        unique=False,
    )
    op.create_index(
        "ix_literature_artifacts_paper_kind",
        "literature_artifacts",
        ["paper_id", "artifact_type"],
        unique=False,
    )

    op.create_table(
        "literature_evidence",
        sa.Column("literature_run_paper_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("matched_term", sa.Text(), nullable=False),
        sa.Column("match_level", sa.String(length=40), nullable=False),
        sa.Column("evidence_text", sa.Text(), server_default="", nullable=False),
        sa.Column("evidence_source", sa.String(length=40), server_default="", nullable=False),
        sa.Column("page_number", sa.Integer(), nullable=True),
        sa.Column("section_name", sa.String(length=120), nullable=True),
        sa.Column("char_start", sa.Integer(), nullable=True),
        sa.Column("char_end", sa.Integer(), nullable=True),
        sa.Column("verified", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["literature_run_paper_id"],
            ["literature_run_papers.id"],
            name="fk_lit_evidence_run_paper",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_literature_evidence"),
    )
    op.create_index(
        "ix_literature_evidence_run_paper",
        "literature_evidence",
        ["literature_run_paper_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_table("literature_evidence")
    op.drop_table("literature_artifacts")
    op.drop_table("literature_run_papers")
    op.drop_table("literature_run_events")
    op.drop_table("literature_papers")
    op.drop_table("literature_runs")
