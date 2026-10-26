import app.models  # noqa: F401
from app.api.deps import get_session
from app.core.config import Settings
from app.db.base import Base, LiteratureBase
from app.db.literature import get_literature_session
from app.main import create_app
from app.repositories.database import list_table_metadata
from sqlalchemy.engine import make_url

LITERATURE_TABLES = {
    "literature_runs",
    "literature_papers",
    "literature_run_papers",
    "literature_run_events",
    "literature_artifacts",
    "literature_evidence",
}


def test_literature_url_uses_separate_database_and_preserves_encoded_credentials() -> None:
    settings = Settings(
        _env_file=None,
        DATABASE_URL="postgresql+psycopg://reader:p%40ss@db:5432/main",
        SYNC_DATABASE_URL="postgresql+psycopg://writer:p%25ss@db:5432/main",
    )
    async_url = make_url(settings.resolved_literature_database_url)
    sync_url = make_url(settings.resolved_literature_sync_database_url)
    assert async_url.database == sync_url.database == "literature_research"
    assert async_url.host == sync_url.host == "db"
    assert async_url.password == "p@ss"
    assert sync_url.password == "p%ss"
    assert make_url(settings.database_url).database == "main"


def test_explicit_literature_connection_is_used_for_both_session_types() -> None:
    url = "postgresql+psycopg://literature:secret@other:5433/papers"
    settings = Settings(_env_file=None, LITERATURE_DATABASE_URL=url)
    assert settings.resolved_literature_database_url == url
    assert settings.resolved_literature_sync_database_url == url


def test_main_schema_and_browser_do_not_query_moved_tables() -> None:
    assert set(LiteratureBase.metadata.tables) == LITERATURE_TABLES
    assert not set(Base.metadata.tables) & LITERATURE_TABLES
    assert not {table.table_name for table in list_table_metadata()} & LITERATURE_TABLES
    for table in LiteratureBase.metadata.tables.values():
        assert all(fk.column.table.metadata is LiteratureBase.metadata for fk in table.foreign_keys)


def test_every_literature_endpoint_uses_the_dedicated_session() -> None:
    routes = [
        route
        for route in create_app().routes
        if getattr(route, "path", "").startswith("/literature-")
    ]
    assert len(routes) == 8
    for route in routes:
        dependencies = {dependency.call for dependency in route.dependant.dependencies}
        assert get_literature_session in dependencies
        assert get_session not in dependencies
