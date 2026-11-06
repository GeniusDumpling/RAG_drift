"""Relation scoring regressions: ownership, direction, polarity, scope, and offline execution."""

import json
import uuid
from collections import Counter

import pytest
from app.schemas.video_graph import ChunkExtraction, QualityReview
from app.services.video_graph_identity import input_hash, policy_id
from app.services.video_graph_quality import result_hash, review_extraction

from scripts import evaluate_relation_gold as evaluation

pytestmark = pytest.mark.no_db


def entity(name, kind="product", mentions=None):
    return {"name": name, "type": kind, "mentions": mentions or [name]}


def relation(subject="A", obj="B", predicate="contains", assertion="asserted", qualifiers=None):
    row = {
        "subject": subject,
        "object": obj,
        "predicate": predicate,
        "assertion": assertion,
        "quote": "A contains B.",
    }
    if qualifiers is not None:
        row["qualifiers"] = qualifiers
    return row


def case(entities=None, relations=None):
    return {
        "id": "sample",
        "language": "en",
        "models": [],
        "source": {
            "title": "A",
            "url": "https://example.test/a",
            "chunk_id": str(uuid.uuid4()),
            "start_seconds": 0,
            "end_seconds": 1,
        },
        "text": ["A contains B."],
        "entities": entities or [entity("A"), entity("B", "component")],
        "relations": relations if relations is not None else [relation()],
    }


def prediction(entities=None, relations=None):
    entities = entities or [entity("A"), entity("B", "component")]
    return ChunkExtraction.model_validate(
        {
            "entities": [
                {
                    "key": e["name"],
                    "surface_text": e["mentions"][0],
                    "canonical_name": e["name"],
                    "entity_type": e["type"],
                }
                for e in entities
            ],
            "relations": [
                {**r, "confidence": 0.9}
                for r in (relations if relations is not None else [relation()])
            ],
        }
    )


def counts(detail, metric="assertion"):
    return tuple(detail[metric][key] for key in ("tp", "fp", "fn"))


def reviewed_empty_output(current):
    material = evaluation.material_for(current)
    output = ChunkExtraction(schema_version=2)
    output.review = QualityReview(
        policy=policy_id(),
        input_hash=input_hash(material, material.chunks[0]),
        result_hash=result_hash(output),
        candidate_entities=0,
        candidate_relations=0,
    )
    return output.model_dump()


def test_correct_edge_matches_all_metrics_without_identical_evidence_boundaries():
    answer = case()
    answer["relations"][0]["quote"] = "contains B"
    detail = evaluation.score_relations(answer, prediction())
    assert all(counts(detail, metric) == (1, 0, 0) for metric in evaluation.METRICS)


def test_direction_matters_for_composition():
    detail = evaluation.score_relations(case(), prediction(relations=[relation("B", "A")]))
    assert counts(detail) == (0, 1, 1)


def test_compatibility_is_symmetric_and_reverse_duplicates_do_not_inflate_credit():
    answer = case(relations=[relation(predicate="compatible_with")])
    output = prediction(
        relations=[relation(predicate="compatible_with"), relation("B", "A", "compatible_with")]
    )
    detail = evaluation.score_relations(answer, output)
    assert counts(detail) == (1, 0, 0)
    assert detail["predicted"][0]["source_indices"] == [0, 1]


def test_wrong_polarity_is_one_fp_and_one_fn_but_triple_still_matches():
    detail = evaluation.score_relations(
        case(), prediction(relations=[relation(assertion="uncertain")])
    )
    assert counts(detail, "triple") == (1, 0, 0)
    assert counts(detail) == (0, 1, 1)


def test_missing_condition_does_not_earn_strict_qualified_credit():
    answer = case(
        relations=[relation(qualifiers={"modality": "conditional", "scope": "if enabled"})]
    )
    detail = evaluation.score_relations(answer, prediction())
    assert counts(detail) == (1, 0, 0)
    assert counts(detail, "qualified") == (0, 1, 1)


def test_qualifier_whitespace_normalization_and_default_actual():
    answer = case(relations=[relation(qualifiers={"mode": "Single Lens"})])
    output = prediction(
        relations=[relation(qualifiers={"modality": "actual", "mode": "single\nlens"})]
    )
    assert counts(evaluation.score_relations(answer, output), "qualified") == (1, 0, 0)
    output.relations[0].qualifiers["mode"] = "spherical mode"
    assert counts(evaluation.score_relations(answer, output), "qualified") == (0, 1, 1)


def test_type_errors_are_visible_in_relation_fp_fn_instead_of_silently_excluded():
    output = prediction(entities=[entity("A"), entity("B", "software")])
    detail = evaluation.score_relations(case(), output)
    assert counts(detail) == (0, 1, 1)
    assert detail["out_of_scope"] == []


def test_unannotated_endpoint_is_unassessed_and_does_not_remove_gold_fn():
    output = prediction(
        entities=[entity("A"), entity("C", "component")], relations=[relation("A", "C")]
    )
    detail = evaluation.score_relations(case(), output)
    assert counts(detail) == (0, 0, 1)
    assert detail["out_of_scope"] == [0]


def test_missing_predicted_entity_does_not_remove_gold_relation_from_recall():
    output = prediction(entities=[entity("A")], relations=[])
    assert counts(evaluation.score_relations(case(), output)) == (0, 0, 1)


def test_ambiguous_camera_mention_cannot_guess_an_owner_from_the_gold_edge():
    entities = [
        entity("A"),
        entity("left camera", "component", ["camera"]),
        entity("right camera", "component", ["camera"]),
    ]
    answer = case(entities, [relation("A", "left camera")])
    output = prediction(
        entities=[entity("A"), entity("camera", "component")], relations=[relation("A", "camera")]
    )
    detail = evaluation.score_relations(answer, output)
    assert counts(detail) == (0, 1, 1)
    assert detail["entity_alignment"]["ambiguous_predictions"]
    output.entities[1].canonical_name = "left camera"
    assert counts(evaluation.score_relations(answer, output)) == (1, 0, 0)


def test_explicit_wrong_component_owner_does_not_match_an_identical_surface():
    answer = case(
        [entity("A"), entity("Air 3 camera", "component", ["camera"])],
        [relation("A", "Air 3 camera")],
    )
    answer["models"] = ["DJI Air 3", "DJI Air 3S"]
    output = prediction(
        [entity("A"), entity("Air 3S camera", "component", ["camera"])],
        [relation("A", "Air 3S camera")],
    )
    assert counts(evaluation.score_relations(answer, output))[0] == 0


def test_one_prediction_cannot_satisfy_multiple_gold_relation_records():
    answer = case(relations=[relation(), relation(assertion="uncertain")])
    detail = evaluation.score_relations(answer, prediction())
    assert counts(detail, "triple") == (1, 0, 1)


def test_duplicate_entity_mentions_and_quotes_count_one_fact():
    output = prediction()
    alias = output.entities[0].model_copy(update={"key": "alias", "surface_text": "the drone"})
    output.entities.append(alias)
    output.relations.append(
        output.relations[0].model_copy(update={"subject": "alias", "quote": "contains B"})
    )
    detail = evaluation.score_relations(case(), output)
    assert counts(detail) == (1, 0, 0)
    assert detail["predicted"][0]["source_indices"] == [0, 1]


def test_maximum_matching_uses_an_augmenting_path():
    scored = evaluation.one_to_one([[0, 1], [0]], 2)
    assert (scored["tp"], scored["fp"], scored["fn"]) == (2, 0, 0)


def test_errors_and_pending_cases_stay_in_recall_denominator():
    first, second = case(), case()
    first["id"], second["id"], second["language"] = "first", "second", "zh"
    metrics = evaluation.score_all([first, second], {"first": {"status": "error"}})
    assert metrics["errors"] == ["first"] and metrics["pending"] == ["second"]
    assert metrics["overall"]["assertion"] == evaluation.measures({"fn": 2})
    assert metrics["by_language"]["zh"]["assertion"]["fn"] == 1
    assert metrics["by_predicate"]["contains"]["assertion"]["fn"] == 2


def test_malformed_or_unreviewed_saved_output_is_a_failure_not_a_false_perfect_score():
    answer = case()
    result = {
        "status": "success",
        "output": prediction().model_dump(),
        "scoring": {"assertion": {"tp": 999}},
    }
    metrics = evaluation.score_all([answer], {answer["id"]: result})
    assert metrics["errors"] == [answer["id"]]
    assert result["error_detail"]["code"] == "invalid_saved_extraction"
    assert metrics["overall"]["assertion"]["tp"] == 0


def test_saved_review_receipt_is_bound_to_the_actual_source():
    answer = case()
    output = reviewed_empty_output(answer)
    answer["text"] = ["Changed source"]
    result = {"status": "success", "output": output}
    assert evaluation.score_all([answer], {answer["id"]: result})["errors"] == [answer["id"]]


def test_nonempty_output_passes_real_source_and_review_validation():
    answer = case()
    material = evaluation.material_for(answer)
    candidate = prediction()
    verdict = {
        "entities": [
            {
                "key": e.key,
                "accepted": True,
                "canonical_name": e.surface_text,
                "identity_scope": "video",
                "identity_quote": e.surface_text,
                "identity_source": "current_chunk",
                "reason": "literal source",
            }
            for e in candidate.entities
        ],
        "relations": [
            {
                "index": 0,
                "supported": True,
                "predicate": "contains",
                "quote": "A contains B.",
                "assertion": "asserted",
                "modality": "actual",
                "reason": "explicit composition",
            }
        ],
    }
    reviewed = review_extraction(
        lambda *args: verdict, material, material.chunks[0], "", candidate, ""
    )
    result = {"status": "success", "output": reviewed.model_dump()}
    metrics = evaluation.score_all([answer], {answer["id"]: result})
    assert metrics["successful_cases"] == 1
    assert metrics["overall"]["assertion"]["f1"] == 1


@pytest.mark.parametrize("invalid", [None, [], {}, {"results": []}])
def test_bad_offline_input_never_falls_back_to_live_extraction(
    tmp_path, monkeypatch, invalid, reviewed_cli_dataset
):
    path = tmp_path / "invalid.json"
    path.write_text(json.dumps(invalid))
    monkeypatch.setattr(evaluation, "Settings", forbid_model)
    monkeypatch.setattr(evaluation, "run_case", forbid_model)
    with pytest.raises(SystemExit) as exc:
        evaluation.main(["--predictions", str(path), "--output", str(tmp_path / "scores.json")])
    assert exc.value.code == 2


@pytest.fixture
def reviewed_cli_dataset(tmp_path, monkeypatch):
    dataset = json.loads(evaluation.DEFAULT_DATASET.read_text())
    dataset["status"] = "human_reviewed"
    path = tmp_path / "reviewed-relation-gold.json"
    path.write_text(json.dumps(dataset))
    monkeypatch.setattr(evaluation, "DEFAULT_DATASET", path)
    return path


@pytest.fixture
def offline_input(tmp_path, reviewed_cli_dataset):
    dataset = json.loads(evaluation.DEFAULT_DATASET.read_text())
    selected = dataset["cases"][0]
    path = tmp_path / "predictions.json"
    path.write_text(
        json.dumps(
            {
                "extraction_version": "test-extractor",
                "results": {
                    selected["id"]: {
                        "status": "success",
                        "output": reviewed_empty_output(selected),
                        "scoring": {"assertion": {"tp": 999}},
                    },
                },
            }
        )
    )
    return path, selected


def forbid_model(*args, **kwargs):
    pytest.fail("offline execution must not construct model settings or invoke extraction")


def test_offline_cli_scores_raw_outputs_without_settings_or_model_calls(
    tmp_path, monkeypatch, offline_input
):
    path, selected = offline_input
    monkeypatch.setattr(evaluation, "Settings", forbid_model)
    monkeypatch.setattr(evaluation, "run_case", forbid_model)
    output, report = tmp_path / "scores.json", tmp_path / "report.md"
    gold_bytes = evaluation.DEFAULT_DATASET.read_bytes()
    args = [
        "--predictions",
        str(path),
        "--output",
        str(output),
        "--report",
        str(report),
        "--case-id",
        selected["id"],
    ]
    assert evaluation.main(args) == 0
    result = json.loads(output.read_text())
    assert result["execution"] == "offline_predictions"
    assert result["metrics"]["successful_cases"] == 1
    assert result["metrics"]["overall"]["assertion"]["fn"] == len(selected["relations"])
    assert result["metrics"]["overall"]["assertion"]["tp"] == 0
    assert "范围外" in report.read_text()
    assert gold_bytes == evaluation.DEFAULT_DATASET.read_bytes()
    # Repeating an identical invocation recomputes safely without calling the model.
    assert evaluation.main(args) == 0
    saved = json.loads(output.read_text())
    saved["scoring_rule"] = "obsolete-scorer"
    output.write_text(json.dumps(saved))
    with pytest.raises(SystemExit) as exc:
        evaluation.main(args)
    assert exc.value.code == 2


def test_partial_import_is_explicitly_pending_and_returns_failure(
    tmp_path, monkeypatch, offline_input
):
    path, _ = offline_input
    monkeypatch.setattr(evaluation, "Settings", forbid_model)
    output = tmp_path / "partial.json"
    assert evaluation.main(["--predictions", str(path), "--output", str(output)]) == 1
    metrics = json.loads(output.read_text())["metrics"]
    dataset = json.loads(evaluation.DEFAULT_DATASET.read_text())
    assert len(metrics["pending"]) == 19
    assert metrics["overall"]["assertion"]["fn"] == sum(
        len(c["relations"]) for c in dataset["cases"]
    )


@pytest.mark.parametrize("input_field", ["dataset", "entities", "predictions"])
def test_cli_refuses_to_overwrite_any_input_file(tmp_path, offline_input, input_field):
    prediction_path, _ = offline_input
    paths = {
        "dataset": evaluation.DEFAULT_DATASET,
        "entities": evaluation.DEFAULT_ENTITIES,
        "predictions": prediction_path,
    }
    target = paths[input_field]
    before = target.read_bytes()
    with pytest.raises(SystemExit) as exc:
        evaluation.main(["--predictions", str(prediction_path), "--output", str(target)])
    assert exc.value.code == 2 and before == target.read_bytes()


def test_dry_run_needs_no_credentials_and_writes_nothing(tmp_path, monkeypatch):
    monkeypatch.setattr(evaluation, "Settings", forbid_model)
    output = tmp_path / "dry.json"
    assert evaluation.main(["--dry-run", "--output", str(output)]) == 0
    assert not output.exists()


def test_live_runner_resumes_and_retries_only_explicit_errors(
    tmp_path, monkeypatch, reviewed_cli_dataset
):
    dataset = json.loads(evaluation.DEFAULT_DATASET.read_text())
    first, second = dataset["cases"][:2]
    calls = Counter()

    class ExtractorVersion:
        def __init__(self, settings):
            self.version = "test-live-version"

    def fake_run(current, settings, cache, attempt):
        calls[current["id"]] += 1
        if current["id"] == first["id"] and attempt == 0:
            return {"status": "error", "error_detail": {"code": "test_failure"}, "attempt": attempt}
        return {"status": "success", "output": reviewed_empty_output(current), "attempt": attempt}

    monkeypatch.setattr(evaluation, "Settings", lambda: object())
    monkeypatch.setattr(evaluation, "LLMVideoExtractor", ExtractorVersion)
    monkeypatch.setattr(evaluation, "run_case", fake_run)
    output = tmp_path / "live.json"
    args = ["--output", str(output), "--case-id", first["id"], "--case-id", second["id"]]
    assert evaluation.main(args) == 1
    assert evaluation.main(args) == 1
    assert calls == {first["id"]: 1, second["id"]: 1}
    assert evaluation.main([*args, "--retry-errors"]) == 0
    assert calls == {first["id"]: 2, second["id"]: 1}
    saved = json.loads(output.read_text())
    assert len(saved["results"][first["id"]]["prior_attempts"]) == 1


def test_draft_blocks_scoring_but_allows_dry_run(tmp_path, monkeypatch):
    dataset = json.loads(evaluation.DEFAULT_DATASET.read_text())
    dataset["status"] = "assistant_draft"
    draft = tmp_path / "draft.json"
    draft.write_text(json.dumps(dataset))
    output = tmp_path / "scores.json"
    monkeypatch.setattr(evaluation, "Settings", forbid_model)
    monkeypatch.setattr(evaluation, "run_case", forbid_model)
    args = ["--dataset", str(draft), "--output", str(output)]
    with pytest.raises(SystemExit) as exc:
        evaluation.main(args)
    assert exc.value.code == 2
    assert evaluation.main([*args, "--dry-run"]) == 0
    assert not output.exists()


def test_predicate_breakdowns_include_false_positive_only_predicates(monkeypatch):
    # This test isolates aggregation; production score_case always validates receipts first.
    monkeypatch.setattr(evaluation, "validate_extraction", lambda *args, **kwargs: None)
    answer = case()
    output = prediction(relations=[relation(predicate="compatible_with")])
    metrics = evaluation.score_all(
        [answer], {answer["id"]: {"status": "success", "output": output.model_dump()}}
    )
    assert metrics["by_predicate"]["compatible_with"]["assertion"]["fp"] == 1
    assert metrics["by_predicate"]["contains"]["assertion"]["fn"] == 1
    assert metrics["overall"]["assertion"]["f1"] == 0
