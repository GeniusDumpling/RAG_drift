"""Remove the old literature schema only after its populated tables have been migrated."""

from alembic import op
from sqlalchemy import inspect, text

revision = "0008_literature_database_split"
down_revision = "0007_chat_sessions"
branch_labels = None
depends_on = None

TABLES = (
    "literature_evidence",
    "literature_artifacts",
    "literature_run_papers",
    "literature_run_events",
    "literature_papers",
    "literature_runs",
)


def upgrade() -> None:
    connection = op.get_bind()
    existing = set(inspect(connection).get_table_names(schema="public"))
    tables = [name for name in TABLES if name in existing]
    if not tables:
        return
    # Prevent a writer inserting between the empty-table check and DROP.
    connection.execute(text("SET LOCAL lock_timeout = '10s'"))
    quoted = ", ".join(f'public."{name}"' for name in tables)
    connection.execute(text(f"LOCK TABLE {quoted} IN ACCESS EXCLUSIVE MODE"))
    for name in tables:
        if connection.scalar(text(f'SELECT EXISTS (SELECT 1 FROM public."{name}")')):
            raise RuntimeError(
                "Literature data still exists in the main database. Stop literature writers and "
                "run scripts/migrate_literature_database.py before upgrading. No tables removed."
            )
    for name in tables:
        op.drop_table(name, schema="public")


def downgrade() -> None:
    raise RuntimeError(
        "Literature lives in its own database. Restore the pre-migration dump and the prior "
        "application release to roll back; this revision does not merge databases automatically."
    )
