from collections.abc import Callable
from typing import Any, Protocol

import httpx

from app.schemas.search import EvidenceObject


class AnswerGenerationError(RuntimeError):
    """Raised when a model response cannot produce an evidence-grounded answer."""


class AnswerResponse(Protocol):
    def raise_for_status(self) -> None: ...

    def json(self) -> dict[str, Any]: ...


AnswerPost = Callable[..., AnswerResponse]


class DeepSeekAnswerClient:
    def __init__(self, *, base_url: str, api_key: str, model: str, timeout_seconds: int) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = model
        self.timeout_seconds = timeout_seconds

    def generate(
        self,
        query: str,
        evidence: list[EvidenceObject],
        *,
        post: AnswerPost | None = None,
    ) -> str:
        context = build_answer_context(evidence, max_chars=12_000)
        payload = {
            "model": self.model,
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "你是基于检索证据回答问题的助手。只能依据提供的编号证据回答；"
                        "每项事实都用 [n] 引用对应证据。证据不足时明确说明。"
                    ),
                },
                {"role": "user", "content": f"问题：{query}\n\n证据：\n{context}"},
            ],
            "temperature": 0.2,
        }
        request_post = post or httpx.post
        try:
            response = request_post(
                f"{self.base_url}/chat/completions",
                headers={"Authorization": f"Bearer {self.api_key}"},
                json=payload,
                timeout=self.timeout_seconds,
            )
            response.raise_for_status()
            data = response.json()
            content = (data.get("choices") or [{}])[0].get("message", {}).get("content", "")
        except Exception as exc:
            raise AnswerGenerationError("DeepSeek 答案生成请求失败") from exc
        if not isinstance(content, str) or not content.strip():
            raise AnswerGenerationError("DeepSeek 返回了空答案")
        return content.strip()


def build_answer_context(evidence: list[EvidenceObject], *, max_chars: int) -> str:
    """Build bounded, numbered evidence text for a grounded answer prompt."""
    if max_chars < 1:
        raise ValueError("max_chars must be at least 1")

    blocks: list[str] = []
    remaining = max_chars
    for index, item in enumerate(evidence, start=1):
        block = (
            f"[{index}] 标题: {item.title or '无标题'}\n"
            f"来源: {item.source_site_name}\n"
            f"URL: {item.canonical_url}\n"
            f"内容: {item.snippet.strip()}"
        )
        if len(block) > remaining:
            block = block[:remaining].rstrip()
        if not block:
            break
        blocks.append(block)
        remaining -= len(block) + 2
        if remaining <= 0:
            break
    return "\n\n".join(blocks)
