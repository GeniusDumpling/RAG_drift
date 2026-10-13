"""Add versioned video knowledge graph and leased build queue."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0005_video_knowledge_graph"
down_revision = "0004_supplier_relation_integrity"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "video_graph_builds",
        sa.Column("content_item_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("fingerprint", sa.String(64), nullable=False),
        sa.Column("extractor_version", sa.String(160), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default=sa.text("'pending'")),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("lease_token", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("lease_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column(
            "output_json", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")
        ),
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.CheckConstraint(
            "attempts >= 0", name=op.f("ck_video_graph_builds_attempts_non_negative")
        ),
        sa.CheckConstraint(
            "status IN ('pending','running','success','failed','obsolete')",
            name=op.f("ck_video_graph_builds_status_valid"),
        ),
        sa.ForeignKeyConstraint(
            ["content_item_id"],
            ["content_items.id"],
            name=op.f("fk_video_graph_builds_content_item_id_content_items"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_video_graph_builds")),
        sa.UniqueConstraint(
            "content_item_id",
            "fingerprint",
            "extractor_version",
            name=op.f("uq_video_graph_builds_content_item_id_fingerprint_extractor_version"),
        ),
    )
    op.create_index(
        "ix_video_graph_builds_queue", "video_graph_builds", ["status", "lease_until", "created_at"]
    )
    op.create_table(
        "video_graph_states",
        sa.Column("content_item_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("source_key", sa.String(512), nullable=False),
        sa.Column("withdrawn", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("active_build_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("desired_build_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.ForeignKeyConstraint(
            ["active_build_id"],
            ["video_graph_builds.id"],
            name=op.f("fk_video_graph_states_active_build_id_video_graph_builds"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["content_item_id"],
            ["content_items.id"],
            name=op.f("fk_video_graph_states_content_item_id_content_items"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["desired_build_id"],
            ["video_graph_builds.id"],
            name=op.f("fk_video_graph_states_desired_build_id_video_graph_builds"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("content_item_id", name=op.f("pk_video_graph_states")),
    )
    op.create_table(
        "video_graph_aliases",
        sa.Column("entity_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("entity_type", sa.String(80), nullable=False),
        sa.Column("surface_text", sa.String(255), nullable=False),
        sa.Column("normalized_alias", sa.String(255), nullable=False),
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["entity_id"],
            ["entities.id"],
            name=op.f("fk_video_graph_aliases_entity_id_entities"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_video_graph_aliases")),
        sa.UniqueConstraint(
            "entity_type",
            "normalized_alias",
            name=op.f("uq_video_graph_aliases_entity_type_normalized_alias"),
        ),
    )
    op.create_table(
        "video_graph_relations",
        sa.Column("relation_key", sa.String(64), nullable=False),
        sa.Column("subject_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("object_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("predicate", sa.String(40), nullable=False),
        sa.Column(
            "qualifiers_json",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.ForeignKeyConstraint(
            ["object_id"], ["entities.id"], name=op.f("fk_video_graph_relations_object_id_entities")
        ),
        sa.ForeignKeyConstraint(
            ["subject_id"],
            ["entities.id"],
            name=op.f("fk_video_graph_relations_subject_id_entities"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_video_graph_relations")),
        sa.UniqueConstraint("relation_key", name=op.f("uq_video_graph_relations_relation_key")),
    )
    op.create_index("ix_video_graph_relations_object", "video_graph_relations", ["object_id"])
    op.create_index("ix_video_graph_relations_subject", "video_graph_relations", ["subject_id"])
    op.create_table(
        "video_graph_evidence",
        sa.Column("build_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("relation_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("content_chunk_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("quote", sa.Text(), nullable=False),
        sa.Column("quote_hash", sa.String(64), nullable=False),
        sa.Column("char_start", sa.Integer(), nullable=False),
        sa.Column("char_end", sa.Integer(), nullable=False),
        sa.Column("start_seconds", sa.Float(), nullable=True),
        sa.Column("end_seconds", sa.Float(), nullable=True),
        sa.Column("assertion", sa.String(20), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("caption_source", sa.String(80), nullable=True),
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.CheckConstraint(
            "assertion IN ('asserted','uncertain','negated')",
            name=op.f("ck_video_graph_evidence_assertion_valid"),
        ),
        sa.CheckConstraint(
            "confidence >= 0 AND confidence <= 1",
            name=op.f("ck_video_graph_evidence_confidence_valid"),
        ),
        sa.ForeignKeyConstraint(
            ["build_id"],
            ["video_graph_builds.id"],
            name=op.f("fk_video_graph_evidence_build_id_video_graph_builds"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["content_chunk_id"],
            ["content_chunks.id"],
            name=op.f("fk_video_graph_evidence_content_chunk_id_content_chunks"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["relation_id"],
            ["video_graph_relations.id"],
            name=op.f("fk_video_graph_evidence_relation_id_video_graph_relations"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_video_graph_evidence")),
        sa.UniqueConstraint(
            "build_id",
            "relation_id",
            "content_chunk_id",
            "quote_hash",
            "assertion",
            name=op.f(
                "uq_video_graph_evidence_build_id_relation_id_content_chunk_id_quote_hash_assertion"
            ),
        ),
    )
    op.create_index("ix_video_graph_evidence_build", "video_graph_evidence", ["build_id"])
    op.create_index("ix_video_graph_evidence_relation", "video_graph_evidence", ["relation_id"])


def downgrade() -> None:
    op.drop_table("video_graph_evidence")
    op.drop_table("video_graph_relations")
    op.drop_table("video_graph_aliases")
    op.drop_table("video_graph_states")
    op.drop_table("video_graph_builds")
