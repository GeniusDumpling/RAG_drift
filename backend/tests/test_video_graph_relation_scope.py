"""Relation review must retain grounded candidate conditions across omissions and repairs."""

from unittest.mock import Mock

import pytest
from app.schemas.video_graph import ChunkExtraction, ExtractedEntity, ExtractedRelation
from app.services.video_graph.grounding import validate_support
from app.services.video_graph.review import review_extraction, review_relation_batches
from app.services.video_graph.semantics import orient_relation_by_types
from app.services.video_graph.validation import validate_extraction

from backend.tests.test_video_graph_quality import entity, material, reviewed_pair

pytestmark = pytest.mark.no_db


def setup_review(modality="conditional"):
    text = "If Rover has an ESC fault, inspect its controller."
    if modality == "hypothetical":
        text = "Rover may have an ESC fault according to an unconfirmed report."
    source = material(text, title="Rover troubleshooting")
    draft = ChunkExtraction(
        entities=[
            entity("Rover", "product", scope="video", key="device"),
            entity("ESC fault", "fault", scope="video", key="fault"),
        ]
    )
    verdict = {
        "entities": [
            dict(
                key=e.key,
                accepted=True,
                canonical_name=e.surface_text,
                identity_scope="video",
                identity_source="current_chunk",
                identity_quote=e.surface_text,
                reason="literal",
            )
            for e in draft.entities
        ],
        "relations": [],
    }
    approved = review_extraction(lambda *_: verdict, source, source.chunks[0], "", draft, "")
    relation = ExtractedRelation(
        subject="device",
        object="fault",
        predicate="reports_fault",
        quote=text,
        assertion="asserted",
        confidence=0.9,
        qualifiers={"modality": modality, "scope": text},
    )
    return source, approved, relation


@pytest.mark.parametrize(
    "modality,assertion",
    [
        ("conditional", "asserted"),
        ("hypothetical", "uncertain"),
    ],
)
def test_omitted_review_scope_preserves_a_grounded_candidate_condition(modality, assertion):
    source, approved, candidate = setup_review(modality)
    request = Mock(
        return_value={
            "relations": [
                {"index": 0, "supported": True, "reason": "source confirms scoped claim"},
            ]
        }
    )
    output = review_relation_batches(request, source, source.chunks[0], "", approved, [candidate])
    assert len(output.relations) == 1
    assert output.relations[0].qualifiers == candidate.qualifiers
    assert output.relations[0].assertion == assertion
    assert output.review.relation_support[0]["modality"] == modality
    validate_extraction(source.chunks[0], output, material=source, require_review=True)


def test_explicit_conditional_review_can_reuse_an_unchanged_grounded_scope():
    source, approved, candidate = setup_review()
    request = Mock(
        return_value={
            "relations": [
                {
                    "index": 0,
                    "supported": True,
                    "modality": "conditional",
                    "qualifiers": {},
                    "reason": "same condition; optional scope field omitted",
                },
            ]
        }
    )
    output = review_relation_batches(request, source, source.chunks[0], "", approved, [candidate])
    assert output.relations[0].qualifiers == candidate.qualifiers
    validate_extraction(source.chunks[0], output, material=source, require_review=True)


def test_invalid_explicit_scope_does_not_fall_back_to_the_valid_old_scope():
    source, approved, candidate = setup_review()
    request = Mock(
        return_value={
            "relations": [
                {
                    "index": 0,
                    "supported": True,
                    "modality": "conditional",
                    "scope_quote": "Invented condition",
                    "reason": "wrong source",
                },
            ]
        }
    )
    output = review_relation_batches(request, source, source.chunks[0], "", approved, [candidate])
    assert not output.relations
    assert any("范围依据" in r["reason"] for r in output.review.rejected)


def test_invalid_candidate_condition_still_requires_real_source_evidence():
    source, approved, candidate = setup_review()
    candidate.qualifiers["scope"] = "Invented condition"
    request = Mock(
        return_value={
            "relations": [
                {"index": 0, "supported": True, "reason": "missing scope fields"},
            ]
        }
    )
    output = review_relation_batches(request, source, source.chunks[0], "", approved, [candidate])
    assert not output.relations


def test_format_retry_does_not_turn_an_omitted_modality_into_an_actual_verdict():
    source, approved, candidate = setup_review()
    request = Mock(
        side_effect=[
            {
                "relations": [
                    {"index": 0, "supported": True, "reason": "keep scoped claim"},
                    {"index": 1, "supported": "invalid boolean", "reason": "invalid"},
                ]
            },
            {
                "relations": [
                    {"index": 0, "supported": False, "reason": "must not override frozen decision"},
                    {"index": 1, "supported": False, "reason": "duplicate not needed"},
                ]
            },
        ]
    )
    output = review_relation_batches(
        request, source, source.chunks[0], "", approved, [candidate, candidate.model_copy()]
    )
    assert request.call_count == 2 and len(output.relations) == 1
    assert output.relations[0].qualifiers == candidate.qualifiers
    validate_extraction(source.chunks[0], output, material=source, require_review=True)


@pytest.mark.parametrize("prior", ["instruction", "capability", "hypothetical"])
def test_actual_review_clears_obsolete_candidate_modality(prior):
    text = "Rover contains a motor."
    subject = ExtractedEntity(key="s", surface_text="Rover", entity_type="product")
    obj = ExtractedEntity(key="o", surface_text="motor", entity_type="component")
    relation = ExtractedRelation(
        subject="s",
        object="o",
        predicate="contains",
        quote=text,
        assertion="asserted",
        confidence=0.9,
        qualifiers={"modality": prior, "mode": "assembly"},
    )
    if prior == "hypothetical":
        relation.qualifiers["scope"] = text
    output = validate_support(
        relation,
        subject,
        obj,
        basis="composition",
        modality="actual",
        scope_quote=None,
        source=text,
    )
    assert output.qualifiers == {"mode": "assembly"}


@pytest.mark.parametrize(
    "predicate,subject_type,object_type",
    [
        ("offered_by", "organization", "product"),
        ("has_model", "product", "product_family"),
        ("applied_to", "product", "method"),
    ],
)
def test_uniquely_typed_reversed_endpoints_are_oriented_without_creating_a_fact(
    predicate, subject_type, object_type
):
    subject = ExtractedEntity(key="s", surface_text="Alpha", entity_type=subject_type)
    obj = ExtractedEntity(key="o", surface_text="Beta", entity_type=object_type)
    relation = ExtractedRelation(
        subject="s",
        object="o",
        predicate=predicate,
        quote="Alpha Beta",
        assertion="uncertain",
        confidence=0.7,
    )
    fixed = orient_relation_by_types(relation, subject, obj)
    assert (fixed.subject, fixed.object) == ("o", "s")
    assert fixed.quote == relation.quote and fixed.assertion == relation.assertion
    assert (relation.subject, relation.object) == ("s", "o")


def test_ambiguous_or_impossible_type_pairs_are_not_direction_guessed():
    first = ExtractedEntity(key="s", surface_text="Alpha", entity_type="product")
    second = ExtractedEntity(key="o", surface_text="Beta", entity_type="product")
    relation = ExtractedRelation(
        subject="s",
        object="o",
        predicate="compatible_with",
        quote="Alpha Beta",
        assertion="asserted",
        confidence=0.7,
    )
    assert orient_relation_by_types(relation, first, second) is relation
    relation.predicate = "offered_by"
    assert orient_relation_by_types(relation, first, second) is relation


def test_direction_fix_still_requires_independent_source_support():
    accepted = reviewed_pair(
        "Vendor's Rover.", "Vendor", "organization", "Rover", "product", "offered_by"
    )
    assert len(accepted.relations) == 1
    assert accepted.review.relation_support[0]["direction_rule"] == "endpoint_types"
    # Direction correction cannot turn brand ownership into a manufacturing claim.
    rejected = reviewed_pair(
        "Vendor's Rover.", "Vendor", "organization", "Rover", "product", "manufactured_by"
    )
    assert not rejected.relations
