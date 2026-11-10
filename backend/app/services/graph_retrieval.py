"""Shared entry point for the reserved graph API and answer retrieval."""

import asyncio

from sqlalchemy import create_engine
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.schemas.video_graph import GraphRetrieveRequest, GraphRetrieveResponse


async def retrieve_graph_context(
    session: AsyncSession, request: GraphRetrieveRequest, settings: Settings
) -> GraphRetrieveResponse:
    def retrieve() -> GraphRetrieveResponse:
        from app.services.video_graph.queries import GraphQueries
        from app.services.video_graph.storage import GraphStore

        engine = create_engine(settings.sync_database_url, pool_pre_ping=True)
        try:
            with GraphStore(settings) as store, Session(engine) as source:
                store.ensure_ready()
                queries = GraphQueries(store)  # type: ignore[no-untyped-call]
                result: GraphRetrieveResponse = queries.retrieve_context(  # type: ignore[no-untyped-call]
                    source, request
                )
                return result
        finally:
            engine.dispose()

    return await asyncio.to_thread(retrieve)
