import logging
from typing import Any

from app.agents.contracts import (
    ExtractionAgentRequest,
    ExtractionAgentResponse,
    ExtractionItem,
    QueryOptimizationResponse,
)

logger = logging.getLogger(__name__)

_FALLBACK_EMPTY_BODY_TEXT = "[agent_fallback_empty_body]"
_FAKE_EMPTY_BODY_TEXT = "[fake_agent_empty_body]"


class AgentClient:
    def __init__(self, provider: str, timeout_seconds: int) -> None:
        self.provider = provider
        self.timeout_seconds = timeout_seconds

    def extract_page(self, request: ExtractionAgentRequest) -> ExtractionAgentResponse:
        try:
            if self.provider == "fake":
                return self._fake_extract_page(request)
            if self.provider == "fake-failing":
                raise RuntimeError("fake provider configured to fail")
            return self._fallback_extraction_response(request, provider="unsupported")
        except Exception as exc:
            logger.warning("Agent extraction failed; using fallback response", exc_info=exc)
            return self._fallback_extraction_response(request, error_message=str(exc))

    def optimize_query(
        self, raw_query: str, filters: dict[str, Any], mode: str
    ) -> QueryOptimizationResponse:
        try:
            if self.provider == "fake":
                return self._fake_optimize_query(raw_query=raw_query, filters=filters, mode=mode)
            if self.provider == "fake-failing":
                raise RuntimeError("fake provider configured to fail")
            return self._fallback_query_response(raw_query, provider="unsupported")
        except Exception as exc:
            logger.warning("Agent query optimization failed; using fallback response", exc_info=exc)
            return self._fallback_query_response(raw_query, error_message=str(exc))

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

    def _fallback_extraction_response(
        self,
        request: ExtractionAgentRequest,
        *,
        provider: str | None = None,
        error_message: str | None = None,
    ) -> ExtractionAgentResponse:
        raw_body = self._raw_body_from_request(request)
        body_text = raw_body if raw_body.strip() else _FALLBACK_EMPTY_BODY_TEXT
        warnings = ["agent_fallback_used"]
        metadata_json: dict[str, Any] = {"fallback": True}
        if not raw_body.strip():
            warnings.append("agent_fallback_empty_body")
            metadata_json["empty_body"] = True

        trace_summary_json: dict[str, Any] = {"fallback": True}
        if provider is not None:
            trace_summary_json["provider"] = provider
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
        provider: str | None = None,
        error_message: str | None = None,
    ) -> QueryOptimizationResponse:
        trace_summary_json: dict[str, Any] = {"fallback": True}
        if provider is not None:
            trace_summary_json["provider"] = provider
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

    @staticmethod
    def _raw_body_from_request(request: ExtractionAgentRequest) -> str:
        return (request.raw_markdown or request.raw_html or "")[:8000]
