from typing import Any

from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.client import AgentClient
from app.agents.contracts import QueryOptimizationResponse
from app.core.config import Settings, get_settings
from app.repositories.search import SearchRepository
from app.schemas.search import SearchQueryRead, SearchRequest, SearchResponse
from app.services.embeddings import DeterministicEmbeddingService, EmbeddingService
from app.services.retrieval import retrieve_evidence


class UnsupportedSearchModeError(ValueError):
    """Raised when a search-only route is asked to run an unsupported mode."""


class SearchService:
    def __init__(
        self,
        session: AsyncSession,
        *,
        settings: Settings | None = None,
        agent_client: AgentClient | None = None,
        embedding: EmbeddingService | None = None,
    ) -> None:
        self.session = session
        self.settings = settings or get_settings()
        self.agent_client = agent_client or AgentClient(
            provider=self.settings.llm_provider,
            timeout_seconds=self.settings.agent_timeout_seconds,
        )
        self.embedding = embedding or DeterministicEmbeddingService()

    async def search(self, request: SearchRequest) -> SearchResponse:
        if request.mode != "search":
            raise UnsupportedSearchModeError(
                "Use /answer for answer mode when it is available"
            )

        repo = SearchRepository(self.session)
        filter_json = _filter_json(request)
        search_query = await repo.create_search_query(
            raw_query=request.query,
            filter_json=filter_json,
            mode=request.mode,
            top_k=request.top_k,
        )

        optimized = self._optimize_query(
            raw_query=request.query,
            filters=filter_json,
            mode=request.mode,
        )
        trace = dict(optimized.trace_summary_json)
        used_agent = not bool(trace.get("fallback"))
        await repo.update_search_query_optimization(
            search_query_id=search_query.id,
            optimized=optimized,
            used_agent=used_agent,
            trace=trace,
        )

        retrieval_result = await retrieve_evidence(
            self.session,
            raw_query=request.query,
            optimized_query_text=optimized.optimized_query_text,
            keyword_terms=optimized.keyword_terms,
            filters=request.filters,
            top_k=request.top_k,
            qdrant_url=self.settings.qdrant_url,
            qdrant_collection=self.settings.qdrant_collection,
            embedding=self.embedding,
        )
        updated_query = await repo.update_search_query_retrieval(
            search_query_id=search_query.id,
            result_count=len(retrieval_result.evidence),
            retrieval_trace=retrieval_result.trace,
        )
        return SearchResponse(
            query=SearchQueryRead.model_validate(updated_query),
            evidence=retrieval_result.evidence,
        )

    def _optimize_query(
        self,
        *,
        raw_query: str,
        filters: dict[str, Any],
        mode: str,
    ) -> QueryOptimizationResponse:
        try:
            agent_response = self.agent_client.optimize_query(
                raw_query=raw_query,
                filters=filters,
                mode=mode,
            )
            return QueryOptimizationResponse.model_validate(agent_response.model_dump())
        except (RuntimeError, ValidationError, ValueError) as exc:
            return _fallback_optimization(raw_query=raw_query, error_message=str(exc))


def _filter_json(request: SearchRequest) -> dict[str, Any]:
    return request.filters.model_dump(mode="json", exclude_none=True)


def _fallback_optimization(*, raw_query: str, error_message: str) -> QueryOptimizationResponse:
    stripped_query = raw_query.strip()
    optimized_query_text = stripped_query or "[empty query]"
    return QueryOptimizationResponse(
        optimized_query_text=optimized_query_text,
        keyword_terms=[term for term in stripped_query.split() if term],
        entity_hints=[],
        time_hints_json={},
        query_intent="fallback_raw_query",
        confidence=0.0,
        trace_summary_json={"fallback": True, "error": error_message},
    )
