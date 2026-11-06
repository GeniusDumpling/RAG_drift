"""Release gates must use all outputs and an immutable source-review sample."""

import json
from copy import deepcopy

import pytest
from app.services.video_graph_identity import extraction_version, stable_hash

from backend.tests.test_video_graph_quality import material, reviewed
from scripts.evaluate_video_graph_quality import (
    EVALUATION_PROTOCOL,
    retarget_result,
    source_groups,
)
from scripts.review_video_graph_quality import check_review, make_sample, release_score

pytestmark = pytest.mark.no_db


def criteria():
    return json.load(open("scripts/evaluation_data/video_graph_release_criteria.json"))


def target():
    return {"errors": [], "pending": [], "supported_retention": 0.85,
            "bad_target_rejection": 0.96}


def test_source_precision_gate_is_separate_from_old_target_regression():
    assert release_score(target(), ["supported"] * 51 + ["issue"] * 3 + ["review"] * 6,
                         criteria())["release_gate"]
    assert not release_score(target(), ["supported"] * 56 + ["issue"] * 4,
                             criteria())["release_gate"]
    assert not release_score(target(), ["supported"] * 53 + ["review"] * 7,
                             criteria())["release_gate"]
    assert not release_score(target(), ["supported"] * 59, criteria())["release_gate"]
    assert not release_score(target(), ["supported"] * 59 + ["pending"],
                             criteria())["release_gate"]
    errors = {**target(), "errors": ["case1"]}
    assert not release_score(errors, ["supported"] * 60, criteria())["release_gate"]


def fixture_evaluation():
    m = material()
    output = reviewed(m)
    chunk = m.chunks[0]
    dataset = {"cases": [{
        "case_id": "case1", "video_id": str(m.id), "chunk_id": str(chunk.id),
        "title": m.title, "url": m.url, "text": chunk.text, "expectation": "supported",
        "start_seconds": chunk.start_seconds, "end_seconds": chunk.end_seconds,
        "baseline_relation": {"subject": "The Neo", "subject_type": "product",
                              "object": "GPS", "object_type": "technology", "predicate": "uses",
                              "assertion": "asserted"},
    }]}
    version = extraction_version("video-graph-v2", "fixture")
    evaluation = {"target_version": version, "dataset_hash": stable_hash(dataset),
                  "evaluation_protocol": EVALUATION_PROTOCOL,
                  "results": {"case1": {"status": "success", "expectation": "supported",
                                        "target_present": False, "output": output.model_dump()}}}
    rule = {**criteria(), "output_review": {"sample_size": 1, "seed": 20261102,
                                            "minimum_supported": 1, "maximum_issue": 0,
                                            "maximum_review": 0}}
    return evaluation, dataset, rule, version


def test_sampling_checks_receipts_not_cached_matching_booleans():
    evaluation, dataset, rule, version = fixture_evaluation()
    sample = make_sample(evaluation, dataset, rule, expected_version=version)
    assert sample["target_metrics"]["supported_retained"] == 1
    assert sample["samples"][0]["judgment"] == "pending"
    evaluation["results"]["case1"]["output"]["relations"][0]["quote"] = "Invented GPS claim."
    with pytest.raises(ValueError):
        make_sample(evaluation, dataset, rule, expected_version=version)


def test_incomplete_runs_changed_labels_and_wrong_versions_cannot_be_sampled():
    evaluation, dataset, rule, version = fixture_evaluation()
    with pytest.raises(ValueError, match="抽取版本"):
        make_sample(evaluation, dataset, rule, expected_version="other-version")
    changed = deepcopy(dataset)
    changed["cases"][0]["expectation"] = "issue"
    with pytest.raises(ValueError, match="标签"):
        make_sample(evaluation, changed, rule, expected_version=version)
    evaluation["results"]["case1"]["status"] = "error"
    with pytest.raises(ValueError, match="全部样本"):
        make_sample(evaluation, dataset, rule, expected_version=version)


def test_only_judgment_and_note_can_change_after_sampling():
    evaluation, dataset, rule, version = fixture_evaluation()
    expected = make_sample(evaluation, dataset, rule, expected_version=version)
    review = deepcopy(expected)
    review["samples"][0].update(judgment="supported", note="The source explicitly supports use.")
    assert check_review(review, expected)["output_gate"]
    review["samples"][0]["source_text"] = "Replacement source."
    with pytest.raises(ValueError, match="被修改"):
        check_review(review, expected)
    review = deepcopy(expected)
    review["criteria"]["output_review"]["minimum_supported"] = 0
    with pytest.raises(ValueError, match="门槛"):
        check_review(review, expected)


def test_multiple_targets_share_the_same_extraction_and_are_scored_separately():
    evaluation, dataset, rule, version = fixture_evaluation()
    first = dataset["cases"][0]
    second = deepcopy(first)
    second.update(case_id="case2", expectation="issue")
    second["baseline_relation"]["object"] = "GLONASS"
    groups = source_groups([second, first])
    assert [[c["case_id"] for c in group] for group in groups] == [["case1", "case2"]]
    row = {**evaluation["results"]["case1"], "case_id": "case1"}
    a, b = (retarget_result(row, case) for case in groups[0])
    assert a["output"] == b["output"] and a["target_present"] and not b["target_present"]
    assert a["source_input_hash"] == b["source_input_hash"]
    dataset["cases"].append(second)
    evaluation.update(dataset_hash=stable_hash(dataset), results={"case1": a, "case2": b})
    make_sample(evaluation, dataset, rule, expected_version=version)
    b["output"]["relations"][0]["confidence"] = 0.2
    with pytest.raises(ValueError, match="共享同一次"):
        make_sample(evaluation, dataset, rule, expected_version=version)
