from typing import Any, cast
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.contracts import QueryOptimizationResponse
from app.models.search import AgentCall, SearchQuery


class SearchRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def create_search_query(
        self,
        *,
        raw_query: str,
        filter_json: dict[str, Any],
        mode: str,
        top_k: int,
    ) -> SearchQuery:
        search_query = SearchQuery(
            raw_query=raw_query,
            filter_json=filter_json,
            mode=mode,
            top_k=top_k,
            used_agent=False,
            optimized_query_text=None,
            keyword_terms_json=[],
            entity_hints_json=[],
            time_hints_json={},
            result_summary_json={},
            query_trace_json={},
            result_count=None,
        )
        self.session.add(search_query)
        await self.session.commit()
        await self.session.refresh(search_query)
        return search_query

    async def update_search_query_optimization(
        self,
        *,
        search_query_id: UUID,
        optimized: QueryOptimizationResponse,
        used_agent: bool,
        trace: dict[str, Any],
    ) -> SearchQuery:
        search_query = await self.get_search_query(search_query_id)
        if search_query is None:
            raise ValueError(f"Search query not found: {search_query_id}")

        search_query.optimized_query_text = optimized.optimized_query_text
        search_query.keyword_terms_json = list(optimized.keyword_terms)
        search_query.entity_hints_json = list(optimized.entity_hints)
        search_query.time_hints_json = dict(optimized.time_hints_json)
        search_query.used_agent = used_agent
        search_query.query_trace_json = dict(trace)
        search_query.result_summary_json = {
            "query_intent": optimized.query_intent,
            "confidence": optimized.confidence,
        }
        await self.session.commit()
        await self.session.refresh(search_query)
        return search_query

    async def update_search_query_retrieval(
        self,
        *,
        search_query_id: UUID,
        result_count: int,
        retrieval_trace: dict[str, Any],
    ) -> SearchQuery:
        search_query = await self.get_search_query(search_query_id)
        if search_query is None:
            raise ValueError(f"Search query not found: {search_query_id}")

        search_query.query_trace_json = {
            **dict(search_query.query_trace_json),
            "retrieval": dict(retrieval_trace),
        }
        search_query.result_summary_json = {
            **dict(search_query.result_summary_json),
            "result_count": result_count,
            "retrieval": dict(retrieval_trace),
        }
        search_query.result_count = result_count
        await self.session.commit()
        await self.session.refresh(search_query)
        return search_query

    async def get_search_query(self, search_query_id: UUID) -> SearchQuery | None:
        return cast(
            SearchQuery | None,
            await self.session.scalar(select(SearchQuery).where(SearchQuery.id == search_query_id)),
        )


async def create_search_query(
    session: AsyncSession,
    *,
    raw_query: str,
    filter_json: dict[str, Any],
    mode: str,
    top_k: int,
) -> SearchQuery:
    return await SearchRepository(session).create_search_query(
        raw_query=raw_query,
        filter_json=filter_json,
        mode=mode,
        top_k=top_k,
    )


async def update_search_query_optimization(
    session: AsyncSession,
    *,
    search_query_id: UUID,
    optimized: QueryOptimizationResponse,
    used_agent: bool,
    trace: dict[str, Any],
) -> SearchQuery:
    return await SearchRepository(session).update_search_query_optimization(
        search_query_id=search_query_id,
        optimized=optimized,
        used_agent=used_agent,
        trace=trace,
    )


async def update_search_query_retrieval(
    session: AsyncSession,
    *,
    search_query_id: UUID,
    result_count: int,
    retrieval_trace: dict[str, Any],
) -> SearchQuery:
    return await SearchRepository(session).update_search_query_retrieval(
        search_query_id=search_query_id,
        result_count=result_count,
        retrieval_trace=retrieval_trace,
    )


async def get_search_query(session: AsyncSession, search_query_id: UUID) -> SearchQuery | None:
    return await SearchRepository(session).get_search_query(search_query_id)


async def create_agent_call(
    session: AsyncSession,
    *,
    agent_role: str,
    caller: str,
    status: str,
    input_summary_json: dict[str, Any],
    output_summary_json: dict[str, Any],
    related_crawl_run_id: UUID | None = None,
    related_raw_page_id: UUID | None = None,
    related_content_item_id: UUID | None = None,
    related_search_query_id: UUID | None = None,
    latency_ms: int | None = None,
    error_message: str | None = None,
    request_schema_version: str = "agent_call.v1",
    response_schema_version: str = "agent_call.v1",
) -> AgentCall:
    agent_call = AgentCall(
        agent_role=agent_role,
        caller=caller,
        related_crawl_run_id=related_crawl_run_id,
        related_raw_page_id=related_raw_page_id,
        related_content_item_id=related_content_item_id,
        related_search_query_id=related_search_query_id,
        request_schema_version=request_schema_version,
        response_schema_version=response_schema_version,
        status=status,
        input_summary_json=input_summary_json,
        output_summary_json=output_summary_json,
        latency_ms=latency_ms,
        error_message=error_message,
        trace_json={},
    )
    session.add(agent_call)
    await session.commit()
    await session.refresh(agent_call)
    return agent_call
