"""Evaluation replays exact requests, never stale prompts or graph review receipts."""

import json
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import Mock, patch

import pytest
from app.core.config import Settings
from app.services.video_graph import LLMVideoExtractor

from scripts.video_graph_response_cache import (
    CachedEvaluationExtractor,
    ModelResponseCache,
    ResponseCacheMiss,
)

pytestmark = pytest.mark.no_db


def test_cached_model_response_is_bound_to_exact_request_and_attempt(tmp_path):
    cache = ModelResponseCache(tmp_path)
    compute = Mock(return_value={"entities": [], "relations": []})
    args = dict(namespace="R001:attempt0", endpoint_hash="provider-model-account",
                request={"model": "example", "messages": ["source"]}, compute=compute)
    first, hit, key = cache.get(**args)
    assert not hit
    first["entities"].append("mutation must not alter cache")
    second, hit, _ = cache.get(**args)
    assert hit and second["entities"] == [] and compute.call_count == 1
    cache.cache_only = True
    for change in [{"namespace": "R001:attempt1"}, {"endpoint_hash": "new-account"},
                   {"request": {"model": "example", "messages": ["changed source"]}}]:
        with pytest.raises(ResponseCacheMiss):
            cache.get(**{**args, **change})
    envelope = json.loads((tmp_path / (key + ".json")).read_text())
    envelope["response"]["entities"] = ["tampered"]
    (tmp_path / (key + ".json")).write_text(json.dumps(envelope))
    with pytest.raises(ValueError, match="内容校验"):
        cache.get(**args)


def test_concurrent_duplicate_requests_pin_one_response(tmp_path):
    cache = ModelResponseCache(tmp_path)
    compute = Mock(return_value={"answer": "first"})
    args = dict(namespace="case0", endpoint_hash="endpoint", request={"source": "same"},
                compute=compute)
    with ThreadPoolExecutor(max_workers=4) as pool:
        rows = list(pool.map(lambda _: cache.get(**args), range(8)))
    assert compute.call_count == 1
    assert sum(not row[1] for row in rows) == 1


def test_live_then_offline_extractor_replay_has_no_plaintext_credentials(tmp_path):
    settings = Settings(_env_file=None, LLM_API_KEY="never-persist-this-key",
                        LLM_MODEL="deepseek-example")
    cache = ModelResponseCache(tmp_path)
    extractor = CachedEvaluationExtractor(settings, cache=cache, namespace="R001:attempt0")
    with patch.object(LLMVideoExtractor, "_request", return_value={"entities": []}) as live:
        assert extractor._request("system", {"source": "text"}) == {"entities": []}
        assert live.call_count == 1
    offline = CachedEvaluationExtractor(
        settings, cache=ModelResponseCache(tmp_path, cache_only=True), namespace="R001:attempt0"
    )
    with patch.object(LLMVideoExtractor, "_request", side_effect=AssertionError("network called")):
        assert offline._request("system", {"source": "text"}) == {"entities": []}
        assert offline.cache_stats == {"live_stages": 0, "replayed_stages": 1}
        with pytest.raises(ResponseCacheMiss):
            offline._request("system", {"source": "changed text"})
        with pytest.raises(ResponseCacheMiss):
            offline._request("system", {"source": "text"}, phase="review")
    for path in tmp_path.glob("*.json"):
        assert "never-persist-this-key" not in path.read_text()
