import json
import logging
import re
from typing import Any

import httpx

from app.agents.contracts import (
    ExtractionAgentRequest,
    ExtractionAgentResponse,
    ExtractionItem,
    QueryOptimizationResponse,
)
from app.core.config import Settings, get_settings

logger = logging.getLogger(__name__)

_FALLBACK_EMPTY_BODY_TEXT = "[agent_fallback_empty_body]"
_FAKE_EMPTY_BODY_TEXT = "[fake_agent_empty_body]"


class AgentClient:
    def __init__(
        self,
        provider: str,
        timeout_seconds: int,
        settings: Settings | None = None,
    ) -> None:
        self.provider = provider
        self.timeout_seconds = timeout_seconds
        self.settings = settings or get_settings()

    def extract_page(self, request: ExtractionAgentRequest) -> ExtractionAgentResponse:
        try:
            if self.provider == "fake":
                return self._fake_extract_page(request)
            if self.provider == "fake-failing":
                raise RuntimeError("fake provider configured to fail")
            return self._fallback_extraction_response(request, status="unsupported_provider")
        except Exception as exc:
            logger.warning("Agent extraction failed; using fallback response", exc_info=exc)
            return self._fallback_extraction_response(
                request,
                error_message=str(exc),
                status=self._fallback_status_for_error(),
            )

    def optimize_query(
        self, raw_query: str, filters: dict[str, Any], mode: str
    ) -> QueryOptimizationResponse:
        try:
            if self.provider == "fake":
                return self._fake_optimize_query(raw_query=raw_query, filters=filters, mode=mode)
            if self.provider == "fake-failing":
                raise RuntimeError("fake provider configured to fail")
            if self.provider == "deepseek":
                return self._deepseek_optimize_query(
                    raw_query=raw_query, filters=filters, mode=mode
                )
            return self._fallback_query_response(raw_query, status="unsupported_provider")
        except Exception as exc:
            logger.warning("Agent query optimization failed; using fallback response", exc_info=exc)
            return self._fallback_query_response(
                raw_query,
                error_message=str(exc),
                status=self._fallback_status_for_error(),
            )

    def _fake_extract_page(self, request: ExtractionAgentRequest) -> ExtractionAgentResponse:
        raw_body = self._raw_body_from_request(request)
        body_text = raw_body if raw_body.strip() else _FAKE_EMPTY_BODY_TEXT
        warnings = [] if raw_body.strip() else ["fake_agent_empty_body"]
        return ExtractionAgentResponse(
            page_kind="article",
            items=[
                ExtractionItem(
                    item_type="article",
                    external_item_id=str(request.raw_page_id),
                    title=None,
                    author=None,
                    published_at=None,
                    body_text=body_text,
                    summary_text=body_text[:240],
                    tags=[],
                    parent_ref=None,
                    thread_root_ref=None,
                    metadata_json={
                        "provider": "fake",
                        "raw_page_id": str(request.raw_page_id),
                        "source_site_id": str(request.source_site_id),
                        "crawl_run_id": str(request.crawl_run_id),
                    },
                )
            ],
            extraction_confidence=0.5,
            warnings=warnings,
            trace_summary_json={"provider": "fake", "fallback": False},
        )

    def _fake_optimize_query(
        self, *, raw_query: str, filters: dict[str, Any], mode: str
    ) -> QueryOptimizationResponse:
        optimized_query_text = raw_query.strip() or "[empty query]"
        return QueryOptimizationResponse(
            optimized_query_text=optimized_query_text,
            keyword_terms=[term for term in raw_query.split() if term],
            entity_hints=[],
            time_hints_json={},
            query_intent=f"fake_{mode}",
            confidence=0.5,
            trace_summary_json={
                "provider": "fake",
                "fallback": False,
                "filter_keys": sorted(filters.keys()),
            },
        )

    def _deepseek_optimize_query(
        self, *, raw_query: str, filters: dict[str, Any], mode: str
    ) -> QueryOptimizationResponse:
        if not self.settings.deepseek_api_key:
            raise RuntimeError(
                "缺少 DEEPSEEK_API_KEY；无法使用 deepseek 查询优化。"
                "请配置密钥或改用 LLM_PROVIDER=fake。"
            )
        system_prompt = (
            "你是无人机领域 RAG 检索的查询优化器。将用户的原始问题转成更利于检索的形式。"
            "只输出 JSON 对象，字段如下：\n"
            "optimized_query_text: string，表达同一意图但更适合向量检索的查询文本；\n"
            "keyword_terms: string[]，用于精确关键词匹配的核心词条（型号、零件、动作等），"
            "每个词条就是一个完整词或短语；\n"
            "entity_hints: string[]，识别出的实体提示（型号如 Mavic 3、PX4、品牌、GPS、RTK、"
            "特定协议等），用于在标题/标签中命中该实体的内容上加权。只收录明确提到的具体实体，"
            "没有就返回空数组；\n"
            "time_hints_json: object，若提到相对时间则给出 "
            '{"relative": "recent"|"year"}，否则为空对象 {}；\n'
            "query_intent: string，取值 fact|analysis|opinion|troubleshoot|other；\n"
            "confidence: number(0-1)，本次优化的把握程度。\n"
            "不得虚构原问题中不存在的实体或事实。"
        )
        user_prompt = (
            f"原始问题：{raw_query}\n"
            f"模式：{mode}\n"
            f"活跃过滤条件：{json.dumps(filters, ensure_ascii=False)}\n"
        )
        content = self._request_json_content(
            system=system_prompt, user=user_prompt, temperature=0.2
        )
        parsed = _parse_json_object(content)
        return QueryOptimizationResponse.model_validate(
            {
                "optimized_query_text": parsed.get("optimized_query_text")
                or (raw_query.strip() or "[empty query]"),
                "keyword_terms": [
                    str(term) for term in parsed.get("keyword_terms") or [] if str(term).strip()
                ],
                "entity_hints": [
                    str(hint) for hint in parsed.get("entity_hints") or [] if str(hint).strip()
                ],
                "time_hints_json": dict(parsed.get("time_hints_json") or {}),
                "query_intent": str(parsed.get("query_intent") or "other"),
                "confidence": float(parsed.get("confidence") or 0.0),
                "trace_summary_json": {"provider": "deepseek", "fallback": False},
            }
        )

    def _request_json_content(
        self, *, system: str, user: str, temperature: float
    ) -> str:
        url = f"{self.settings.deepseek_base_url.rstrip('/')}/chat/completions"
        headers = {
            "Authorization": f"Bearer {self.settings.deepseek_api_key}",
            "Content-Type": "application/json",
        }
        body = {
            "model": self.settings.deepseek_model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "temperature": temperature,
            "response_format": {"type": "json_object"},
        }
        timeout = httpx.Timeout(connect=10, read=self.timeout_seconds, write=30, pool=10)
        with httpx.Client(timeout=timeout) as client:
            response = client.post(url, headers=headers, json=body)
            response.raise_for_status()
            payload = response.json()
        try:
            return str(payload["choices"][0]["message"]["content"])
        except (KeyError, IndexError, TypeError) as exc:
            raise RuntimeError("DeepSeek 响应缺少 choices[0].message.content。") from exc

    def _fallback_extraction_response(
        self,
        request: ExtractionAgentRequest,
        *,
        error_message: str | None = None,
        status: str,
    ) -> ExtractionAgentResponse:
        raw_body = self._raw_body_from_request(request)
        body_text = raw_body if raw_body.strip() else _FALLBACK_EMPTY_BODY_TEXT
        warnings = ["agent_fallback_used"]
        metadata_json: dict[str, Any] = {"fallback": True}
        if not raw_body.strip():
            warnings.append("agent_fallback_empty_body")
            metadata_json["empty_body"] = True

        trace_summary_json: dict[str, Any] = {
            "provider": self.provider,
            "fallback": True,
            "status": status,
        }
        if error_message is not None:
            trace_summary_json["error"] = error_message

        return ExtractionAgentResponse(
            page_kind="other",
            items=[
                ExtractionItem(
                    item_type="article",
                    external_item_id=None,
                    title=None,
                    author=None,
                    published_at=None,
                    body_text=body_text,
                    summary_text=None,
                    tags=[],
                    parent_ref=None,
                    thread_root_ref=None,
                    metadata_json=metadata_json,
                )
            ],
            extraction_confidence=0.1,
            warnings=warnings,
            trace_summary_json=trace_summary_json,
        )

    def _fallback_query_response(
        self,
        raw_query: str,
        *,
        error_message: str | None = None,
        status: str,
    ) -> QueryOptimizationResponse:
        trace_summary_json: dict[str, Any] = {
            "provider": self.provider,
            "fallback": True,
            "status": status,
        }
        if error_message is not None:
            trace_summary_json["error"] = error_message

        stripped_query = raw_query.strip()
        optimized_query_text = stripped_query or "[empty query]"

        return QueryOptimizationResponse(
            optimized_query_text=optimized_query_text,
            keyword_terms=[term for term in stripped_query.split() if term],
            entity_hints=[],
            time_hints_json={},
            query_intent="fallback_raw_query",
            confidence=0.0,
            trace_summary_json=trace_summary_json,
        )

    def _fallback_status_for_error(self) -> str:
        if self.provider == "fake-failing":
            return "fake_failure"
        return "provider_error"

    @staticmethod
    def _raw_body_from_request(request: ExtractionAgentRequest) -> str:
        for source in (request.raw_markdown, request.raw_html):
            if source is not None and source.strip():
                return source[:8000]
        if request.raw_json is not None:
            return json.dumps(request.raw_json, sort_keys=True, separators=(",", ":"))[:8000]
        return ""


def _parse_json_object(content: str) -> dict[str, Any]:
    content = content.strip()
    if content.startswith("```"):
        content = re.sub(r"^```(?:json)?\s*", "", content, flags=re.I)
        content = re.sub(r"\s*```$", "", content)
    try:
        value = json.loads(content)
    except json.JSONDecodeError:
        start, end = content.find("{"), content.rfind("}")
        if start < 0 or end <= start:
            raise RuntimeError("模型输出无法解析为 JSON。") from None
        value = json.loads(content[start : end + 1])
    if not isinstance(value, dict):
        raise RuntimeError("模型输出不是 JSON 对象。")
    return value
