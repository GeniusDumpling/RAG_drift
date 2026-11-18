"""Read-only forum graph APIs, grounded in current business source rows."""

from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from app.core.config import get_settings
from app.services.forum_graph import queries
from app.services.forum_graph.storage import ForumGraphStore

router = APIRouter(prefix="/forum-graph", tags=["forum-graph"])


async def _query(callback):
    def run():
        settings = get_settings()
        if not settings.forum_graph_enabled:
            raise HTTPException(status_code=503, detail="论坛图谱尚未启用")
        engine = create_engine(settings.sync_database_url, pool_pre_ping=True)
        try:
            with ForumGraphStore(settings) as store, Session(engine) as session:
                return callback(store, session)
        except Exception as exc:
            raise HTTPException(status_code=503, detail="论坛图谱或当前来源证据暂不可用") from exc
        finally:
            engine.dispose()

    return await run_in_threadpool(run)


@router.get("/retrieve")
async def retrieve(
    q: str = Query(default="", max_length=1000),
    source_id: str | None = None,
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
):
    return await _query(
        lambda store, session: queries.retrieve(
            store,
            session,
            q,
            source_id=source_id,
            limit=limit,
            offset=offset,
        )
    )


@router.get("/verify")
async def verify(source_id: str = Query(min_length=1, max_length=300)):
    return await _query(lambda store, session: queries.verify(store, session, source_id))


@router.get("/status")
async def status(source_id: str = Query(min_length=1, max_length=300)):
    return await _query(lambda store, session: queries.status_summary(store, source_id))
