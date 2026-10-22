import uuid

import pytest
from app.schemas.search import EvidenceObject

pytestmark = pytest.mark.no_db


def _evidence(snippet: str, *, title: str = "证据标题") -> EvidenceObject:
    return EvidenceObject(
        chunk_id=uuid.uuid4(),
        content_item_id=uuid.uuid4(),
        raw_page_id=uuid.uuid4(),
        source_site_id=uuid.uuid4(),
        title=title,
        snippet=snippet,
        canonical_url="https://example.com/evidence",
        source_site_name="Example Source",
        author_name=None,
        published_at=None,
        item_type="article",
        score=0.9,
        matched_by="vector",
        thread_summary=None,
    )


def test_build_answer_context_numbers_evidence_and_bounds_snippets() -> None:
    from app.services.answer_generation import build_answer_context

    context = build_answer_context(
        [_evidence("第一条证据内容"), _evidence("第二条证据内容")],
        max_chars=1000,
    )

    assert "[1]" in context
    assert "[2]" in context
    assert "第一条证据内容" in context
    assert "第二条证据内容" in context
    assert "https://example.com/evidence" in context


def test_deepseek_answer_client_returns_grounded_content() -> None:
    from app.services.answer_generation import DeepSeekAnswerClient

    received: dict[str, object] = {}

    class Response:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict[str, object]:
            return {"choices": [{"message": {"content": "根据证据得出结论。[1]"}}]}

    def post(url: str, **kwargs: object) -> Response:
        received["url"] = url
        received.update(kwargs)
        return Response()

    answer = DeepSeekAnswerClient(
        base_url="https://deepseek.example/v1",
        api_key="test-key",
        model="deepseek-chat",
        timeout_seconds=10,
    ).generate("问题", [_evidence("事实")], post=post)

    assert answer == "根据证据得出结论。[1]"
    assert received["url"] == "https://deepseek.example/v1/chat/completions"


@pytest.mark.parametrize(
    "body",
    [
        'data: {"choices":[{"delta":{"content":"完整答案[1]"}}]}\n\ndata: [DONE]\n\n',
        'data: {"choices":[{"delta":{}}]}\n\n'
        'data: {"choices":[{"delta":{"content":"完整答案[1]"}}]}\n\ndata: [DONE]\n\n',
    ],
)
async def test_stream_reads_provider_sse_and_sends_full_context(monkeypatch, body) -> None:
    import json

    import httpx
    from app.services.answer_generation import DeepSeekAnswerClient

    received = {}

    def handler(request):
        received.update(json.loads(request.content))
        return httpx.Response(200, text=body, headers={"Content-Type": "text/event-stream"})

    client_class = httpx.AsyncClient
    monkeypatch.setattr(
        httpx,
        "AsyncClient",
        lambda **kwargs: client_class(transport=httpx.MockTransport(handler), **kwargs),
    )
    client = DeepSeekAnswerClient(
        base_url="https://model.test", api_key="test", model="test", timeout_seconds=10
    )
    history = [{"role": "user", "content": "先前的问题"}]
    output = "".join(
        [
            delta
            async for delta in client.stream(
                "追问",
                [_evidence("短摘要")],
                context="完整切片证据",
                history=history,
            )
        ]
    )
    assert output == "完整答案[1]"
    assert received["stream"] is True
    assert "完整切片证据" in received["messages"][-1]["content"]
    assert received["messages"][1] == history[0]


@pytest.mark.parametrize(
    "body",
    [
        "data: [DONE]\n\n",
        'data: {"choices":[{"delta":{"content":"未完成"}}]}\n\n',
        'data: {"error":{"message":"timeout"}}\n\n',
        "data: not-json\n\n",
        'data: {"choices":[{"delta":{"content":"截断"},"finish_reason":"length"}]}\n\n'
        "data: [DONE]\n\n",
    ],
)
async def test_stream_rejects_empty_broken_and_truncated_answers(monkeypatch, body) -> None:
    import httpx
    from app.services.answer_generation import AnswerGenerationError, DeepSeekAnswerClient

    client_class = httpx.AsyncClient
    monkeypatch.setattr(
        httpx,
        "AsyncClient",
        lambda **kwargs: client_class(
            transport=httpx.MockTransport(lambda request: httpx.Response(200, text=body)),
            **kwargs,
        ),
    )
    client = DeepSeekAnswerClient(
        base_url="https://model.test", api_key="test", model="test", timeout_seconds=10
    )
    with pytest.raises(AnswerGenerationError):
        _ = [delta async for delta in client.stream("问题", [], context="证据", history=[])]
