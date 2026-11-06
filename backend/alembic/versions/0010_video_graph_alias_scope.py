"""Scope contextual entity aliases without rewriting legacy identities."""

import sqlalchemy as sa
from alembic import op

revision = "0010_video_graph_alias_scope"
down_revision = "0009_supplier_multibrand"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "video_graph_aliases",
        sa.Column("scope_key", sa.String(80), nullable=False, server_default=""),
    )
    op.drop_constraint(
        op.f("uq_video_graph_aliases_entity_type_normalized_alias"),
        "video_graph_aliases",
        type_="unique",
    )
    op.create_unique_constraint(
        op.f("uq_video_graph_alias_scope_entity"),
        "video_graph_aliases",
        ["entity_type", "normalized_alias", "scope_key", "entity_id"],
    )


def downgrade():
    # Contextual aliases cannot be represented by the old schema. Preserve legacy
    # aliases and the entities/evidence; only discard the v2 alias projections.
    op.execute("DELETE FROM video_graph_aliases WHERE scope_key <> ''")
    op.drop_constraint(
        op.f("uq_video_graph_alias_scope_entity"), "video_graph_aliases", type_="unique"
    )
    op.create_unique_constraint(
        op.f("uq_video_graph_aliases_entity_type_normalized_alias"),
        "video_graph_aliases",
        ["entity_type", "normalized_alias"],
    )
    op.drop_column("video_graph_aliases", "scope_key")
