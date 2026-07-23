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
