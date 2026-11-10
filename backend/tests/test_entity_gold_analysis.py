"""Error explanations stay separate from strict scoring and preserve undecided cases."""

from copy import deepcopy

import pytest

from scripts.analyze_entity_gold import apply_review, classify_errors
from scripts.evaluate_entity_gold import matching

pytestmark = pytest.mark.no_db


def item(name, kind="product", mentions=None):
    return {"name": name, "type": kind, "mentions": mentions or [name]}


def case(gold, models=None):
    return {"id": "fixture", "entities": gold, "models": models or []}


def test_type_diagnosis_keeps_one_fp_and_one_fn_in_original_score():
    c = case([item("firmware", "software")])
    prediction = [item("firmware", "method")]
    errors, score = classify_errors(c, prediction)
    assert score == matching(c["entities"], prediction, [], typed=True)
    assert (score["tp"], score["fp"], score["fn"]) == (0, 1, 1)
    assert len(errors) == 1 and errors[0]["category"] == "type"


def test_explicit_owner_conflict_is_separate_from_missing_recognition():
    c = case([item("Air 3 camera", "component", ["camera"])],
             ["DJI Air 3", "DJI Air 3S"])
    errors, score = classify_errors(c, [item("Air 3S camera", "component", ["camera"])])
    assert score["tp"] == 0 and errors[0]["category"] == "owner"


def test_rejected_exact_candidate_is_visible_as_filtering_not_a_missing_proposal():
    c = case([item("motor", "component")])
    errors, _ = classify_errors(c, [],
        [{"key": "x", "surface_text": "motor", "entity_type": "component"}],
        {"x": {"accepted": False, "reason": "无信息量泛称"}})
    assert errors[0]["category"] == "selection"
    assert "无信息量" in errors[0]["note"]


def test_boundary_similarity_never_changes_score_or_automatically_claims_hallucination():
    c = case([item("sensor", "component")])
    prediction = [item("1-inch sensor", "component")]
    errors, score = classify_errors(c, prediction)
    assert (score["tp"], score["fp"], score["fn"]) == (0, 1, 1)
    assert len(errors) == 2 and all(e["category"] == "review" for e in errors)


def test_manual_categories_cannot_replace_gold_prediction_or_evaluation_identity():
    errors, _ = classify_errors(case([item("sensor", "component")]), [])
    expected = {"evaluation_sha256": "fixed", "errors": errors}
    saved = deepcopy(expected)
    saved["errors"][0].update(category="missed", note="已对照候选和原文，没有对应对象。")
    assert apply_review(expected, saved) == saved
    for key in ["gold", "id"]:
        tampered = deepcopy(saved)
        tampered["errors"][0][key] = "changed"
        with pytest.raises(ValueError, match="清单"):
            apply_review(expected, tampered)


def test_diagnosis_reads_exact_source_bound_cache_and_rejects_tampering(tmp_path):
    import json

    from scripts.analyze_entity_gold import proposal_review
    from scripts.video_graph_response_cache import ModelResponseCache

    data = {"current_chunk": "Firmware is updated.", "entities": [
        {"key": "f", "surface_text": "Firmware", "entity_type": "software"}], "relations": []}
    request = {"messages": [{"role": "user", "content": json.dumps(data)}]}
    cache = ModelResponseCache(tmp_path)
    _, _, key = cache.get(namespace="fixture", endpoint_hash="hashed-endpoint", request=request,
        compute=lambda: {"entities": [{"key": "f", "accepted": False, "reason": "background"}]})
    row = {"cache_trace": [{"phase": "review", "request_hash": key}]}
    proposals, verdicts = proposal_review(row, tmp_path, expected_text=data["current_chunk"])
    assert proposals[0]["key"] == "f" and verdicts["f"]["accepted"] is False
    with pytest.raises(ValueError, match="片段"):
        proposal_review(row, tmp_path, expected_text="Another source")
    path = tmp_path / (key + ".json")
    envelope = json.loads(path.read_text())
    envelope["response"]["entities"][0]["accepted"] = True
    path.write_text(json.dumps(envelope))
    with pytest.raises(ValueError, match="校验"):
        proposal_review(row, tmp_path)


def test_failed_cases_keep_all_gold_in_fn_and_are_not_claimed_as_semantic_misses():
    from app.services.video_graph.identity import stable_hash

    from scripts.analyze_entity_gold import SCORING_RULE, analyze

    dataset = {"cases": [case([item("camera", "component")])]}
    evaluation = {"dataset_sha256": stable_hash(dataset), "scoring_rule": SCORING_RULE,
                  "extraction_version": "fixture", "results": {"fixture": {"status": "error"}}}
    diagnosis = analyze(dataset, evaluation)
    assert diagnosis["strict_counts"] == {"tp": 0, "fp": 0, "fn": 1}
    assert diagnosis["failed_or_pending_cases"] == ["fixture"]
    assert diagnosis["errors"][0]["category"] == "review"


def test_shared_component_word_does_not_falsely_accuse_another_valid_owner():
    c = case([item("Air 3 camera", "component", ["Air 3 camera"]),
              item("Mini 4 Pro camera", "component", ["camera"])],
             ["DJI Air 3", "DJI Mini 4 Pro"])
    errors, _ = classify_errors(c, [item("DJI Air 3 camera", "component", ["camera"])])
    assert all(row["category"] != "owner" for row in errors)
