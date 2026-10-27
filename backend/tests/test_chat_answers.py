import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any
from uuid import UUID, uuid4

import httpx
import pytest
from app.api.deps import get_session_factory
from app.core.config import Settings
from app.db.base import utcnow
from app.main import app
from app.models.chat import ChatTurn
from app.models.content import ContentChunk, ContentItem, RawPage
from app.models.control import CrawlJob, CrawlRun, SourceSite
from app.models.search import SearchQuery
from app.repositories.chat import ChatRepository, ChatSessionBusy
from app.schemas.search import AnswerRequest, SearchFilters
from app.services.answer_context import prepare_answer_context
from app.services.answer_generation import AnswerGenerationError, DeepSeekAnswerClient
from app.services.embeddings import SiliconFlowEmbeddingService
from app.services.reranking import SiliconFlowReranker
from app.services.retrieval import retrieve_evidence
from app.services.search import SearchService
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession


@pytest.fixture
async def chat_client(db_session: AsyncSession) -> AsyncIterator[httpx.AsyncClient]:
    @asynccontextmanager
    async def factory() -> AsyncIterator[AsyncSession]:
        yield db_session

    app.dependency_overrides[get_session_factory] = lambda: factory
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        yield client
    app.dependency_overrides.pop(get_session_factory, None)


@pytest.fixture(autouse=True)
def offline_models(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(SiliconFlowEmbeddingService, "embed", lambda self, text: [1.0] * 1024)
    monkeypatch.setattr(
        "app.services.retrieval.QdrantIndexer.search_chunks", lambda self, **kwargs: []
    )


async def seed(session: AsyncSession) -> list[ContentChunk]:
    site = SourceSite(
        name="Manuals", site_type="docs", base_url="https://manual.test", fetch_mode="manual"
    )
    session.add(site)
    await session.flush()
    job = CrawlJob(
        source_site_id=site.id,
        name="manual",
        trigger_mode="manual",
        parser_profile="manual",
        max_pages=1,
    )
    session.add(job)
    await session.flush()
    run = CrawlRun(source_site_id=site.id, crawl_job_id=job.id, trigger_type="manual")
    session.add(run)
    await session.flush()
    chunks = []
    for item_index in range(3):
        url = f"https://manual.test/{item_index}"
        raw = RawPage(
            source_site_id=site.id, crawl_run_id=run.id, requested_url=url, fetched_at=utcnow()
        )
        session.add(raw)
        await session.flush()
        item = ContentItem(
            source_site_id=site.id,
            raw_page_id=raw.id,
            crawl_run_id=run.id,
            item_type="video_description",
            canonical_url=url,
            title=f"PX4 telemetry {item_index}",
            cleaned_text="telemetry manual",
            content_hash=str(uuid4()),
            dedup_key=str(uuid4()),
        )
        session.add(item)
        await session.flush()
        for index in range(4):
            text = f"PX4 telemetry source {item_index} chunk {index}. " + "detail " * 70
            chunk = ContentChunk(
                content_item_id=item.id,
                chunk_index=index,
                display_text=text,
                embed_text=text,
                embed_status="success",
            )
            session.add(chunk)
            chunks.append(chunk)
    await session.commit()
    return chunks


def settings(*, api_key: str | None = None) -> Settings:
    return Settings(
        _env_file=None,
        LLM_PROVIDER="fake",
        EMBEDDING_API_KEY="test",
        DEEPSEEK_API_KEY=api_key,
        QDRANT_URL="memory://chat-tests",
    )


@pytest.mark.parametrize("api_failure", [False, True])
async def test_answer_reranking_preserves_scores_order_and_audit_or_falls_back(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch, api_failure: bool
) -> None:
    await seed(db_session)
    config = settings().model_copy(
        update={
            "reranker_provider": "siliconflow",
            "reranker_candidate_limit": 12,
            "answer_max_evidence": 3,
        }
    )
    original = SiliconFlowReranker.rerank
    expected = []

    async def rerank(self: Any, query: str, hits: list, texts: dict) -> Any:
        assert query == "PX4 telemetry"
        assert len(hits) == 6
        assert all(len(texts[str(hit.chunk_id)]) > len(hit.snippet) for hit in hits)
        first_source = hits[-1].content_item_id
        ordered = [e for e in hits if e.content_item_id == first_source]
        ordered.extend(e for e in hits if e.content_item_id != first_source)
        expected.extend(ordered)

        def handler(request: httpx.Request) -> httpx.Response:
            payload = json.loads(request.content)
            assert payload["top_n"] == len(hits)
            if api_failure:
                return httpx.Response(429)
            return httpx.Response(
                200,
                json={
                    "results": [
                        {"index": hits.index(hit), "relevance_score": 1 - rank / 10}
                        for rank, hit in enumerate(ordered)
                    ]
                },
            )

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            return await original(SiliconFlowReranker(config, client=client), query, hits, texts)

    monkeypatch.setattr(SiliconFlowReranker, "rerank", rerank)
    service = SearchService(db_session, settings=config)
    events = [event async for event in service.stream_answer(AnswerRequest(query="PX4 telemetry"))]
    response = events[-1]["data"]
    assert [e["data"]["stage"] for e in events if e["event"] == "status"] == [
        "retrieving",
        "reranking",
        "generating",
    ]
    assert response["generation"]["evidence_count"] == 3
    trace = response["generation"]["reranking"]
    evidence = response["supporting_evidence"]
    if api_failure:
        assert trace["status"] == "fallback"
        assert trace["reason"] == "http_429"
        assert len({e["content_item_id"] for e in evidence}) == 3
        assert all(e["rerank_score"] is None for e in evidence)
    else:
        assert trace["status"] == "reranked"
        assert len(trace["rankings"]) == 6
        assert [e["chunk_id"] for e in evidence] == [str(e.chunk_id) for e in expected[:3]]
        assert evidence[0]["content_item_id"] == evidence[1]["content_item_id"]
        assert [e["score"] for e in evidence] == [e.score for e in expected[:3]]
    stored = await db_session.get(SearchQuery, UUID(response["query"]["id"]))
    assert stored is not None
    assert stored.query_trace_json["generation"]["reranking"] == trace
    assert stored.result_summary_json["generation"]["reranking"] == trace


async def test_context_uses_full_chunks_neighbors_and_diverse_sources(
    db_session: AsyncSession,
) -> None:
    chunks = await seed(db_session)
    result = await retrieve_evidence(
        db_session,
        raw_query="telemetry",
        optimized_query_text="telemetry",
        keyword_terms=["telemetry"],
        filters=SearchFilters(),
        top_k=12,
        qdrant_url="memory://chat-tests",
        qdrant_collection="test",
        embedding=SiliconFlowEmbeddingService(
            model_name="BAAI/bge-m3", api_key="test", base_url="https://unused.test"
        ),
        max_per_content=2,
    )
    # A single video's chunks must not monopolize the selected evidence.
    context = await prepare_answer_context(db_session, result.evidence)
    assert len(context.evidence) == 6
    assert len({e.content_item_id for e in context.evidence[:3]}) == 3
    assert len(context.text) <= 12_000
    assert len(context.text) > sum(len(e.snippet) for e in context.evidence)
    assert chunks[0].display_text.strip() in context.text
    assert len(context.chunk_ids) > len(context.evidence)
    bounded = await prepare_answer_context(db_session, result.evidence, max_chars=1000)
    assert len(bounded.text) <= 1000
    for n in range(1, len(bounded.evidence) + 1):
        assert f"[{n}] 标题:" in bounded.text


async def test_stream_reset_and_final_answer_are_persisted(
    db_session: AsyncSession,
    chat_client: httpx.AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await seed(db_session)
    monkeypatch.setattr("app.services.search.get_settings", lambda: settings(api_key="test"))

    async def broken_stream(self: Any, *args: Any, **kwargs: Any) -> AsyncIterator[str]:
        assert len(kwargs["context"]) > 300
        yield "unfinished and unsupported"
        raise AnswerGenerationError("simulated timeout")

    monkeypatch.setattr(DeepSeekAnswerClient, "stream", broken_stream)
    session = (await chat_client.post("/chat-sessions")).json()
    response = await chat_client.post(
        "/answer/stream",
        json={
            "query": "PX4 telemetry",
            "session_id": session["id"],
            "top_k": 10,
        },
    )
    assert response.headers["content-type"].startswith("text/event-stream")
    assert response.headers["x-accel-buffering"] == "no"
    frames = [frame.splitlines() for frame in response.text.strip().split("\n\n")]
    events = [(frame[0][7:], json.loads(frame[1][6:])) for frame in frames]
    names = [event for event, _ in events]
    assert names.index("delta") < names.index("reset") < names.index("done")
    final = events[-1][1]
    assert final["generation"]["status"] == "fallback"
    assert "unfinished" not in final["answer"]
    assert len(final["supporting_evidence"]) > 3
    detail = (await chat_client.get(f"/chat-sessions/{session['id']}")).json()
    assert detail["turns"][0]["answer"] == final["answer"]
    assert detail["turns"][0]["status"] == "completed"
    trace = (await chat_client.get(f"/search-queries/{final['query']['id']}")).json()
    assert trace["query_trace_json"]["generation"]["status"] == "fallback"
    query = await db_session.get(SearchQuery, UUID(final["query"]["id"]))
    assert query.result_summary_json["answer"] == final["answer"]


async def test_followup_rewrites_for_retrieval_and_reloads_history(
    db_session: AsyncSession,
    chat_client: httpx.AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await seed(db_session)
    monkeypatch.setattr("app.services.search.get_settings", lambda: settings(api_key="test"))
    received = []

    async def model_stream(
        self: Any, query: str, evidence: Any, **kwargs: Any
    ) -> AsyncIterator[str]:
        received.append((query, kwargs["history"]))
        yield "PX4 telemetry "
        yield "supported [1]"

    async def rewrite(self: Any, query: str, history: Any) -> str:
        assert history[0]["content"] == "PX4 telemetry"
        return "PX4 telemetry limitations"

    monkeypatch.setattr(DeepSeekAnswerClient, "stream", model_stream)
    monkeypatch.setattr(DeepSeekAnswerClient, "rewrite_question", rewrite)
    session_id = (await chat_client.post("/chat-sessions")).json()["id"]
    for question in ["PX4 telemetry", "它有什么限制？"]:
        result = await chat_client.post(
            "/answer", json={"query": question, "session_id": session_id}
        )
        assert result.status_code == 200
    final = result.json()
    assert final["query"]["raw_query"] == "它有什么限制？"
    assert final["generation"]["retrieval_query"] == "PX4 telemetry limitations"
    assert final["generation"]["rewrite_status"] == "rewritten"
    assert len(received[1][1]) == 2
    detail = (await chat_client.get(f"/chat-sessions/{session_id}")).json()
    assert len(detail["turns"]) == 2
    assert all(turn["status"] == "completed" for turn in detail["turns"])
    assert (await chat_client.get("/chat-sessions")).json()[0]["title"] == "PX4 telemetry"


async def test_missing_key_no_evidence_and_rewrite_fallback_are_explicit(
    db_session: AsyncSession,
) -> None:
    service = SearchService(db_session, settings=settings())
    repo = ChatRepository(db_session)
    session_id = (await repo.create_session()).id
    result = await service.answer(AnswerRequest(query="PX4 telemetry", session_id=session_id))
    assert result.generation.status == "no_evidence"
    await seed(db_session)
    result = await service.answer(AnswerRequest(query="它呢？", session_id=session_id))
    assert result.generation.status == "fallback"
    assert result.generation.reason == "missing_api_key"
    assert result.generation.rewrite_status == "fallback"
    assert "PX4 telemetry" in result.generation.retrieval_query


async def test_cancelling_stream_saves_partial_and_releases_session(
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await seed(db_session)

    async def stream(self: Any, *args: Any, **kwargs: Any) -> AsyncIterator[str]:
        yield "partial answer"
        yield " must not be used in follow-up"

    monkeypatch.setattr(DeepSeekAnswerClient, "stream", stream)
    repo = ChatRepository(db_session)
    session_id = (await repo.create_session()).id
    generator = SearchService(db_session, settings=settings(api_key="test")).stream_answer(
        AnswerRequest(query="PX4 telemetry", session_id=session_id)
    )
    async for event in generator:
        if event["event"] == "delta":
            break
    await generator.aclose()
    turn = await db_session.scalar(select(ChatTurn).where(ChatTurn.session_id == session_id))
    assert turn.status == "cancelled"
    assert turn.answer == "partial answer"
    assert await repo.history(session_id) == []
    next_turn = await repo.begin_turn(session_id, "next question")
    assert next_turn.status == "pending"
    with pytest.raises(ChatSessionBusy):
        await repo.begin_turn(session_id, "duplicate question")


async def test_unknown_sessions_return_clear_errors(chat_client: httpx.AsyncClient) -> None:
    unknown = str(uuid4())
    assert (await chat_client.get(f"/chat-sessions/{unknown}")).status_code == 404
    assert (
        await chat_client.post("/answer", json={"query": "test", "session_id": unknown})
    ).status_code == 404
    stream = await chat_client.post("/answer/stream", json={"query": "test", "session_id": unknown})
    assert "event: error" in stream.text
    assert "event: done" not in stream.text


async def test_response_iterator_disconnect_finalizes_before_session_closes(
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.api.search import stream_answer

    await seed(db_session)
    monkeypatch.setattr("app.services.search.get_settings", lambda: settings(api_key="test"))

    async def model_stream(self: Any, *args: Any, **kwargs: Any) -> AsyncIterator[str]:
        yield "partial over SSE"
        yield "next fragment"

    monkeypatch.setattr(DeepSeekAnswerClient, "stream", model_stream)
    chat = ChatRepository(db_session)
    session_id = (await chat.create_session()).id

    @asynccontextmanager
    async def factory() -> AsyncIterator[AsyncSession]:
        yield db_session

    response = await stream_answer(
        AnswerRequest(query="PX4 telemetry", session_id=session_id), factory
    )
    iterator = response.body_iterator
    async for frame in iterator:
        if "event: delta" in frame:
            break
    await iterator.aclose()
    detail = await chat.detail(session_id)
    assert detail.turns[0].status == "cancelled"
    assert detail.turns[0].answer == "partial over SSE"


async def test_new_chat_migration_upgrades_and_downgrades(db_session: AsyncSession) -> None:
    from importlib.util import module_from_spec, spec_from_file_location
    from pathlib import Path

    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    from sqlalchemy import inspect, text

    path = Path(__file__).parents[1] / "alembic/versions/0007_chat_sessions.py"
    spec = spec_from_file_location("chat_migration", path)
    module = module_from_spec(spec)
    spec.loader.exec_module(module)
    connection = await db_session.connection()
    # Isolate the migration in its own schema while retaining the FK target.
    schema = "chat_migration_" + uuid4().hex
    await connection.execute(text(f"CREATE SCHEMA {schema}"))
    await connection.execute(text(f"SET LOCAL search_path TO {schema}"))
    await connection.execute(text("CREATE TABLE search_queries (id uuid PRIMARY KEY)"))

    def verify(sync_connection: Any) -> None:
        with Operations.context(MigrationContext.configure(sync_connection)):
            module.upgrade()
            tables = inspect(sync_connection).get_table_names(schema=schema)
            assert {"chat_sessions", "chat_turns"} <= set(tables)
            module.downgrade()
            assert "chat_turns" not in inspect(sync_connection).get_table_names(schema=schema)

    await connection.run_sync(verify)
    await db_session.rollback()


async def test_delete_session_removes_turns_and_associated_diagnostics(
    db_session: AsyncSession,
    chat_client: httpx.AsyncClient,
) -> None:
    from app.models.search import AgentCall

    chat = ChatRepository(db_session)
    service = SearchService(db_session, settings=settings())
    removed_id = (await chat.create_session()).id
    retained_id = (await chat.create_session()).id
    removed = await service.answer(
        AnswerRequest(query="delete this question", session_id=removed_id)
    )
    retained = await service.answer(
        AnswerRequest(query="retain this question", session_id=retained_id)
    )
    audit = AgentCall(
        agent_role="answer",
        caller="test",
        status="success",
        related_search_query_id=removed.query.id,
        request_schema_version="test.v1",
        response_schema_version="test.v1",
    )
    db_session.add(audit)
    await db_session.commit()
    audit_id = audit.id
    response = await chat_client.delete(f"/chat-sessions/{removed_id}")
    assert response.status_code == 204
    assert response.content == b""
    assert (await chat_client.get(f"/chat-sessions/{removed_id}")).status_code == 404
    assert (await chat_client.get(f"/search-queries/{removed.query.id}")).status_code == 404
    assert (await chat_client.get(f"/chat-sessions/{retained_id}")).status_code == 200
    assert (await chat_client.get(f"/search-queries/{retained.query.id}")).status_code == 200
    assert (
        await db_session.scalar(select(ChatTurn.id).where(ChatTurn.session_id == removed_id))
        is None
    )
    assert await db_session.scalar(select(AgentCall.id).where(AgentCall.id == audit_id)) is None
    assert [s["id"] for s in (await chat_client.get("/chat-sessions")).json()] == [str(retained_id)]
    assert (await chat_client.delete(f"/chat-sessions/{removed_id}")).status_code == 404
    result = await chat_client.post(
        "/answer", json={"query": "follow-up", "session_id": str(removed_id)}
    )
    assert result.status_code == 404


async def test_delete_empty_session(chat_client: httpx.AsyncClient) -> None:
    session_id = (await chat_client.post("/chat-sessions")).json()["id"]
    assert (await chat_client.delete(f"/chat-sessions/{session_id}")).status_code == 204
    assert (await chat_client.get("/chat-sessions")).json() == []


async def test_delete_active_session_is_rejected_and_can_retry_after_stop(
    db_session: AsyncSession,
    chat_client: httpx.AsyncClient,
) -> None:
    chat = ChatRepository(db_session)
    session_id = (await chat.create_session()).id
    turn = await chat.begin_turn(session_id, "still generating")
    turn_id = turn.id
    response = await chat_client.delete(f"/chat-sessions/{session_id}")
    assert response.status_code == 409
    assert "停止生成" in response.json()["detail"]
    assert (await chat_client.get(f"/chat-sessions/{session_id}")).status_code == 200
    turn = await db_session.get(ChatTurn, turn_id)
    await chat.finish_turn(turn, None, status="cancelled")
    assert (await chat_client.delete(f"/chat-sessions/{session_id}")).status_code == 204


async def test_delete_abandoned_pending_session(
    db_session: AsyncSession,
    chat_client: httpx.AsyncClient,
) -> None:
    from datetime import timedelta

    chat = ChatRepository(db_session)
    session_id = (await chat.create_session()).id
    turn = await chat.begin_turn(session_id, "abandoned request")
    turn.created_at = utcnow() - timedelta(minutes=11)
    await db_session.commit()
    assert (await chat_client.delete(f"/chat-sessions/{session_id}")).status_code == 204


async def test_delete_preserves_queries_shared_by_another_session(
    db_session: AsyncSession,
    chat_client: httpx.AsyncClient,
) -> None:
    chat = ChatRepository(db_session)
    session_id = (await chat.create_session()).id
    result = await SearchService(db_session, settings=settings()).answer(
        AnswerRequest(query="shared query", session_id=session_id)
    )
    other_id = (await chat.create_session()).id
    db_session.add(
        ChatTurn(
            session_id=other_id,
            query="other session",
            answer="shared answer",
            status="completed",
            search_query_id=result.query.id,
            supporting_evidence=[],
        )
    )
    await db_session.commit()
    assert (await chat_client.delete(f"/chat-sessions/{session_id}")).status_code == 204
    assert (await chat_client.get(f"/search-queries/{result.query.id}")).status_code == 200
    assert (await chat_client.get(f"/chat-sessions/{other_id}")).json()["turns"][0][
        "answer"
    ] == "shared answer"
