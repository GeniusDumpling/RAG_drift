"""Canonical workflow roles and evidence-aware questions must not invent facts."""

from unittest.mock import Mock

import pytest
from app.schemas.video_graph import ChunkExtraction, ExtractedRelation
from app.services.video_graph import validate_extraction
from app.services.video_graph_quality import (
    classify_relations,
    relation_payload,
    review_extraction,
    review_relation_batches,
)
from app.services.video_graph_semantics import canonicalize_relation_expression

from backend.tests.test_video_graph_quality import entity, material

pytestmark = pytest.mark.no_db


def facts(text, names=(("Rover", "product"), ("Editor", "software"))):
    source = material(text, title="Rover guide")
    draft = ChunkExtraction(
        entities=[
            entity(name, kind, scope="video", key=str(i)) for i, (name, kind) in enumerate(names)
        ]
    )
    verdict = {
        "entities": [
            {
                "key": e.key,
                "accepted": True,
                "canonical_name": e.surface_text,
                "identity_scope": "video",
                "identity_source": "current_chunk",
                "identity_quote": e.surface_text,
                "reason": "source",
            }
            for e in draft.entities
        ],
        "relations": [],
    }
    approved = review_extraction(lambda *_: verdict, source, source.chunks[0], "", draft, "")
    return source, approved


@pytest.mark.parametrize("predicate", ["uses", "connected_to"])
@pytest.mark.parametrize("polarity", ["asserted", "uncertain", "negated"])
def test_external_workflow_has_one_canonical_direction_and_preserves_scope(predicate, polarity):
    text = "If needed, Rover uses Editor to edit video clips."
    source, approved = facts(text)
    device, tool = approved.entities
    relation = ExtractedRelation(
        subject=device.key,
        object=tool.key,
        predicate=predicate,
        quote=text,
        assertion=polarity,
        confidence=0.8,
        qualifiers={"modality": "conditional", "scope": "If needed", "mode": "offline"},
    )
    normalized, rule = canonicalize_relation_expression(relation, device, tool, text)
    assert (normalized.subject, normalized.predicate, normalized.object) == (
        tool.key,
        "applied_to",
        device.key,
    )
    assert normalized.assertion == relation.assertion
    assert normalized.qualifiers == relation.qualifiers and normalized.quote == relation.quote
    assert rule == "external_workflow_to_device"
    again, rule = canonicalize_relation_expression(normalized, tool, device, text)
    assert again.model_dump() == normalized.model_dump() and not rule


@pytest.mark.parametrize(
    "tool,kind,text",
    [
        ("firmware", "software", "Download the app to refresh firmware on Rover."),
        ("PX4", "software", "Rover uses PX4 for onboard flight control."),
        ("Navigator", "software", "Rover uses Navigator for route calculation."),
        ("GPS", "technology", "Rover uses GPS for positioning."),
        ("laptop", "product", "Rover is connected to laptop to edit video clips."),
    ],
)
def test_onboard_use_and_hardware_connections_are_not_reversed(tool, kind, text):
    source, approved = facts(text, (("Rover", "product"), (tool, kind)))
    device, resource = approved.entities
    relation = ExtractedRelation(
        subject=device.key,
        object=resource.key,
        predicate="uses",
        quote=text,
        assertion="asserted",
        confidence=0.8,
    )
    normalized, rule = canonicalize_relation_expression(relation, device, resource, text)
    assert normalized.model_dump() == relation.model_dump() and not rule


def test_canonicalization_requires_literal_workflow_evidence():
    text = "Rover and Editor are mentioned."
    source, approved = facts(text)
    device, tool = approved.entities
    relation = ExtractedRelation(
        subject=device.key,
        object=tool.key,
        predicate="uses",
        quote="Rover uses Editor to edit video clips.",
        assertion="asserted",
        confidence=0.8,
    )
    normalized, rule = canonicalize_relation_expression(relation, device, tool, text)
    assert normalized.model_dump() == relation.model_dump() and not rule


@pytest.mark.parametrize(
    "types",
    [
        ("product", "organization"),
        ("product_family", "product"),
        ("software", "product"),
        ("product", "component"),
        ("component", "component"),
        ("product", "product"),
    ],
)
def test_role_questions_do_not_create_edges_without_model_source_support(types):
    source, approved = facts(
        "Alpha is discussed beside Beta.", (("Alpha", types[0]), ("Beta", types[1]))
    )
    request = Mock(
        return_value={
            "relations": [
                {"index": 0, "supported": False, "reason": "cooccurrence only"},
            ]
        }
    )
    candidates, _ = classify_relations(request, source, source.chunks[0], "", approved)
    assert not candidates and request.call_count == 1
    payload = request.call_args.args[1]
    assert payload["pairs"][0]["question"]
    assert payload["current_chunk"] == source.chunks[0].text


def test_canonical_candidate_still_needs_independent_review():
    text = "Rover uses Editor to edit video clips."
    source, approved = facts(text)
    request = Mock(
        return_value={
            "relations": [
                {
                    "index": 0,
                    "supported": True,
                    "predicate": "uses",
                    "quote": text,
                    "assertion": "asserted",
                    "reason": "candidate",
                },
            ]
        }
    )
    candidates, _ = classify_relations(request, source, source.chunks[0], "", approved)
    assert candidates[0].predicate == "applied_to" and candidates[0].subject == "1"
    review = Mock(
        return_value={
            "relations": [
                {"index": 0, "supported": False, "reason": "independent rejection"},
            ]
        }
    )
    output = review_relation_batches(review, source, source.chunks[0], "", approved, candidates)
    assert not output.relations and len(output.entities) == 2
    assert review.call_args.args[1]["relations"][0]["predicate"] == "applied_to"


@pytest.mark.parametrize("repair", [False, True])
def test_invalid_quote_is_flagged_and_requires_an_actual_literal_repair(repair):
    text = "Controller controls the motors."
    source, approved = facts(text, (("Controller", "component"), ("motors", "component")))
    edge = ExtractedRelation(
        subject="0",
        object="1",
        predicate="controls",
        quote="Controller controls motors.",
        assertion="asserted",
        confidence=0.8,
    )
    payload = relation_payload(source, source.chunks[0], approved.entities, [edge])
    assert payload["relations"][0]["quote_valid"] is False
    decision = {"index": 0, "supported": True, "reason": "source confirms"}
    if repair:
        decision["quote"] = text
    request = Mock(return_value={"relations": [decision]})
    output = review_relation_batches(request, source, source.chunks[0], "", approved, [edge])
    assert len(output.relations) == int(repair)
    if repair:
        assert output.relations[0].quote == text
    validate_extraction(source.chunks[0], output, material=source, require_review=True)


def test_review_cannot_restore_a_noncanonical_external_workflow_expression():
    text = "Rover uses Editor to edit video clips."
    source, approved = facts(text)
    edge = ExtractedRelation(
        subject="0",
        object="1",
        predicate="uses",
        quote=text,
        assertion="asserted",
        confidence=0.8,
        qualifiers={"mode": "offline"},
    )
    request = Mock(
        return_value={
            "relations": [
                {"index": 0, "supported": True, "predicate": "uses", "reason": "confirmed"},
            ]
        }
    )
    output = review_relation_batches(request, source, source.chunks[0], "", approved, [edge])
    assert output.relations[0].predicate == "applied_to"
    assert output.relations[0].subject == "1" and output.relations[0].object == "0"
    assert output.relations[0].qualifiers == {"mode": "offline"}
    assert "external_workflow_to_device" in output.review.relation_support[0]["direction_rule"]
    validate_extraction(source.chunks[0], output, material=source, require_review=True)


@pytest.mark.parametrize(
    "line_range,accepted", [([0, 1], True), ([1, 0], False), ([-1, 0], False), ([0, 90], False)]
)
def test_review_can_select_exact_source_lines_without_retyping_or_fuzzy_matching(
    line_range, accepted
):
    text = "Controller controls the motors.\nIt adjusts the speed."
    source, approved = facts(text, (("Controller", "component"), ("motors", "component")))
    edge = ExtractedRelation(
        subject="0",
        object="1",
        predicate="controls",
        quote="Controller controls motors.",
        assertion="asserted",
        confidence=0.8,
    )
    request = Mock(
        return_value={
            "relations": [
                {
                    "index": 0,
                    "supported": True,
                    "quote_lines": line_range,
                    "reason": "literal lines",
                }
            ]
        }
    )
    result = review_relation_batches(request, source, source.chunks[0], "", approved, [edge])
    assert len(result.relations) == int(accepted)
    assert request.call_args.args[1]["source_lines"][0] == {
        "line": 0,
        "text": "Controller controls the motors.",
    }
    if accepted:
        assert result.relations[0].quote == text
    validate_extraction(source.chunks[0], result, material=source, require_review=True)


def test_source_line_selection_cannot_bypass_quote_length_limit():
    text = "Controller controls the motors. " + "x" * 6000
    source, approved = facts(text, (("Controller", "component"), ("motors", "component")))
    edge = ExtractedRelation(
        subject="0",
        object="1",
        predicate="controls",
        quote="Controller controls motors.",
        assertion="asserted",
        confidence=0.8,
    )
    request = Mock(
        return_value={
            "relations": [
                {
                    "index": 0,
                    "supported": True,
                    "quote_lines": [0, 0],
                    "reason": "literal lines",
                }
            ]
        }
    )
    result = review_relation_batches(request, source, source.chunks[0], "", approved, [edge])
    assert not result.relations
    assert any("6000" in row["reason"] for row in result.review.rejected)


def test_missing_required_quote_repair_gets_one_retry_and_pins_other_negative_decisions():
    from app.services.video_graph_quality import request_verdicts

    payload = {
        "current_chunk": "First line.\nExact second line.",
        "relations": [
            {"index": 0, "quote_valid": False},
            {"index": 1, "quote_valid": False},
        ],
    }
    request = Mock(
        side_effect=[
            {
                "relations": [
                    {"index": 0, "supported": False, "reason": "no fact"},
                    {"index": 1, "supported": True, "reason": "forgot evidence repair"},
                ]
            },
            {
                "relations": [
                    {"index": 0, "supported": True, "quote_lines": [0, 0], "reason": "cannot flip"},
                    {
                        "index": 1,
                        "supported": True,
                        "quote_lines": [1, 1],
                        "reason": "literal repair",
                    },
                ]
            },
        ]
    )
    verdict, errors = request_verdicts(request, "", payload, set(), 2)
    assert request.call_count == 2 and not errors
    assert not verdict.relations[0].supported
    assert verdict.relations[1].supported and verdict.relations[1].quote_lines == (1, 1)
    assert request.call_args.args[1]["format_correction"]["quote_repair_indices"] == [1]


def test_unrepaired_positive_is_quarantined_after_one_retry():
    from app.services.video_graph_quality import request_verdicts

    request = Mock(
        return_value={
            "relations": [
                {
                    "index": 0,
                    "supported": True,
                    "quote": "Invented quote",
                    "reason": "still missing",
                },
            ]
        }
    )
    verdict, errors = request_verdicts(
        request,
        "",
        {
            "current_chunk": "Actual text",
            "relations": [{"index": 0, "quote_valid": False}],
        },
        set(),
        1,
    )
    assert request.call_count == 2 and not verdict.relations[0].supported
    assert errors[0]["key"] == "relation:0"
