"""Checkpoints never reuse a response across source scopes or changed requests."""

from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from app.services.video_graph.request_checkpoint import RequestCheckpoint

pytestmark = pytest.mark.no_db


def extractor(version="v1", key="private", endpoint="https://test.invalid"):
    return SimpleNamespace(
        version=version,
        settings=SimpleNamespace(
            llm_api_key=key,
            base_url=endpoint,
        ),
    )


def test_exact_response_is_durable_and_isolated_from_return_value_mutations():
    output, persist = {}, Mock()
    checkpoint = RequestCheckpoint(output, extractor(), persist)
    checkpoint.namespace = "chunk1"
    compute = Mock(return_value={"entities": []})
    value, replayed = checkpoint.get({"source": "text"}, compute)
    assert not replayed and persist.call_count == 1
    value["entities"].append("mutated")
    recovered = RequestCheckpoint(output, extractor(), persist)
    recovered.namespace = "chunk1"
    assert recovered.get({"source": "text"}, compute) == ({"entities": []}, True)
    assert compute.call_count == 1
    assert "private" not in str(output) and "https://test.invalid" not in str(output)


@pytest.mark.parametrize("change", ["version", "key", "endpoint", "namespace", "body"])
def test_changed_identity_or_request_is_never_replayed(change):
    output = {}
    checkpoint = RequestCheckpoint(output, extractor(), Mock())
    checkpoint.get({"text": "old"}, lambda: {"answer": "old"})
    options = {change: "changed"} if change in {"version", "key", "endpoint"} else {}
    recovered = RequestCheckpoint(output, extractor(**options), Mock())
    if change == "namespace":
        recovered.namespace = "newchunk"
    body = {"text": "new" if change == "body" else "old"}
    assert recovered.get(body, lambda: {"answer": "fresh"}) == ({"answer": "fresh"}, False)


def test_failed_requests_are_not_pinned_and_corruption_is_recomputed():
    output = {}
    checkpoint = RequestCheckpoint(output, extractor(), Mock())
    with pytest.raises(RuntimeError):
        checkpoint.get({}, Mock(side_effect=RuntimeError("provider failed")))
    assert checkpoint.get({}, lambda: {"answer": "ok"}) == ({"answer": "ok"}, False)
    entry = next(iter(output["request_checkpoints"]["assessment"].values()))
    entry["response_json"] = '{"answer":"tampered"}'
    assert checkpoint.get({}, lambda: {"answer": "fresh"}) == ({"answer": "fresh"}, False)
    checkpoint.clear()
    assert "request_checkpoints" not in output


def test_lost_lease_does_not_return_uncommitted_model_response():
    output = {}
    checkpoint = RequestCheckpoint(
        output, extractor(), Mock(side_effect=RuntimeError("lease lost"))
    )
    with pytest.raises(RuntimeError, match="lease lost"):
        checkpoint.get({}, lambda: {"answer": "ok"})
    assert output["request_checkpoints"]["assessment"] == {}


@pytest.mark.parametrize("corrupt", [None, [], {"assessment": []}])
def test_corrupted_checkpoint_structure_is_a_cache_miss(corrupt):
    checkpoint = RequestCheckpoint({"request_checkpoints": corrupt}, extractor(), Mock())
    assert checkpoint.get({}, lambda: {"answer": "ok"}) == ({"answer": "ok"}, False)


def test_jsonb_key_reordering_does_not_change_downstream_wire_json():
    import json

    output = {}
    original = {"attributes": {"long_field": "first", "a": "second"}}
    checkpoint = RequestCheckpoint(output, extractor(), Mock())
    checkpoint.get({}, lambda: original)
    database_round_trip = json.loads(json.dumps(output, sort_keys=True))
    recovered = RequestCheckpoint(database_round_trip, extractor(), Mock())
    response, replayed = recovered.get({}, Mock(side_effect=AssertionError("network")))
    assert replayed and json.dumps(response) == json.dumps(original)
