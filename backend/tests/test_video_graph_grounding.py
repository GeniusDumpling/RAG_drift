"""Source-claim contracts and within-video identity failures from the output audit."""

from typing import get_args

import pytest
from app.schemas.video_graph import ExtractedEntity, ExtractedRelation, Predicate
from app.services.video_graph_grounding import (
    BASIS_BY_PREDICATE,
    composition_direction,
    project_inventory,
    validate_support,
)
from app.services.video_graph_quality import (
    RelationVerdict,
    normalize_reviewed,
    review_extraction,
    seed_fault_mentions,
    validate_identity_proof,
)
from app.services.video_graph_semantics import (
    validate_entity_semantics,
    validate_relation_semantics,
)

from backend.tests.test_video_graph_quality import entity, material, reviewed_pair

pytestmark = pytest.mark.no_db


def pair(text, predicate="contains", subject_type="product", object_type="component"):
    subject = ExtractedEntity(key="s", surface_text="Rover", entity_type=subject_type)
    obj = ExtractedEntity(key="o", surface_text="motor", entity_type=object_type)
    relation = ExtractedRelation(subject="s", object="o", predicate=predicate,
                                 quote=text, assertion="asserted", confidence=0.9)
    return subject, obj, relation


def support(text, predicate="contains", basis="composition", modality="actual", scope=None):
    subject, obj, relation = pair(text, predicate)
    return validate_support(relation, subject, obj, basis=basis, modality=modality,
                            scope_quote=scope, source=text)


def test_every_predicate_has_an_explicit_claim_contract():
    assert set(BASIS_BY_PREDICATE) == set(get_args(Predicate))


@pytest.mark.parametrize("basis", ["package", "ownership", "parameter", "influence", "analogy"])
def test_reviewer_yes_does_not_override_an_incompatible_source_claim(basis):
    with pytest.raises(ValueError, match="类别与谓词"):
        support("Rover contains motor.", basis=basis)


def test_box_inventory_and_physical_parts_have_distinct_relations():
    text = "Also included is a driver and a motor."
    with pytest.raises(ValueError, match="包装随附"):
        support(text)
    assert support(text, "packaged_with", "package").predicate == "packaged_with"
    physical = "Also included is a motor installed inside the drone."
    assert support(physical).predicate == "contains"
    next_scene = "Also included is a driver. Back to the Rover: it contains a motor."
    s, o, r = pair("it contains a motor.")
    assert validate_support(r, s, o, basis="composition", modality="actual", scope_quote=None,
                            source=next_scene).predicate == "contains"


def test_company_product_ownership_cannot_be_hardware_composition():
    subject, obj, relation = pair("Company offers Rover.", subject_type="organization")
    with pytest.raises(ValueError, match="产品归属"):
        validate_relation_semantics(relation, subject, obj)
    product, company, offered = pair("Rover is a Company product.", "offered_by",
                                     object_type="organization")
    validate_relation_semantics(offered, product, company)
    assert validate_support(offered, product, company, basis="ownership", modality="actual",
                            scope_quote=None, source=offered.quote)


def test_a_series_contains_models_in_the_lineup_instead_of_physical_parts():
    series, model, relation = pair("Series has model M1.", subject_type="product_family",
                                    object_type="product")
    with pytest.raises(ValueError, match="系列不是机体"):
        validate_relation_semantics(relation, series, model)
    relation.predicate = "has_model"
    validate_relation_semantics(relation, series, model)
    assert validate_support(relation, series, model, basis="model_lineup", modality="actual",
                            scope_quote=None, source=relation.quote)


def test_ui_readout_does_not_prove_a_physical_part():
    with pytest.raises(ValueError, match="电量读数"):
        support("The remaining battery is shown in the right corner.")
    with pytest.raises(ValueError, match="参数名称"):
        validate_entity_semantics(ExtractedEntity(key="load", surface_text="CPU load",
                                                  entity_type="technology"))


def test_simulation_and_threats_do_not_become_compatibility_or_application():
    with pytest.raises(ValueError, match="模拟或类比"):
        support("Virtual sticks simulate physical joysticks.", "compatible_with", "compatibility")
    with pytest.raises(ValueError, match="影响或挑战"):
        support("Autonomy challenges conventional defenses.", "applied_to", "application")


def test_optional_installation_must_keep_its_scope():
    text = "再装上摄像头、雷达或者机械臂，开发自己的机器人。"
    with pytest.raises(ValueError, match="可选加装"):
        support(text)
    with pytest.raises(ValueError, match="范围依据"):
        support(text, modality="conditional")
    relation = support(text, modality="conditional", scope=text)
    assert relation.qualifiers == {"modality": "conditional", "scope": text}


def test_design_goal_does_not_claim_a_demonstrated_effect():
    text = "The goal is a slower descent."
    relation = support(text, "supports", "capability", "design_goal", text)
    assert relation.assertion == "uncertain"
    assert relation.qualifiers["modality"] == "design_goal"


def test_corrected_data_and_document_types_reach_the_stored_result():
    for body, original_type, corrected_type, predicate, basis in [
        ("Software uses movement records.", "technology", "data", "uses", "data_use"),
        ("Kit is packaged with user guide.", "product", "document", "packaged_with", "package"),
    ]:
        name, obj = ("Software", "movement records") if corrected_type == "data" else (
            "Kit", "user guide"
        )
        m = material(body)
        s, o, rel = pair(body, predicate, "software" if corrected_type == "data" else "product",
                         original_type)
        s.surface_text, o.surface_text = name, obj
        from app.schemas.video_graph import ChunkExtraction

        draft = ChunkExtraction(entities=[s, o], relations=[rel])
        verdict = {
            "entities": [dict(key=e.key, accepted=True, canonical_name=e.surface_text,
                              identity_scope="video", identity_source="current_chunk",
                              identity_quote=e.surface_text, reason="explicit",
                              entity_type=corrected_type if e.key == "o" else e.entity_type)
                         for e in draft.entities],
            "relations": [dict(index=0, supported=True, basis=basis, modality="actual",
                               quote=body, reason="explicit")],
        }
        result = review_extraction(
            lambda *_, reviewed=verdict: reviewed, m, m.chunks[0], "", draft, ""
        )
        assert result.entities[1].entity_type == corrected_type
        assert len(result.relations) == 1
        assert result.review.relation_support[0]["basis"] == basis


def test_data_use_requires_a_data_endpoint():
    subject, obj, relation = pair("Software uses tracks.", "uses")
    with pytest.raises(ValueError, match="数据不能强标"):
        validate_support(relation, subject, obj, basis="data_use", modality="actual",
                         scope_quote=None, source=relation.quote)


def graph_with_owners(*, resolved=False, nested=False):
    m = material("Device and remote each have a power button.")
    entities = [entity("Device", "product", scope="video", key="device"),
                entity("remote", "component", scope="video", key="remote"),
                entity("power button", "component", scope="video", key="button")]
    relations = []
    for subject in ["device", "remote"]:
        obj = "button"
        if subject == "remote" and resolved:
            entities.append(entity("power button", "component", "remote power button", "video",
                                   key="remote_button"))
            entities[2].canonical_name = "Device power button"
            obj = "remote_button"
        relations.append(ExtractedRelation(subject=subject, object=obj, predicate="contains",
                                           quote=m.chunks[0].text, assertion="asserted",
                                           confidence=0.9))
    if nested:
        relations.append(ExtractedRelation(subject="device", object="remote", predicate="contains",
                                           quote=m.chunks[0].text, assertion="asserted",
                                           confidence=0.9))
    proofs = [dict(source_index=str(i), basis="composition", modality="actual", scope_quote="")
              for i in range(len(relations))]
    return m, entities, relations, proofs


def test_same_surface_can_represent_distinct_owned_components():
    m, entities, relations, proofs = graph_with_owners(resolved=True)
    kept, relations, proofs = normalize_reviewed(entities, relations, proofs, [], m)
    assert len(kept) == 4 and len(relations) == 2
    assert relations[0].object != relations[1].object


def test_disjoint_owners_get_distinct_instances_without_losing_the_supported_compositions():
    m, entities, relations, proofs = graph_with_owners()
    rejected = []
    kept, result, _ = normalize_reviewed(entities, relations, proofs, rejected, m)
    assert len(result) == 2 and result[0].object != result[1].object
    assert len(kept) == 4 and "button" not in {entity.key for entity in kept}
    assert rejected[0]["kind"] == "identity_split"
    m, entities, relations, proofs = graph_with_owners(nested=True)
    assert len(normalize_reviewed(entities, relations, proofs, [], m)[1]) == 3


def test_owner_partition_does_not_copy_unattributed_values_or_actions():
    m, entities, relations, proofs = graph_with_owners()
    entities[2].attributes = {"voltage": "5V"}
    relations.append(ExtractedRelation(subject="button", object="device", predicate="controls",
                                       quote=m.chunks[0].text, assertion="asserted",
                                       confidence=0.8))
    proofs.append(dict(source_index="2", basis="control", modality="actual", scope_quote=""))
    rejected = []
    kept, result, _ = normalize_reviewed(entities, relations, proofs, rejected, m)
    assert len(result) == 2
    assert all(not entity.attributes for entity in kept)
    assert {row["kind"] for row in rejected} == {"identity_split", "attribute", "relation"}


def test_punctuation_aliases_are_preserved_when_adding_the_verified_owner():
    m = material("Device and remote both have a threeaxis gimbal.")
    _, entities, relations, proofs = graph_with_owners()
    entities[2].surface_text = "threeaxis gimbal"
    entities[2].canonical_name = "3-axis gimbal"
    entities[2].identity_quote = m.chunks[0].text
    for relation in relations:
        relation.quote = m.chunks[0].text
    kept, result, _ = normalize_reviewed(entities, relations, proofs, [], m)
    assert len(kept) == 4 and len(result) == 2
    assert result[0].object != result[1].object


def test_constituents_forming_a_whole_have_the_correct_composition_direction():
    s, o, r = pair("四路天線構成分級接收陣列")
    s.surface_text, o.surface_text = "四路天線", "分級接收陣列"
    corrected, rule = composition_direction(r, s, o, "composition")
    assert corrected.subject == "o" and corrected.object == "s"
    assert rule == "constituent_to_whole"
    assert composition_direction(corrected, o, s, "composition")[0] == corrected
    r.assertion = "negated"
    assert composition_direction(r, s, o, "composition")[0] == r


def test_missing_or_tampered_claim_proof_cannot_be_published():
    from app.services.video_graph import validate_extraction

    m = material("Device uses GPS.")
    result = reviewed_pair(m.chunks[0].text, "Device", "product", "GPS", "technology", "uses")
    # Rebind to this test material through its source-independent extraction helper.
    from app.services.video_graph_identity import input_hash

    result.review.input_hash = input_hash(m, m.chunks[0])
    validate_extraction(m.chunks[0], result, material=m, require_review=True)
    result.review.relation_support[0]["basis"] = "analogy"
    with pytest.raises(ValueError, match="类别与谓词"):
        validate_extraction(m.chunks[0], result, material=m, require_review=True)


@pytest.mark.parametrize("canonical", ["DJI Neo", "DJI Neo 2 Pro"])
def test_identity_proof_preserves_model_generation_and_variant(canonical):
    m = material("DJI Neo 2", title="DJI Neo 2")
    e = entity("DJI Neo 2", "product", canonical)
    with pytest.raises(ValueError, match="代际"):
        validate_identity_proof(e, m, m.chunks[0], "")


def test_misplaced_scope_does_not_override_candidate_polarity():
    verdict = RelationVerdict.model_validate(dict(index=0, supported=True,
        assertion="instruction", modality="instruction", basis="use", reason="explicit"))
    assert verdict.assertion is None and verdict.modality == "instruction"


def test_literal_fault_seed_adds_only_candidates_and_keeps_existing_mentions():
    from app.schemas.video_graph import ChunkExtraction

    m = material("It doesn't want to turn on. 无法开机。 Turn on the drone.")
    seeded = seed_fault_mentions(ChunkExtraction(), m.chunks[0])
    assert {e.surface_text for e in seeded.entities} == {"doesn't want to turn on", "无法开机"}
    assert not seeded.relations
    assert seed_fault_mentions(seeded, m.chunks[0]) == seeded


def test_military_inventory_is_projected_with_its_source_scope():
    text = "中國閱兵。反無人機方隊包括高能激光。"
    s, o, r = pair("反無人機方隊包括高能激光", subject_type="organization",
                   object_type="technology")
    s.surface_text, o.surface_text = "反無人機方隊", "高能激光"
    projected, basis, rule = project_inventory(r, s, o, "composition", text)
    assert projected.predicate == "equipped_with" and rule == "military_inventory"
    assert validate_support(projected, s, o, basis=basis, modality="actual",
                            scope_quote=None, source=text) == projected
    reverse = r.model_copy(update={"subject": "o", "object": "s"})
    assert project_inventory(reverse, o, s, "composition", text)[0] == projected
    for body in ["反無人機方隊不包括高能激光", "Company includes 高能激光"]:
        changed = r.model_copy(update={"quote": body})
        assert project_inventory(changed, s, o, "composition", body)[2] == ""


def test_partial_graph_does_not_prove_that_a_device_and_subsystem_are_independent():
    m, entities, relations, proofs = graph_with_owners()
    entities[1].entity_type = "technology"
    entities[1].surface_text = entities[1].canonical_name = "array"
    kept, result, _ = normalize_reviewed(entities, relations, proofs, [], m)
    assert len(kept) == 3 and len(result) == 2
    assert result[0].object == result[1].object


def test_uphill_composition_requires_an_explicit_membership_statement():
    s, o, r = pair("The spool adds weight to the drone.", subject_type="component",
                   object_type="product")
    with pytest.raises(ValueError, match="包含"):
        validate_support(r, s, o, basis="composition", modality="actual", scope_quote=None,
                         source=r.quote)


def test_protection_transfer_and_publication_are_not_stronger_relations():
    s, o, r = pair("Cover provides protection to the lens.")
    s.surface_text, o.surface_text = "Cover", "lens"
    with pytest.raises(ValueError, match="保护关系"):
        validate_support(r, s, o, basis="composition", modality="actual", scope_quote=None,
                         source=r.quote)
    assert support("The protective housing contains the motor.")
    with pytest.raises(ValueError, match="结果迁移"):
        support("Low-speed tuning results carry over to high-speed tuning.",
                "compatible_with", "compatibility")
    with pytest.raises(ValueError, match="发布或推出"):
        support("The minister launched the initiative.", "developed_by", "development")


@pytest.mark.parametrize("name", ["speed", "acceleration", "emissivity"])
def test_scalar_parameter_names_are_not_technical_entities(name):
    with pytest.raises(ValueError, match="参数名称"):
        validate_entity_semantics(entity(name, "technology"))


@pytest.mark.parametrize("name,kind", [("Flight mode", "software"), ("Fly screen", "software"),
    ("virtual joystick", "component"), ("information card", "product"),
    ("home point", "technology"), ("lift", "technology")])
def test_source_objects_must_fit_the_actual_entity_type(name, kind):
    with pytest.raises(ValueError):
        validate_entity_semantics(entity(name, kind))
    validate_entity_semantics(entity("flight mode switching", "method"))
    validate_entity_semantics(entity("GPS", "technology"))


def test_self_description_mounting_and_workflow_order_are_not_composition_or_application():
    with pytest.raises(ValueError, match="本身成为"):
        support("无人机变成能够自主决策的系统。")
    s, o, r = pair("The camera has a lens on a gimbal.", subject_type="component")
    s.surface_text, o.surface_text = "camera", "gimbal"
    # 'has' only proves camera -> lens; it cannot establish camera -> gimbal.
    with pytest.raises(ValueError, match="安装在"):
        validate_support(r, s, o, basis="composition", modality="actual", scope_quote=None,
                         source=r.quote)
    s, o, r = pair("然后upload，然后把固件刷写进去。", "applied_to", "method", "method")
    with pytest.raises(ValueError, match="两个步骤"):
        validate_support(r, s, o, basis="application", modality="instruction", scope_quote=None,
                         source=r.quote)


def test_past_affiliation_keeps_its_source_time_in_the_published_fact():
    s, o, r = pair("甲有乙公司工作背景。", "affiliated_with", "person", "organization")
    result = validate_support(r, s, o, basis="affiliation", modality="actual", scope_quote=None,
                              source=r.quote)
    assert result.qualifiers == {"time": "past", "scope": r.quote}
    assert validate_support(result, s, o, basis="affiliation", modality="actual", scope_quote=None,
                            source=r.quote) == result


@pytest.mark.parametrize("name,kind", [("map", "software"), ("机身设计", "component"),
                                      ("姿态指令", "technology")])
def test_resources_designs_and_commands_are_not_programs_parts_or_technologies(name, kind):
    with pytest.raises(ValueError):
        validate_entity_semantics(entity(name, kind))


def test_failure_to_eliminate_every_consequence_does_not_negate_mitigation():
    s, o, r = pair("No helmet makes every crash harmless.", "mitigates", "product", "fault")
    r.assertion = "negated"
    with pytest.raises(ValueError, match="量词"):
        validate_support(r, s, o, basis="mitigation", modality="actual", scope_quote=None,
                         source=r.quote)
    r.quote = "The helmet does not reduce the impact."
    assert validate_support(r, s, o, basis="mitigation", modality="actual", scope_quote=None,
                            source=r.quote).assertion == "negated"
