"""Acceptance matching must recognize encoding changes without accepting false paths."""

import pytest
from app.schemas.video_graph import ChunkExtraction

from scripts.evaluate_video_graph_quality import retention_proof, score

pytestmark = pytest.mark.no_db


def graph(edges):
    names = sorted({name for edge in edges for name in edge[:2]})
    return ChunkExtraction.model_validate(
        {
            "entities": [
                {"key": name, "surface_text": name, "entity_type": "product"} for name in names
            ],
            "relations": [
                {
                    "subject": s,
                    "object": o,
                    "predicate": p,
                    "assertion": a,
                    "quote": "source fixture",
                    "confidence": 0.9,
                }
                for s, o, p, a in edges
            ],
        }
    )


def case(predicate="contains", outcome="supported", subject="A", obj="C"):
    return {
        "case_id": "fixture",
        "title": "",
        "expectation": outcome,
        "baseline_relation": {
            "subject": subject,
            "subject_type": "product",
            "object": obj,
            "object_type": "product",
            "predicate": predicate,
            "assertion": "asserted",
        },
    }


def test_transitive_component_fact_has_explicit_edge_proof():
    output = graph([("A", "B", "contains", "asserted"), ("B", "C", "contains", "asserted")])
    assert retention_proof(case(), output) == {"kind": "composition", "edges": [0, 1]}


def test_siblings_and_negative_component_paths_never_count_as_retained():
    siblings = graph([("B", "A", "contains", "asserted"), ("B", "C", "contains", "asserted")])
    assert retention_proof(case(), siblings) is None
    negative = graph([("A", "B", "contains", "asserted"), ("B", "C", "contains", "negated")])
    assert retention_proof(case(), negative) is None


def test_reversed_unsupported_compatibility_cannot_hide_behind_uncertainty():
    output = graph([("C", "A", "compatible_with", "uncertain")])
    assert retention_proof(case("compatible_with", "issue"), output) is not None


def test_explicit_brand_prefix_does_not_lose_a_named_model():
    output = graph([("DJI AP100", "C", "contains", "asserted")])
    assert retention_proof(case(subject="AP100"), output) is not None


def test_composite_fact_needs_every_declared_source_component():
    target = case("uses", subject="A", obj="B and C")
    target["composite_object_parts"] = ["B", "C"]
    assert retention_proof(target, graph([("A", "B", "uses", "asserted")])) is None
    output = graph([("A", "B", "uses", "asserted"), ("A", "C", "supports", "asserted")])
    assert retention_proof(target, output) == {"kind": "explicit_composite", "edges": [0, 1]}


def test_missing_or_error_cases_and_zero_positive_retention_cannot_pass_gate():
    positive, negative = case(), case(outcome="issue")
    negative["case_id"] = "negative"
    assert not score([positive, negative], {})["gate"]
    assert not score(
        [positive, negative],
        {
            "fixture": {"status": "error"},
            "negative": {"status": "success", "target_present": False},
        },
    )["gate"]


def test_explicit_bilingual_composite_alias_still_needs_every_part():
    target = case("uses", subject="A", obj="Edge AI target recognition")
    target["composite_object_parts"] = ["Edge AI", "target recognition"]
    target["composite_object_mentions"] = {"Edge AI": ["边缘运算 Edge AI"]}
    first = ("A", "边缘运算 Edge AI", "supports", "asserted")
    assert retention_proof(target, graph([first])) is None
    assert retention_proof(target, graph([first, ("A", "target recognition", "uses", "asserted")]))


def test_full_fault_state_keeps_the_complete_original_fault_phrase():
    target = case("reports_fault", subject="A", obj="doesn't want to turn on")
    target["baseline_relation"]["object_type"] = "fault"
    output = graph([("A", "DJI doesn't want to turn on anymore", "reports_fault", "asserted")])
    output.entities[1].entity_type = "fault"
    assert retention_proof(target, output) is not None
    output.entities[1].surface_text = "DJI turns on with no fault"
    assert retention_proof(target, output) is None


def test_owned_specific_component_preserves_the_complete_generic_component_name():
    target = case("contains", subject="A", obj="tele camera")
    target["baseline_relation"]["object_type"] = "component"
    output = graph([("A", "separate tele camera", "contains", "asserted")])
    output.entities[1].entity_type = "component"
    output.entities[1].canonical_name = "A tele camera"
    assert retention_proof(target, output) is not None
    output.entities[1].surface_text = "tele camera button"
    output.entities[1].canonical_name = "A tele camera button"
    assert retention_proof(target, output) is None
