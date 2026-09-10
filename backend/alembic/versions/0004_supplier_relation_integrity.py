"""enforce supplier relationship integrity

Revision ID: 0004_supplier_relation_integrity
Revises: 0003_supplier_relations
Create Date: 2026-09-09 00:00:00.000000
"""

from collections.abc import Sequence

from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0004_supplier_relation_integrity"
down_revision: str | None = "0003_supplier_relations"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_unique_constraint(
        "uq_supplier_relations_buyer_name_supplier_name",
        "supplier_relations",
        ["buyer_name", "supplier_name"],
    )
    op.alter_column(
        "supplier_verifications",
        "relation_id",
        existing_type=postgresql.UUID(as_uuid=True),
        nullable=False,
    )
    op.drop_constraint(
        "fk_supplier_verifications_relation_id_supplier_relations",
        "supplier_verifications",
        type_="foreignkey",
    )
    op.create_foreign_key(
        "fk_supplier_verifications_relation_id_supplier_relations",
        "supplier_verifications",
        "supplier_relations",
        ["relation_id"],
        ["id"],
        ondelete="RESTRICT",
    )


def downgrade() -> None:
    op.drop_constraint(
        "fk_supplier_verifications_relation_id_supplier_relations",
        "supplier_verifications",
        type_="foreignkey",
    )
    op.create_foreign_key(
        "fk_supplier_verifications_relation_id_supplier_relations",
        "supplier_verifications",
        "supplier_relations",
        ["relation_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.alter_column(
        "supplier_verifications",
        "relation_id",
        existing_type=postgresql.UUID(as_uuid=True),
        nullable=True,
    )
    op.drop_constraint(
        "uq_supplier_relations_buyer_name_supplier_name",
        "supplier_relations",
        type_="unique",
    )