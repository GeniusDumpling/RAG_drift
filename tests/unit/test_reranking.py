import asyncio
import json
from unittest.mock import AsyncMock
from uuid import uuid4

import httpx
import pytest
from app.core.config import Settings
from app.schemas.search import EvidenceObject
from app.services.reranking import SiliconFlowReranker, rerank_evidence


def evidence():
    return [
        EvidenceObject(
            chunk_id=uuid4(),
            content_item_id=uuid4(),
            raw_page_id=uuid4(),
            source_site_id=uuid4(),
            title=f"文档{n}",
            snippet="短摘要",
            canonical_url="https://test.invalid",
            source_site_name="manual",
            author_name=None,
            published_at=None,
            item_type="document",
            score=1 - n * 0.1,
            matched_by="hybrid",
            thread_summary=None,
        )
        for n in range(3)
    ]


def settings(**kwargs):
    return Settings(
        _env_file=None, RERANKER_PROVIDER="siliconflow", RERANKER_API_KEY="secret", **kwargs
    )


async def test_reranker_sends_full_text_and_returns_all_candidates_with_separate_scores():
    hits = evidence()
    texts = {str(e.chunk_id): "完整正文" * 100 for e in hits}

    def handler(request):
        body = json.loads(request.content)
        assert request.url.path == "/v1/rerank"
        assert body["query"] == "改写后的问题"
        assert body["top_n"] == len(hits)
        assert body["return_documents"] is False
        assert texts[str(hits[0].chunk_id)] in body["documents"][0]
        return httpx.Response(
            200,
            json={
                "results": [
                    {"index": 1, "relevance_score": 0.2},
                    {"index": 2, "relevance_score": 0.9},
                    {"index": 0, "relevance_score": 0.9},
                ]
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await SiliconFlowReranker(settings(), client=client).rerank(
            "改写后的问题", hits, texts
        )
    assert [e.chunk_id for e in result.evidence] == [hits[n].chunk_id for n in (0, 2, 1)]
    assert [e.score for e in result.evidence] == [hits[n].score for n in (0, 2, 1)]
    assert [e.rerank_score for e in result.evidence] == [0.9, 0.9, 0.2]
    assert all(e.rerank_score is None for e in hits)
    assert len(result.trace["rankings"]) == 3
    assert "secret" not in json.dumps(result.trace)


@pytest.mark.parametrize(
    "payload",
    [
        {"results": [{"index": 0, "relevance_score": 0.5}]},
        {"results": [{"index": 0, "relevance_score": 0.5}] * 3},
        {"results": [{"index": n, "relevance_score": "bad"} for n in range(3)]},
        {"results": [{"index": n + 1, "relevance_score": 0.5} for n in range(3)]},
        {"results": [{"index": n, "relevance_score": float("nan")} for n in range(3)]},
    ],
)
async def test_invalid_or_partial_rankings_fall_back_atomically(payload):
    hits = evidence()
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda r: httpx.Response(200, content=json.dumps(payload)))
    ) as client:
        result = await SiliconFlowReranker(settings(), client=client).rerank(
            "q", hits, {str(e.chunk_id): "正文" for e in hits}
        )
    assert result.evidence == hits
    assert result.trace["status"] == "fallback"
    assert all(e.rerank_score is None for e in result.evidence)


@pytest.mark.parametrize("failure", ["http", "timeout"])
async def test_api_failure_retains_original_order_and_does_not_leak_body(failure):
    def handler(request):
        if failure == "timeout":
            raise httpx.ReadTimeout("secret", request=request)
        return httpx.Response(429, json={"error": "secret"})

    hits = evidence()
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await SiliconFlowReranker(settings(), client=client).rerank(
            "q", hits, {str(e.chunk_id): "正文" for e in hits}
        )
    assert result.evidence == hits
    assert result.trace["reason"] == ("http_429" if failure == "http" else "ReadTimeout")
    assert "secret" not in json.dumps(result.trace)


async def test_missing_key_and_disabled_provider_do_not_call_network_or_database():
    hits = evidence()
    missing = Settings(_env_file=None, RERANKER_PROVIDER="siliconflow")
    result = await SiliconFlowReranker(missing).rerank("q", hits, {})
    assert result.trace["reason"] == "missing_api_key"
    session = AsyncMock()
    result = await rerank_evidence(session, "q", hits, Settings(_env_file=None))
    assert result.trace["status"] == "disabled"
    session.scalars.assert_not_called()


async def test_empty_candidates_skip_api_and_cancellation_propagates():
    assert (await SiliconFlowReranker(settings()).rerank("q", [], {})).trace["status"] == "empty"

    async def handler(request):
        raise asyncio.CancelledError()

    hits = evidence()
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(asyncio.CancelledError):
            await SiliconFlowReranker(settings(), client=client).rerank(
                "q", hits, {str(e.chunk_id): "正文" for e in hits}
            )


async def test_overall_deadline_falls_back_and_missing_body_never_uses_snippet():
    hits = evidence()

    async def slow(request):
        await asyncio.sleep(0.1)
        return httpx.Response(200, json={})

    async with httpx.AsyncClient(transport=httpx.MockTransport(slow)) as client:
        service = SiliconFlowReranker(settings(RERANKER_TIMEOUT_SECONDS=0.01), client=client)
        result = await service.rerank("q", hits, {str(e.chunk_id): "正文" for e in hits})
        assert result.trace["reason"] == "TimeoutError"
        assert result.evidence == hits
        result = await service.rerank("q", hits, {})
        assert result.trace["reason"] == "missing_chunk_text"
        assert result.evidence == hits
