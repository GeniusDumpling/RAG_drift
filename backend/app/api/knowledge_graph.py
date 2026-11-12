"""Unified video/document graph retrieval with per-source evidence."""

from fastapi import APIRouter, HTTPException, Query
from starlette.concurrency import run_in_threadpool

from app.core.config import get_settings
from app.services.knowledge_graph import queries
from app.services.knowledge_graph.storage import UnifiedGraphStore

router = APIRouter(prefix="/knowledge-graph", tags=["knowledge-graph"])


async def _query(callback):
    def run():
        try:
            with UnifiedGraphStore(get_settings()) as store:
                return callback(store)
        except Exception as exc:
            raise HTTPException(status_code=503, detail="统一图谱或来源证据暂不可用") from exc

    return await run_in_threadpool(run)


@router.get("/sources")
async def sources(source_type: str | None = None):
    return await _query(lambda store: dict(items=queries.sources(store, source_type)))


@router.get("/verify")
async def verify(source_id: str = Query(min_length=1, max_length=300)):
    return await _query(lambda store: queries.verify(store, source_id))


@router.get("/retrieve")
async def retrieve(
    q: str = Query(default="", max_length=1000),
    source_type: str | None = None,
    source_id: str | None = None,
    entity_id: str | None = None,
    predicate: str | None = None,
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
):
    return await _query(
        lambda store: queries.retrieve(
            store,
            q,
            source_type=source_type,
            source_id=source_id,
            entity_id=entity_id,
            predicate=predicate,
            limit=limit,
            offset=offset,
        )
    )
