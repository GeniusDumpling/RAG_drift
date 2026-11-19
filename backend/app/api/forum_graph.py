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


async def _display(callback):
    from app.services.forum_graph.display import ForumDisplay

    return await _query(lambda store, session: callback(ForumDisplay(store, session)))


@router.get("/overview")
async def overview():
    return await _display(lambda view: view.overview())


@router.get("/entities")
async def entities(
    q: str = Query(default="", max_length=1000),
    entity_type: str | None = None,
    limit: int = Query(default=50, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
):
    return await _display(lambda view: view.list_entities(q, entity_type, limit, offset))


@router.get("/graph")
async def graph(
    q: str = Query(default="", max_length=1000),
    entity_id: str | None = None,
    entity_type: str | None = None,
    hops: int = Query(default=1, ge=1, le=2),
    max_nodes: int = Query(default=300, ge=2, le=500),
    max_edges: int = Query(default=500, ge=1, le=1000),
    full: bool = False,
    min_component_size: int = Query(default=0, ge=0, le=50),
):
    return await _display(
        lambda view: view.graph(
            q=q,
            entity_id=entity_id,
            entity_type=entity_type,
            hops=hops,
            max_nodes=max_nodes,
            max_edges=max_edges,
            full=full,
            min_component_size=min_component_size,
        )
    )


@router.get("/facts/{fact_id}/evidence")
async def fact_evidence(
    fact_id: str, limit: int = Query(default=20, ge=1, le=100), offset: int = Query(default=0, ge=0)
):
    return await _display(lambda view: view.evidence(fact_id, limit=limit, offset=offset))
