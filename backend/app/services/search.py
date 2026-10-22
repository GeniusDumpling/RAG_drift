import asyncio
import logging
from collections.abc import AsyncGenerator
from contextlib import aclosing
from time import perf_counter
from typing import Any

import anyio
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.client import AgentClient
from app.agents.contracts import QueryOptimizationResponse
from app.core.config import Settings, get_settings
from app.models.chat import ChatTurn
from app.repositories.chat import ChatRepository
from app.repositories.search import SearchRepository
from app.schemas.search import (
    AnswerRequest,
    AnswerResponse,
    EvidenceObject,
    GenerationInfo,
    SearchQueryRead,
    SearchRequest,
    SearchResponse,
)
from app.services.answer_context import prepare_answer_context
from app.services.answer_generation import AnswerGenerationError, DeepSeekAnswerClient
from app.services.embeddings import EmbeddingService, build_embedding_service
from app.services.retrieval import retrieve_evidence

logger = logging.getLogger(__name__)


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
            settings=self.settings,
        )
        self.embedding = embedding or build_embedding_service(self.settings)

    async def search(self, request: SearchRequest) -> SearchResponse:
        if request.mode != "search":
            raise UnsupportedSearchModeError("Use /answer for answer mode when it is available")

        query, evidence = await self._retrieve(request)
        return SearchResponse(query=query, evidence=evidence)

    async def answer(self, request: AnswerRequest) -> AnswerResponse:
        async with aclosing(self.stream_answer(request)) as events:
            async for event in events:
                if event["event"] == "done":
                    return AnswerResponse.model_validate(event["data"])
        raise RuntimeError("Answer pipeline did not complete")

    async def stream_answer(self, request: AnswerRequest) -> AsyncGenerator[dict[str, Any], None]:
        started = perf_counter()
        repo = SearchRepository(self.session)
        chat = ChatRepository(self.session)
        turn: ChatTurn | None = None
        turn_id = None
        query: SearchQueryRead | None = None
        evidence: list[EvidenceObject] = []
        text = ""
        generation_started: float | None = None
        generation = GenerationInfo(model=self.settings.deepseek_model)
        finished = False
        try:
            history: list[dict[str, str]] = []
            if request.session_id is not None:
                turn = await chat.begin_turn(request.session_id, request.query)
                turn_id = turn.id
                history = await chat.history(request.session_id)
            yield {"event": "status", "data": {"stage": "retrieving"}}
            client = DeepSeekAnswerClient(
                base_url=self.settings.deepseek_base_url,
                api_key=self.settings.deepseek_api_key or "",
                model=self.settings.deepseek_model,
                timeout_seconds=self.settings.agent_timeout_seconds,
            )
            retrieval_query = request.query
            if history:
                if self.settings.deepseek_api_key:
                    try:
                        retrieval_query = await client.rewrite_question(request.query, history)
                        generation.rewrite_status = "rewritten"
                    except Exception:
                        logger.warning("Follow-up rewriting failed", exc_info=True)
                        generation.rewrite_status = "fallback"
                else:
                    generation.rewrite_status = "fallback"
                if generation.rewrite_status == "fallback":
                    # Keep the recent topic searchable even when rewriting is unavailable.
                    previous = [m["content"] for m in history if m["role"] == "user"][-2:]
                    retrieval_query = "\n".join([*previous, request.query])
            generation.retrieval_query = retrieval_query
            query, hits = await self._retrieve(request, retrieval_query=retrieval_query)
            context = await prepare_answer_context(
                self.session, hits, max_evidence=min(request.top_k, 8)
            )
            evidence = context.evidence
            generation.evidence_count = len(evidence)
            generation.context_chars = len(context.text)
            generation.context_chunk_ids = context.chunk_ids
            yield {
                "event": "evidence",
                "data": {
                    "query_id": str(query.id),
                    "turn_id": str(turn.id) if turn else None,
                    "supporting_evidence": [e.model_dump(mode="json") for e in evidence],
                },
            }
            yield {"event": "status", "data": {"stage": "generating"}}
            generation_started = perf_counter()
            if not evidence:
                generation.status = "no_evidence"
                generation.reason = "no_matching_evidence"
            elif not self.settings.deepseek_api_key:
                generation.status = "fallback"
                generation.reason = "missing_api_key"
            else:
                try:
                    async with aclosing(
                        client.stream(
                            request.query,
                            evidence,
                            context=context.text,
                            history=history,
                        )
                    ) as deltas:
                        async for delta in deltas:
                            text += delta
                            yield {"event": "delta", "data": {"text": delta}}
                    if not text.strip():
                        raise AnswerGenerationError("模型返回了空答案")
                except AnswerGenerationError as exc:
                    logger.warning("Answer generation failed", exc_info=True)
                    generation.status = "fallback"
                    cause = exc.__cause__ or exc
                    generation.reason = f"model_error:{type(cause).__name__}"
            if generation.status != "generated":
                text = _synthesize_answer(evidence)
                # Discard any partially emitted model answer before showing fallback excerpts.
                yield {
                    "event": "reset",
                    "data": {
                        "text": text,
                        "generation": generation.model_dump(mode="json"),
                    },
                }
            generation.latency_ms = round((perf_counter() - generation_started) * 1000)
            generation.total_latency_ms = round((perf_counter() - started) * 1000)
            updated = await repo.record_generation(
                query.id,
                answer=text,
                generation=generation.model_dump(mode="json"),
                supporting_evidence=[e.model_dump(mode="json") for e in evidence],
            )
            response = AnswerResponse(
                query=SearchQueryRead.model_validate(updated),
                answer=text,
                supporting_evidence=evidence,
                generation=generation,
                session_id=request.session_id,
                turn_id=turn.id if turn else None,
            )
            if turn is not None:
                await chat.finish_turn(turn, response)
            finished = True
            yield {"event": "done", "data": response.model_dump(mode="json")}
        except (asyncio.CancelledError, GeneratorExit):
            generation.status = "cancelled"
            generation.reason = "client_disconnected"
            raise
        except Exception:
            generation.status = "error"
            generation.reason = "pipeline_error"
            raise
        finally:
            if not finished and (turn is not None or query is not None):
                # Starlette cancels the stream on disconnect; shield the final audit write.
                with anyio.CancelScope(shield=True):
                    try:
                        await self.session.rollback()
                        generation.total_latency_ms = round((perf_counter() - started) * 1000)
                        if generation_started is not None:
                            generation.latency_ms = round(
                                (perf_counter() - generation_started) * 1000
                            )
                        if query is not None:
                            await repo.record_generation(
                                query.id,
                                answer=text,
                                generation=generation.model_dump(mode="json"),
                                supporting_evidence=[e.model_dump(mode="json") for e in evidence],
                            )
                        if turn_id is not None:
                            turn = await self.session.get(ChatTurn, turn_id)
                        if turn is not None:
                            partial = (
                                AnswerResponse(
                                    query=query,
                                    answer=text,
                                    supporting_evidence=evidence,
                                    generation=generation,
                                    session_id=request.session_id,
                                    turn_id=turn_id,
                                )
                                if query is not None
                                else None
                            )
                            await chat.finish_turn(
                                turn,
                                partial,
                                status=generation.status,
                                partial_answer=text,
                                reason=generation.reason,
                            )
                    except Exception:
                        logger.exception("Could not persist interrupted answer")

    async def _retrieve(
        self,
        request: SearchRequest | AnswerRequest,
        *,
        retrieval_query: str | None = None,
    ) -> tuple[SearchQueryRead, list[EvidenceObject]]:
        repo = SearchRepository(self.session)
        filter_json = _filter_json(request)
        search_query = await repo.create_search_query(
            raw_query=request.query,
            filter_json=filter_json,
            mode=request.mode,
            top_k=request.top_k,
        )

        optimized = await asyncio.to_thread(
            self._optimize_query,
            raw_query=retrieval_query or request.query,
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

        # Overfetch for answers so source diversity is decided before the final evidence cap.
        candidate_limit = min(request.top_k * 3, 150) if request.mode == "answer" else request.top_k
        retrieval_result = await retrieve_evidence(
            self.session,
            raw_query=retrieval_query or request.query,
            optimized_query_text=optimized.optimized_query_text,
            keyword_terms=optimized.keyword_terms,
            entity_hints=optimized.entity_hints,
            filters=request.filters,
            top_k=candidate_limit,
            qdrant_url=self.settings.qdrant_url,
            qdrant_collection=self.settings.qdrant_collection,
            embedding=self.embedding,
            max_per_content=2 if request.mode == "answer" else 1,
        )
        updated_query = await repo.update_search_query_retrieval(
            search_query_id=search_query.id,
            result_count=len(retrieval_result.evidence),
            retrieval_trace=retrieval_result.trace,
        )
        return SearchQueryRead.model_validate(updated_query), retrieval_result.evidence

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


def _filter_json(request: SearchRequest | AnswerRequest) -> dict[str, Any]:
    return request.filters.model_dump(mode="json", exclude_none=True)


def _synthesize_answer(evidence: list[EvidenceObject]) -> str:
    if not evidence:
        return "No supported answer found in the indexed sources."
    return " ".join(
        f"[{index}] {evidence_object.snippet}"
        for index, evidence_object in enumerate(evidence, start=1)
    )


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
