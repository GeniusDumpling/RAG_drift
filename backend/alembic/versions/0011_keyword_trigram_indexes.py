"""Index exact keyword regex candidates without blocking ingestion writes."""

from alembic import op
from sqlalchemy import text

revision = "0011_keyword_trigram_indexes"
down_revision = "0010_video_graph_alias_scope"
branch_labels = None
depends_on = None

INDEXES = (
    ("ix_content_items_title_trgm", "content_items", "title"),
    ("ix_content_items_summary_trgm", "content_items", "summary_text"),
    ("ix_content_items_tags_trgm", "content_items", "(tags::varchar)"),
    ("ix_content_chunks_display_trgm", "content_chunks", "display_text"),
    ("ix_content_chunks_embed_trgm", "content_chunks", "embed_text"),
)


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")
    with op.get_context().autocommit_block():
        for name, table, column in INDEXES:
            valid = op.get_bind().execute(text(
                "SELECT indisvalid FROM pg_index WHERE indexrelid=to_regclass(:name)"
            ), {"name": name}).scalar()
            if valid is False:
                op.execute(f"DROP INDEX CONCURRENTLY {name}")
            op.execute(
                f"CREATE INDEX CONCURRENTLY IF NOT EXISTS {name} "
                f"ON {table} USING gin ({column} gin_trgm_ops)"
            )


def downgrade() -> None:
    with op.get_context().autocommit_block():
        for name, _, _ in reversed(INDEXES):
            op.execute(f"DROP INDEX CONCURRENTLY IF EXISTS {name}")
    # pg_trgm may also be used by independent application indexes.
