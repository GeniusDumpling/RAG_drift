"""add supplier relation and verification tables

Revision ID: 0003_supplier_relations
Revises: 0002_literature_research
Create Date: 2026-09-09 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0003_supplier_relations"
down_revision: str | None = "0002_literature_research"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "supplier_relations",
        sa.Column("supplier_name", sa.String(length=255), nullable=False),
        sa.Column("supply_content", sa.Text(), nullable=True),
        sa.Column("buyer_name", sa.String(length=120), server_default="大疆", nullable=False),
        sa.Column("credibility", sa.String(length=20), server_default="明确", nullable=False),
        sa.Column("seen_count", sa.Integer(), server_default="1", nullable=False),
        sa.Column(
            "source_urls",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default="[]",
            nullable=False,
        ),
        sa.Column("verify_status", sa.String(length=20), server_default="未验证", nullable=False),
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
        sa.CheckConstraint(
            "credibility IN ('明确','疑似')",
            name=op.f("ck_supplier_relations_credibility_valid"),
        ),
        sa.CheckConstraint(
            "verify_status IN ('未验证','已验证','验证失败')",
            name=op.f("ck_supplier_relations_verify_status_valid"),
        ),
        sa.CheckConstraint(
            "seen_count >= 0", name=op.f("ck_supplier_relations_seen_count_non_negative")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_supplier_relations")),
    )
    op.create_table(
        "supplier_verifications",
        sa.Column("relation_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("supplier_name", sa.String(length=255), nullable=False),
        sa.Column("verdict", sa.String(length=20), nullable=False),
        sa.Column("confidence", sa.String(length=20), nullable=True),
        sa.Column("supply_content", sa.Text(), nullable=True),
        sa.Column("evidence_md", sa.Text(), nullable=True),
        sa.Column(
            "evidence_urls",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default="[]",
            nullable=False,
        ),
        sa.Column(
            "orig_source_urls",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default="[]",
            nullable=False,
        ),
        sa.Column("verify_time", sa.DateTime(timezone=True), nullable=True),
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
        sa.CheckConstraint(
            "verdict IN ('确认','否定','待确认')",
            name=op.f("ck_supplier_verifications_verdict_valid"),
        ),
        sa.CheckConstraint(
            "confidence IS NULL OR confidence IN ('明确','疑似','不相关')",
            name=op.f("ck_supplier_verifications_confidence_valid"),
        ),
        sa.ForeignKeyConstraint(
            ["relation_id"],
            ["supplier_relations.id"],
            name=op.f("fk_supplier_verifications_relation_id_supplier_relations"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_supplier_verifications")),
    )
    op.create_index(
        op.f("ix_supplier_relations_supplier_name"),
        "supplier_relations",
        ["supplier_name"],
        unique=False,
    )
    op.create_index(
        op.f("ix_supplier_relations_verify_status"),
        "supplier_relations",
        ["verify_status"],
        unique=False,
    )
    op.create_index(
        op.f("ix_supplier_relations_buyer_name"),
        "supplier_relations",
        ["buyer_name"],
        unique=False,
    )
    op.create_index(
        op.f("ix_supplier_verifications_relation_id"),
        "supplier_verifications",
        ["relation_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_supplier_verifications_supplier_name"),
        "supplier_verifications",
        ["supplier_name"],
        unique=False,
    )
    op.create_index(
        op.f("ix_supplier_verifications_verdict"),
        "supplier_verifications",
        ["verdict"],
        unique=False,
    )
    op.create_index(
        op.f("ix_supplier_verifications_verify_time"),
        "supplier_verifications",
        ["verify_time"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_table("supplier_verifications")
    op.drop_table("supplier_relations")