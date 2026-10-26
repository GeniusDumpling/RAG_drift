import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import create_engine
from sqlalchemy.exc import ProgrammingError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from app.api.deps import get_session
from app.core.config import get_settings
from app.schemas.video_graph import (
    Assertion,
    EntityType,
    GraphData,
    GraphRetrieveRequest,
    GraphRetrieveResponse,
)
from app.services import video_graph_queries as queries

router = APIRouter(prefix="/video-graph", tags=["video-graph"])
SessionDep = Annotated[AsyncSession, Depends(get_session)]


async def _query(session: AsyncSession, callback: Any) -> Any:
    try:
        settings = get_settings()
        if settings.video_graph_backend == "neo4j":

            def query_neo4j():
                from neo4j.exceptions import Neo4jError, ServiceUnavailable

                from app.services.neo4j_graph.queries import GraphQueries
                from app.services.neo4j_graph.store import GraphStore

                engine = create_engine(settings.sync_database_url, pool_pre_ping=True)
                try:
                    with GraphStore(settings) as store, Session(engine) as source:
                        store.ensure_ready()
                        return callback(source, GraphQueries(store))
                except (Neo4jError, ServiceUnavailable, ValueError) as exc:
                    raise HTTPException(
                        status_code=503, detail="Neo4j 图谱不可用，请检查连接及迁移状态"
                    ) from exc
                finally:
                    engine.dispose()

            return await run_in_threadpool(query_neo4j)
        return await session.run_sync(lambda sync: callback(sync, queries))
    except ProgrammingError as exc:
        if getattr(exc.orig, "sqlstate", None) == "42P01":
            await session.rollback()
            raise HTTPException(
                status_code=503, detail="视频知识图谱尚未初始化，请执行 alembic upgrade head"
            ) from exc
        raise


@router.get("/overview")
async def overview(session: SessionDep) -> Any:
    return await _query(session, lambda sync, service: service.overview(sync))


@router.get("/entities")
async def entities(
    session: SessionDep,
    q: str = "",
    entity_type: EntityType | None = None,
    content_item_id: uuid.UUID | None = None,
    limit: int = Query(default=50, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
) -> Any:
    return await _query(
        session,
        lambda sync, service: service.list_entities(
            sync,
            q=q,
            entity_type=entity_type,
            content_id=content_item_id,
            limit=limit,
            offset=offset,
        ),
    )


@router.get("/graph", response_model=GraphData)
async def graph(
    session: SessionDep,
    entity_id: uuid.UUID | None = None,
    content_item_id: uuid.UUID | None = None,
    q: str = "",
    entity_type: EntityType | None = None,
    assertion: Assertion | None = None,
    hops: int = Query(default=1, ge=1, le=2),
    max_nodes: int = Query(default=300, ge=2, le=500),
    max_edges: int = Query(default=500, ge=1, le=1000),
    full: bool = Query(default=False, description="返回当前筛选范围内的完整有效图谱，不截断数量"),
) -> Any:
    return await _query(
        session,
        lambda sync, service: service.get_graph(
            sync,
            entity_id=entity_id,
            content_id=content_item_id,
            q=q,
            entity_type=entity_type,
            assertion=assertion,
            hops=hops,
            max_nodes=None if full else max_nodes,
            max_edges=None if full else max_edges,
        ),
    )


@router.get("/relations/{relation_id}/evidence")
async def evidence(
    relation_id: uuid.UUID,
    session: SessionDep,
    content_item_id: uuid.UUID | None = None,
    assertion: Assertion | None = None,
    limit: int = Query(default=50, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
) -> Any:
    return await _query(
        session,
        lambda sync, service: service.relation_evidence(
            sync,
            relation_id,
            content_id=content_item_id,
            assertion=assertion,
            limit=limit,
            offset=offset,
        ),
    )


@router.get("/videos/{content_id}/status")
async def video_status(content_id: uuid.UUID, session: SessionDep) -> Any:
    result = await _query(session, lambda sync, service: service.video_status(sync, content_id))
    if result is None:
        raise HTTPException(status_code=404, detail="找不到视频")
    return result


@router.post("/retrieve", response_model=GraphRetrieveResponse)
async def retrieve(payload: GraphRetrieveRequest, session: SessionDep) -> Any:
    return await _query(session, lambda sync, service: service.retrieve_context(sync, payload))
