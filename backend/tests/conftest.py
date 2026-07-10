import os
from collections.abc import AsyncIterator, Iterator

import app.models  # noqa: F401
import pytest
from app.api.deps import get_session
from app.db.base import Base
from app.main import app as fastapi_app
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


@pytest.fixture(scope="session", autouse=True)
def _ensure_test_infra() -> Iterator[None]:
    # Tests require the local PostgreSQL service on localhost:54329 by default.
    yield


@pytest.fixture()
async def db_session() -> AsyncIterator[AsyncSession]:
    database_url = _get_test_database_url()
    _assert_destructive_test_database_is_allowed(database_url)

    engine = create_async_engine(database_url, pool_pre_ping=True)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
    session_factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    async with session_factory() as session:
        yield session
    await engine.dispose()


@pytest.fixture(autouse=True)
def override_session(request: pytest.FixtureRequest) -> Iterator[None]:
    if request.node.get_closest_marker("no_db") is not None:
        yield
        return

    db_session = request.getfixturevalue("db_session")

    async def _override() -> AsyncIterator[AsyncSession]:
        yield db_session

    fastapi_app.dependency_overrides[get_session] = _override
    yield
    fastapi_app.dependency_overrides.clear()
