"""Explicit database allowlist and bounded, read-only connections for the admin UI."""

from collections.abc import AsyncIterator
from dataclasses import dataclass
from functools import lru_cache
from typing import Annotated

from fastapi import Depends, HTTPException
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.api.deps import get_session
from app.core.config import get_settings


@dataclass(frozen=True)
class DatabaseTarget:
    id: str
    label: str
    url: str
    collection: str | None = None


def database_targets() -> dict[str, DatabaseTarget]:
    s = get_settings()
    documents = make_url(s.document_database_url or s.database_url).set(
        drivername="postgresql+psycopg"
    )
    if not s.document_database_url:
        documents = documents.set(database="drone_documents")
    return {
        "primary": DatabaseTarget("primary", "业务数据库", s.database_url, s.qdrant_collection),
        "literature": DatabaseTarget(
            "literature", "文献数据库", s.resolved_literature_database_url
        ),
        "documents": DatabaseTarget(
            "documents",
            "文档数据库",
            documents.render_as_string(hide_password=False),
            s.document_qdrant_collection,
        ),
    }


def get_target(database: str) -> DatabaseTarget:
    target = database_targets().get(database)
    if target is None:
        raise HTTPException(404, "Database not found")
    return target


@lru_cache(maxsize=4)
def readonly_factory(url: str) -> async_sessionmaker[AsyncSession]:
    engine = create_async_engine(
        url,
        poolclass=NullPool,
        connect_args={
            "connect_timeout": 5,
            "options": (
                "-c default_transaction_read_only=on "
                "-c statement_timeout=10000 -c lock_timeout=1000"
            ),
        },
    )
    return async_sessionmaker(engine, expire_on_commit=False)


async def get_browser_session(
    session: Annotated[AsyncSession, Depends(get_session)], database: str = "primary"
) -> AsyncIterator[AsyncSession]:
    target = get_target(database)
    if database == "primary":
        yield session
    else:
        async with readonly_factory(target.url)() as scoped:
            yield scoped


async def get_document_session() -> AsyncIterator[AsyncSession]:
    async with readonly_factory(get_target("documents").url)() as session:
        yield session


async def checked_execute(session: AsyncSession, statement: str, params: dict | None = None):
    """Do not expose connection strings, server filesystem paths or SQL errors to clients."""
    from sqlalchemy.exc import SQLAlchemyError

    try:
        return await session.execute(text(statement), params or {})
    except SQLAlchemyError as exc:
        raise HTTPException(503, "数据库暂不可用，请稍后刷新。") from exc
