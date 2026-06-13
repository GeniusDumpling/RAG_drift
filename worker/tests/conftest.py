import asyncio
import os
from collections.abc import Iterator

import pytest
from sqlalchemy import create_engine
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

DEFAULT_TEST_DATABASE_URL = (
    "postgresql+psycopg://intelligence:intelligence@localhost:54329/intelligence_rag_test"
)


def _get_test_database_url() -> str:
    return os.environ.get("TEST_DATABASE_URL", DEFAULT_TEST_DATABASE_URL)


def _assert_destructive_test_database_is_allowed(database_url: str) -> None:
    if os.environ.get("ALLOW_DESTRUCTIVE_TEST_DB") == "1":
        return

    database_name = make_url(database_url).database
    if database_name is not None and database_name.endswith("_test"):
        return

    raise RuntimeError(
        "Refusing to reset test database because TEST_DATABASE_URL does not target a "
        f"database ending in '_test' (got {database_name!r}). Set "
        "ALLOW_DESTRUCTIVE_TEST_DB=1 to override."
    )


TEST_DATABASE_URL = _get_test_database_url()
_assert_destructive_test_database_is_allowed(TEST_DATABASE_URL)
os.environ["DATABASE_URL"] = TEST_DATABASE_URL
os.environ["SYNC_DATABASE_URL"] = TEST_DATABASE_URL

import app.db.session as db_session_module  # noqa: E402
import app.models  # noqa: E402,F401
from app.core.config import get_settings  # noqa: E402
from app.db.base import Base  # noqa: E402

get_settings.cache_clear()
old_engine = getattr(db_session_module, "engine", None)
if old_engine is not None:
    asyncio.run(old_engine.dispose())

db_session_module.settings = get_settings()
db_session_module.engine = create_async_engine(TEST_DATABASE_URL, pool_pre_ping=True)
db_session_module.AsyncSessionLocal = async_sessionmaker(
    db_session_module.engine, expire_on_commit=False, class_=AsyncSession
)


@pytest.fixture(autouse=True)
def reset_worker_database() -> Iterator[None]:
    engine = create_engine(TEST_DATABASE_URL, pool_pre_ping=True)
    with engine.begin() as connection:
        Base.metadata.drop_all(connection)
        Base.metadata.create_all(connection)
    engine.dispose()
    yield
