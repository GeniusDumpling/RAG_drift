"""Preserve drone models, dated evidence and verification execution status."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0009_supplier_multibrand"
down_revision = "0008_literature_database_split"
branch_labels = None
depends_on = None


def upgrade():
    for table in ("supplier_relations", "supplier_verifications"):
        for name in ("drone_models", "evidence_years"):
            op.add_column(
                table,
                sa.Column(
                    name, postgresql.JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")
                ),
            )
    op.add_column(
        "supplier_relations",
        sa.Column(
            "evidence_items",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
    )
    op.add_column(
        "supplier_verifications",
        sa.Column("execution_status", sa.String(20), nullable=False, server_default="success"),
    )


def downgrade():
    op.drop_column("supplier_verifications", "execution_status")
    op.drop_column("supplier_relations", "evidence_items")
    for table in ("supplier_relations", "supplier_verifications"):
        for name in ("drone_models", "evidence_years"):
            op.drop_column(table, name)
