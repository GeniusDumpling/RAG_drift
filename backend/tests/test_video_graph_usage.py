"""Usage accounting includes billable malformed outputs, without treating replays as calls."""

import json
from unittest.mock import patch

import httpx
import pytest
from app.core.config import Settings
from app.services.video_graph import LLMVideoExtractor
from app.services.video_graph_quality import EntityVerdict, RelationVerdict
from app.services.video_graph_usage import TokenUsage, merge_usage, usage_delta
from pydantic import ValidationError

from scripts.video_graph_response_cache import CachedEvaluationExtractor, ModelResponseCache

pytestmark = pytest.mark.no_db


def response(content, usage):
    return httpx.Response(200, request=httpx.Request("POST", "https://test.invalid"), json={
        "choices": [{"message": {"content": content}}], "usage": usage,
    })


def test_missing_usage_and_missing_cache_measurement_are_unknown():
    usage = TokenUsage()
    usage.record("review", None)
    usage.record("review", {"prompt_tokens": 10, "completion_tokens": 3, "total_tokens": 13})
    row = usage.snapshot()["review"]
    assert row["unknown_usage_responses"] == 1 and row["measured_responses"] == 1
    assert "cached_tokens" not in row and "uncached_prompt_tokens" not in row
    snapshot = usage.snapshot()
    snapshot["review"]["prompt_tokens"] = 0
    assert usage.snapshot()["review"]["prompt_tokens"] == 10
    assert merge_usage({"review": {"cached_tokens": 0}}, {}) == {"review": {"cached_tokens": 0}}


def test_billable_malformed_json_is_counted_before_retry_and_cache_replay(tmp_path):
    settings = Settings(_env_file=None, LLM_API_KEY="secret", LLM_MODEL="deepseek-test")
    cache = ModelResponseCache(tmp_path)
    extractor = CachedEvaluationExtractor(settings, cache=cache, namespace="test")
    counts = {"prompt_tokens": 100, "completion_tokens": 20, "total_tokens": 120,
              "prompt_tokens_details": {"cached_tokens": 40},
              "completion_tokens_details": {"reasoning_tokens": 0}}
    with patch("app.services.video_graph.httpx.post", side_effect=[
        response("invalid json", counts), response("{}", counts),
    ]), patch("app.services.video_graph.time.sleep"):
        assert extractor._request("rules", {"text": "source"}, phase="review") == {}
    row = extractor.cache_trace[0]["usage"]
    assert row["total_tokens"] == 240 and row["uncached_prompt_tokens"] == 120
    assert row["http_responses"] == row["measured_responses"] == 2
    with patch("app.services.video_graph.httpx.post", side_effect=AssertionError("network")):
        assert extractor._request("rules", {"text": "source"}, phase="review") == {}
    assert extractor.cache_trace[-1]["usage"] is None
    assert extractor.token_usage.snapshot()["review"] == row
    assert usage_delta({}, extractor.token_usage.snapshot()) == {"review": row}


def test_wire_json_keeps_original_request_format_and_unicode():
    extractor = LLMVideoExtractor(Settings(_env_file=None, LLM_API_KEY="test"))
    payload = {"text": "中文，含 空格", "entities": [{"key": "e1", "accepted": True}]}
    for phase in ("extraction", "relation", "review"):
        content = extractor._request_body("instructions", payload, phase=phase)["messages"][1][
            "content"]
        assert content == json.dumps(payload, ensure_ascii=False)


@pytest.mark.parametrize("model,identity,positive", [
    (EntityVerdict, {"key": "e1"}, "accepted"),
    (RelationVerdict, {"index": 0}, "supported"),
])
def test_decision_reason_is_preserved_for_positive_and_negative(model, identity, positive):
    for supported in (True, False):
        with pytest.raises(ValidationError):
            model.model_validate({**identity, positive: supported})
    rejected = model.model_validate({**identity, positive: False, "reason": "无依据"})
    assert rejected.reason == "无依据"


def test_terminal_parse_failure_keeps_all_billable_attempts_in_evaluation_trace(tmp_path):
    extractor = CachedEvaluationExtractor(
        Settings(_env_file=None, LLM_API_KEY="test"),
        cache=ModelResponseCache(tmp_path), namespace="failed-case",
    )
    counts = {"prompt_tokens": 10, "completion_tokens": 1, "total_tokens": 11}
    with patch("app.services.video_graph.httpx.post", return_value=response("bad", counts)), \
            patch("app.services.video_graph.time.sleep"):
        with pytest.raises(RuntimeError):
            extractor._request("rules", {}, phase="relation")
    assert len(extractor.cache_trace) == 1
    row = extractor.cache_trace[0]
    assert row["failed"] and row["usage"]["total_tokens"] == 22
    assert row["usage"]["http_attempts"] == 2
    assert not list(tmp_path.glob("*.json"))


def test_transport_failure_reports_attempts_without_inventing_usage():
    extractor = LLMVideoExtractor(Settings(_env_file=None, LLM_API_KEY="test"))
    with patch("app.services.video_graph.httpx.post", side_effect=httpx.ReadTimeout("private")), \
            patch("app.services.video_graph.time.sleep"):
        with pytest.raises(RuntimeError):
            extractor._request("rules", {}, phase="relation")
    assert extractor.token_usage.snapshot() == {"relation": {"http_attempts": 2}}


def test_historical_usage_is_separate_from_live_usage_in_summary():
    from scripts.summarize_video_graph_tokens import summarize

    gold = {"cases": [{"id": "case", "entities": [], "models": []}]}
    payload = {"extraction_version": "test", "metrics": {"overall": {}}, "results": {
        "case": {"status": "error", "cache_trace": [{
            "phase": "review", "replayed": True, "request_hash": "exact", "usage": None,
        }]},
    }}
    summary = summarize(payload, gold, {"exact": {
        "measured_responses": 1, "prompt_tokens": 10, "completion_tokens": 3, "total_tokens": 13,
    }})
    assert summary["usage"] == {} and summary["cache_hit_fraction"] is None
    assert summary["request_usage_including_historical_exact_replays"]["total_tokens"] == 13
    assert summary["requests_without_measurement"] == []
    assert summarize(payload, gold)["requests_without_measurement"] == ["exact"]
