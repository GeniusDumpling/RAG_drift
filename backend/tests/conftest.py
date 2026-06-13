from collections.abc import AsyncIterator, Iterator

import app.models  # noqa: F401
import pytest
from app.api.deps import get_session
from app.db.base import Base
from app.main import app as fastapi_app
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

TEST_DATABASE_URL = "postgresql+psycopg://intelligence:intelligence@localhost:54329/intelligence_rag"


@pytest.fixture(scope="session", autouse=True)
def _ensure_test_infra() -> Iterator[None]:
    # Tests require the local PostgreSQL service on localhost:54329.
    yield


@pytest.fixture()
async def db_session() -> AsyncIterator[AsyncSession]:
    engine = create_async_engine(TEST_DATABASE_URL, pool_pre_ping=True)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
    session_factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    async with session_factory() as session:
        yield session
    await engine.dispose()


@pytest.fixture(autouse=True)
def override_session(db_session: AsyncSession) -> Iterator[None]:
    async def _override() -> AsyncIterator[AsyncSession]:
        yield db_session

    fastapi_app.dependency_overrides[get_session] = _override
    yield
    fastapi_app.dependency_overrides.clear()
