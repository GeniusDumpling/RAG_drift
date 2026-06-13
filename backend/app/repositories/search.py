from typing import Any
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.search import AgentCall


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
