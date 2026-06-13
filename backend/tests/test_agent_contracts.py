import json
import uuid

import pytest
from app.agents.client import AgentClient
from app.agents.contracts import (
    ExtractionAgentRequest,
    ExtractionAgentResponse,
    ExtractionItem,
    QueryOptimizationResponse,
)
from app.models.search import AgentCall
from app.repositories.search import create_agent_call
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession


def test_extraction_response_requires_structured_items() -> None:
    response = ExtractionAgentResponse(
        page_kind="thread_root",
        items=[
            ExtractionItem(
                item_type="thread",
                external_item_id="thread-1",
                title="Telemetry thread",
                author={"handle": "alice", "display_name": "Alice"},
                published_at=None,
                body_text="Telemetry changed in release 1.2.",
                summary_text="Telemetry change discussion.",
                tags=["telemetry"],
                parent_ref=None,
                thread_root_ref=None,
                metadata_json={"source": "test"},
            )
        ],
        extraction_confidence=0.82,
        warnings=[],
        trace_summary_json={"strategy": "fake"},
    )
    assert response.items[0].item_type == "thread"


def test_extraction_response_rejects_free_text_only() -> None:
    with pytest.raises(ValidationError):
        ExtractionAgentResponse.model_validate({"text": "this page is about telemetry"})


def test_query_optimization_response_keeps_structured_fields() -> None:
    response = QueryOptimizationResponse(
        optimized_query_text="telemetry release behavior",
        keyword_terms=["telemetry", "release"],
        entity_hints=["telemetry"],
        time_hints_json={"relative": "recent"},
        query_intent="find_recent_changes",
        confidence=0.75,
        trace_summary_json={"provider": "fake"},
    )
    assert "telemetry" in response.keyword_terms


def _extraction_request(
    *,
    raw_html: str | None,
    raw_markdown: str | None,
    raw_json: dict[str, object] | None = None,
) -> ExtractionAgentRequest:
    return ExtractionAgentRequest(
        raw_page_id=uuid.uuid4(),
        source_site_id=uuid.uuid4(),
        crawl_run_id=uuid.uuid4(),
        requested_url="https://example.com/page",
        final_url="https://example.com/page",
        site_type="docs",
        parser_profile="official_site",
        content_type="text/html",
        raw_html=raw_html,
        raw_markdown=raw_markdown,
        raw_json=raw_json,
        context_json={"source": "test"},
    )


def test_agent_client_query_fallback_uses_raw_query_on_failure() -> None:
    client = AgentClient(provider="fake-failing", timeout_seconds=1)
    result = client.optimize_query(
        raw_query="telemetry", filters={"source_site_id": "s1"}, mode="search"
    )
    assert result.optimized_query_text == "telemetry"
    assert result.keyword_terms == ["telemetry"]
    assert result.trace_summary_json["fallback"] is True
    assert result.trace_summary_json["provider"] == "fake-failing"
    assert result.trace_summary_json["status"] == "fake_failure"
    assert "fake provider configured to fail" in result.trace_summary_json["error"]


def test_agent_client_query_fallback_trace_preserves_unsupported_provider() -> None:
    client = AgentClient(provider="unsupported-provider", timeout_seconds=1)

    result = client.optimize_query(raw_query="telemetry", filters={}, mode="search")

    assert result.optimized_query_text == "telemetry"
    assert result.trace_summary_json["fallback"] is True
    assert result.trace_summary_json["provider"] == "unsupported-provider"
    assert result.trace_summary_json["status"] == "unsupported_provider"


@pytest.mark.parametrize("provider", ["fake-failing", "unsupported-provider"])
def test_agent_client_query_fallback_uses_placeholder_for_whitespace_query(
    provider: str,
) -> None:
    client = AgentClient(provider=provider, timeout_seconds=1)

    result = client.optimize_query(raw_query="  \t\n  ", filters={}, mode="search")

    assert result.optimized_query_text == "[empty query]"
    assert result.keyword_terms == []
    assert result.trace_summary_json["fallback"] is True


def test_agent_client_fake_extract_page_returns_structured_response_from_raw_body() -> None:
    client = AgentClient(provider="fake", timeout_seconds=1)
    request = _extraction_request(
        raw_html="<html><body>HTML fallback body</body></html>",
        raw_markdown="# Markdown body\n\nStructured content from markdown.",
    )

    result = client.extract_page(request)

    assert result.page_kind == "article"
    assert result.items[0].body_text == "# Markdown body\n\nStructured content from markdown."
    assert result.items[0].summary_text == "# Markdown body\n\nStructured content from markdown."
    assert result.trace_summary_json == {"provider": "fake", "fallback": False}


def test_agent_client_fake_extract_page_skips_blank_markdown_for_html_body() -> None:
    client = AgentClient(provider="fake", timeout_seconds=1)
    request = _extraction_request(
        raw_html="<html><body>Useful HTML body</body></html>",
        raw_markdown="   ",
    )

    result = client.extract_page(request)

    assert result.items[0].body_text == "<html><body>Useful HTML body</body></html>"
    assert result.items[0].summary_text == "<html><body>Useful HTML body</body></html>"
    assert result.warnings == []


def test_agent_client_fake_extract_page_uses_deterministic_json_body_when_no_text_sources() -> None:
    client = AgentClient(provider="fake", timeout_seconds=1)
    raw_json = {"zeta": 3, "alpha": [2, 1], "nested": {"b": 2, "a": 1}}
    request = _extraction_request(raw_html=None, raw_markdown=None, raw_json=raw_json)

    result = client.extract_page(request)

    assert result.items[0].body_text == json.dumps(
        raw_json, sort_keys=True, separators=(",", ":")
    )
    assert result.warnings == []


@pytest.mark.parametrize(
    ("provider", "expected_status"),
    [("fake-failing", "fake_failure"), ("unsupported-provider", "unsupported_provider")],
)
def test_agent_client_extract_page_fallback_returns_non_empty_body_without_raw_body(
    provider: str, expected_status: str
) -> None:
    client = AgentClient(provider=provider, timeout_seconds=1)
    request = _extraction_request(raw_html=None, raw_markdown=None)

    result = client.extract_page(request)

    assert result.items[0].body_text.strip()
    assert "agent_fallback_empty_body" in result.warnings
    assert result.trace_summary_json["fallback"] is True
    assert result.trace_summary_json["provider"] == provider
    assert result.trace_summary_json["status"] == expected_status


async def test_create_agent_call_persists_refreshes_summaries_and_schema_versions(
    db_session: AsyncSession,
) -> None:
    input_summary = {"raw_query": "telemetry", "mode": "search"}
    output_summary = {"optimized_query": "telemetry release", "confidence": 0.5}

    agent_call = await create_agent_call(
        db_session,
        agent_role="query_optimizer",
        caller="test_agent_contracts",
        status="succeeded",
        input_summary_json=input_summary,
        output_summary_json=output_summary,
        latency_ms=42,
        request_schema_version="extraction.v1",
        response_schema_version="query_optimization.v1",
    )

    assert agent_call.id is not None
    assert agent_call.created_at is not None
    assert agent_call.input_summary_json == input_summary
    assert agent_call.output_summary_json == output_summary
    assert agent_call.request_schema_version == "extraction.v1"
    assert agent_call.response_schema_version == "query_optimization.v1"

    persisted_agent_call = await db_session.scalar(
        select(AgentCall).where(AgentCall.id == agent_call.id)
    )
    assert persisted_agent_call is not None
    assert persisted_agent_call.input_summary_json == input_summary
    assert persisted_agent_call.output_summary_json == output_summary
    assert persisted_agent_call.request_schema_version == "extraction.v1"
    assert persisted_agent_call.response_schema_version == "query_optimization.v1"
