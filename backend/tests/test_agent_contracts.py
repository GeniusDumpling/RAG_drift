import pytest
from app.agents.client import AgentClient
from app.agents.contracts import (
    ExtractionAgentResponse,
    ExtractionItem,
    QueryOptimizationResponse,
)
from pydantic import ValidationError


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


def test_agent_client_query_fallback_uses_raw_query_on_failure() -> None:
    client = AgentClient(provider="fake-failing", timeout_seconds=1)
    result = client.optimize_query(
        raw_query="telemetry", filters={"source_site_id": "s1"}, mode="search"
    )
    assert result.optimized_query_text == "telemetry"
    assert result.keyword_terms == ["telemetry"]
    assert result.trace_summary_json["fallback"] is True
