from collections.abc import AsyncIterator

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.config import get_settings

engine = create_async_engine(get_settings().resolved_literature_database_url, pool_pre_ping=True)
LiteratureSessionLocal = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)


async def get_literature_session() -> AsyncIterator[AsyncSession]:
    async with LiteratureSessionLocal() as session:
        yield session
