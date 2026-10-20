"""Allow publishing validated chunks while retaining skipped-chunk diagnostics."""

from alembic import op

revision = "0006_video_graph_partial_success"
down_revision = "0005_video_knowledge_graph"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_constraint(
        op.f("ck_video_graph_builds_status_valid"), "video_graph_builds", type_="check"
    )
    op.create_check_constraint(
        op.f("ck_video_graph_builds_status_valid"), "video_graph_builds",
        "status IN ('pending','running','success','partial_success','failed','obsolete')",
    )


def downgrade() -> None:
    op.execute("UPDATE video_graph_builds SET status = 'failed' WHERE status = 'partial_success'")
    op.drop_constraint(
        op.f("ck_video_graph_builds_status_valid"), "video_graph_builds", type_="check"
    )
    op.create_check_constraint(
        op.f("ck_video_graph_builds_status_valid"), "video_graph_builds",
        "status IN ('pending','running','success','failed','obsolete')",
    )
