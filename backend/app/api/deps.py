from collections.abc import AsyncIterator

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.session import AsyncSessionLocal, get_db_session


async def get_session() -> AsyncIterator[AsyncSession]:
    async for session in get_db_session():
        yield session


def get_session_factory() -> async_sessionmaker[AsyncSession]:
    """A stream owns its session after FastAPI's request dependencies are closed."""
    return AsyncSessionLocal
