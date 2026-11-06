"""Name, alias and owner rules must preserve source evidence and distinct objects."""

import uuid

import pytest
from app.schemas.video_graph import ChunkExtraction, ExtractedEntity
from app.services.video_graph import VideoChunk, VideoMaterial, validate_extraction
from app.services.video_graph_grounding import source_span
from app.services.video_graph_identity import entity_identity
from app.services.video_graph_normalization import core_name, normalize_entity_review
from app.services.video_graph_quality import relation_pairs, review_extraction

from scripts.evaluate_entity_gold import prediction_entities

pytestmark = pytest.mark.no_db


def source(text):
    chunk = VideoChunk(uuid.uuid4(), text, "transcript_segment", 0, 30)
    return VideoMaterial(
        uuid.uuid4(),
        "fixture",
        "Equipment discussion",
        "https://example.com",
        "",
        None,
        "en",
        (chunk,),
    )


def row(key, surface, kind, canonical=None, scope="video", **extra):
    return ExtractedEntity(
        key=key,
        surface_text=surface,
        entity_type=kind,
        canonical_name=canonical or surface,
        identity_scope=scope,
        identity_source="current_chunk",
        identity_quote=surface,
        **extra,
    )


def approved(m, entities):
    decisions = [
        {
            "key": e.key,
            "accepted": True,
            "canonical_name": e.canonical_name,
            "identity_scope": e.identity_scope,
            "identity_source": e.identity_source,
            "identity_quote": e.identity_quote,
            "accepted_attributes": list(e.attributes),
            "reason": "Explicit source fixture",
        }
        for e in entities
    ]
    return review_extraction(
        lambda *_: {"entities": decisions, "relations": []},
        m,
        m.chunks[0],
        "",
        ChunkExtraction(entities=entities),
        "",
    )


def normalize(m, entities):
    return normalize_entity_review(m, m.chunks[0], "", approved(m, entities))


@pytest.mark.parametrize(
    "surface,core,attrs",
    [
        ("1/1.1英寸的方形传感器", "方形传感器", {"sensor_size": "1/1.1英寸"}),
        ("等效24mm f1.7的广角镜头", "广角镜头", {"focal_length": "24mm", "aperture": "f1.7"}),
        ("1-inch sensor", "sensor", {"sensor_size": "1-inch"}),
        ("48 MP camera", "camera", {"resolution": "48 MP"}),
        ("两个传感器", "传感器", {"count": "两个"}),
    ],
)
def test_numeric_modifiers_become_literal_attributes(surface, core, attrs):
    m = source(surface)
    out = normalize(m, [row("s", surface, "component")])
    assert {e.surface_text for e in out.entities} == {surface, core}
    assert {e.canonical_name for e in out.entities} == {core}
    assert all(e.attributes == attrs for e in out.entities)
    assert len(prediction_entities(out)) == 1
    validate_extraction(m.chunks[0], out, material=m, require_review=True)


@pytest.mark.parametrize(
    "surface",
    [
        "32-bit ARM Cortex M7",
        "CMOS sensor",
        "infrared camera",
        "new 1-inch sensor",
        "1/1.3 or 1/1.1-inch sensor",
    ],
)
def test_unparsed_modifiers_and_named_hardware_are_preserved(surface):
    assert core_name(row("x", surface, "component")) == (surface, {})


def test_model_numbers_are_never_moved_into_attributes():
    assert core_name(row("x", "DJI Mini 4K", "product")) == ("DJI Mini 4K", {})


def test_name_separation_does_not_overwrite_an_existing_attribute():
    m = source("1-inch sensor; another configuration is 2-inch.")
    out = normalize(
        m, [row("s", "1-inch sensor", "component", attributes={"sensor_size": "2-inch"})]
    )
    assert all(e.attributes["sensor_size"] == "2-inch" for e in out.entities)
    assert any(r["kind"] == "attribute" for r in out.review.rejected)


def test_firmware_aliases_share_identity_and_keep_each_raw_mention():
    m = source("Alpha X1 runs firmware. The drone firmware was updated.")
    out = normalize(
        m,
        [
            row("d", "Alpha X1", "product", scope="global"),
            row("f1", "firmware", "software"),
            row("f2", "drone firmware", "software", "Alpha X1 firmware"),
        ],
    )
    firmware = [e for e in out.entities if e.entity_type == "software"]
    assert {e.surface_text for e in firmware} == {"firmware", "drone firmware"}
    assert len({entity_identity(e, m).id for e in firmware}) == 1
    assert len(prediction_entities(out)) == 2
    assert len(relation_pairs(out.entities, m.chunks[0].text)) == 1


def test_versioned_firmware_is_not_merged_with_generic_firmware():
    m = source("Alpha X1 has firmware v2 and firmware.")
    out = normalize(
        m,
        [
            row("d", "Alpha X1", "product", scope="global"),
            row("f1", "firmware", "software", "firmware v2"),
            row("f2", "firmware", "software"),
        ],
    )
    assert len({e.canonical_name for e in out.entities if e.entity_type == "software"}) == 2


def test_firmware_with_multiple_possible_devices_stays_unresolved():
    m = source("Alpha X1 and Beta Y2 need firmware.")
    out = normalize(
        m,
        [
            row("a", "Alpha X1", "product", scope="global"),
            row("b", "Beta Y2", "product", scope="global"),
            row("f", "firmware", "software"),
        ],
    )
    assert out.entities[-1].canonical_name == "firmware"


def test_proven_local_generic_and_global_model_share_identity_but_keep_aliases():
    m = source("Alpha X1 is here. The aircraft is ready.")
    generic = row("g", "aircraft", "product", "Alpha X1")
    generic.identity_quote = "Alpha X1"
    out = normalize(m, [row("d", "Alpha X1", "product", scope="global"), generic])
    assert {e.surface_text for e in out.entities} == {"Alpha X1", "aircraft"}
    assert len({entity_identity(e, m).id for e in out.entities}) == 1


def test_multiple_physical_units_prevent_local_generic_global_merge():
    m = source("Alpha X1 is a model. Two drones are here. The aircraft is ready.")
    generic = row("g", "aircraft", "product", "Alpha X1")
    generic.identity_quote = "Alpha X1"
    out = normalize(m, [row("d", "Alpha X1", "product", scope="global"), generic])
    assert len({entity_identity(e, m).id for e in out.entities}) == 2


def test_chinese_coordinated_owners_get_distinct_components_without_new_relations():
    m = source("Alpha X1、Beta Y2的广角镜头。")
    out = normalize(
        m,
        [
            row("a", "Alpha X1", "product", scope="global"),
            row("b", "Beta Y2", "product", scope="global"),
            row("c", "广角镜头", "component"),
        ],
    )
    cameras = [e for e in out.entities if e.entity_type == "component"]
    assert {e.canonical_name for e in cameras} == {"Alpha X1 广角镜头", "Beta Y2 广角镜头"}
    assert len({entity_identity(e, m).id for e in cameras}) == 2
    assert not out.relations
    validate_extraction(m.chunks[0], out, material=m, require_review=True)


def test_comparison_does_not_assign_the_component_to_every_mentioned_model():
    m = source("Alpha X1 compared with Beta Y2's camera.")
    out = normalize(
        m,
        [
            row("a", "Alpha X1", "product", scope="global"),
            row("b", "Beta Y2", "product", scope="global"),
            row("c", "camera", "component"),
        ],
    )
    cameras = [e for e in out.entities if e.entity_type == "component"]
    assert {e.canonical_name for e in cameras} == {"Beta Y2 camera"}
    assert len({entity_identity(e, m).id for e in cameras}) == 1


def test_explicitly_shared_hardware_is_not_split_into_fabricated_instances():
    m = source("Alpha X1 and Beta Y2 share one camera.")
    out = normalize(
        m,
        [
            row("a", "Alpha X1", "product", scope="global"),
            row("b", "Beta Y2", "product", scope="global"),
            row("c", "camera", "component"),
        ],
    )
    assert len([e for e in out.entities if e.entity_type == "component"]) == 1


def test_owner_split_does_not_copy_unattributed_attributes():
    m = source("Alpha X1 and Beta Y2 each have a camera. One configuration is 5V.")
    out = normalize(
        m,
        [
            row("a", "Alpha X1", "product", scope="global"),
            row("b", "Beta Y2", "product", scope="global"),
            row("c", "camera", "component", attributes={"voltage": "5V"}),
        ],
    )
    cameras = [e for e in out.entities if e.entity_type == "component"]
    assert len(cameras) == 2 and all(not e.attributes for e in cameras)


def test_normalization_is_idempotent_and_identity_scoped_to_video():
    m = source("Alpha X1 has a 1-inch sensor.")
    first = normalize(
        m, [row("d", "Alpha X1", "product", scope="global"), row("s", "1-inch sensor", "component")]
    )
    second = normalize_entity_review(m, m.chunks[0], "", first)
    assert [e.model_dump() for e in first.entities] == [e.model_dump() for e in second.entities]
    other = source(m.chunks[0].text)
    a = next(e for e in first.entities if e.entity_type == "component")
    assert entity_identity(a, m).id != entity_identity(a, other).id


def test_repeated_identical_case_restoration_is_safe_but_mixed_source_forms_are_ambiguous():
    assert source_span("mini 3 and mini 3", "Mini 3", ignore_case=True) == "mini 3"
    assert source_span("US and us", "Us", ignore_case=True) is None


def test_adjacent_chinese_brand_does_not_destroy_the_owned_model_proof():
    m = source("大疆Mavic 3 Pro的中焦镜头。")
    out = normalize(
        m,
        [
            row("d", "大疆Mavic 3 Pro", "product", "DJI Mavic 3 Pro", "global"),
            row("c", "中焦镜头", "component"),
        ],
    )
    child = next(e for e in out.entities if e.entity_type == "component")
    assert child.canonical_name == "DJI Mavic 3 Pro 中焦镜头"
    validate_extraction(m.chunks[0], out, material=m, require_review=True)


def test_variants_with_shared_model_prefixes_do_not_gain_wrong_owners():
    m = source("DJI Air 3 and DJI Air 3S. DJI Air 3S has a camera.")
    out = normalize(
        m,
        [
            row("a", "DJI Air 3", "product", scope="global"),
            row("b", "DJI Air 3S", "product", scope="global"),
            row("c", "camera", "component"),
        ],
    )
    cameras = [e for e in out.entities if e.entity_type == "component"]
    assert len(cameras) == 1 and cameras[0].canonical_name == "DJI Air 3S camera"


def test_normalization_respects_the_existing_schema_limit():
    names = [f"Widget {i}" for i in range(59)]
    m = source("; ".join(names) + "; 1-inch sensor.")
    entities = [row(str(i), name, "product", scope="global") for i, name in enumerate(names)]
    entities.append(row("sensor", "1-inch sensor", "component"))
    out = normalize(m, entities)
    assert len(out.entities) == 60
    assert any(r["key"] == "normalization" for r in out.review.rejected)


def test_owner_and_parameter_changes_reject_tampered_input_receipts():
    m = source("Alpha X1 has a camera.")
    accepted = approved(m, [row("a", "Alpha X1", "product", scope="global")])
    accepted.entities[0].canonical_name = "Beta Y2"
    with pytest.raises(ValueError, match="凭据"):
        normalize_entity_review(m, m.chunks[0], "", accepted)


def test_each_owner_keeps_its_own_continuous_source_evidence():
    m = source("Alpha X1 has a camera.\nBeta Y2 has a camera.")
    out = normalize(
        m,
        [
            row("a", "Alpha X1", "product", scope="global"),
            row("b", "Beta Y2", "product", scope="global"),
            row("c", "camera", "component"),
        ],
    )
    cameras = [e for e in out.entities if e.entity_type == "component"]
    assert len(cameras) == 2
    for camera in cameras:
        owner = "Alpha X1" if camera.canonical_name.startswith("Alpha") else "Beta Y2"
        assert owner in camera.identity_quote
        assert camera.identity_quote in m.chunks[0].text


def test_already_qualified_component_does_not_repeat_its_owner():
    m = source("Alpha X1的1-inch sensor。")
    out = normalize(
        m,
        [
            row("d", "Alpha X1", "product", scope="global"),
            row("c", "Alpha X1的1-inch sensor", "component"),
        ],
    )
    components = [e for e in out.entities if e.entity_type == "component"]
    assert {e.canonical_name for e in components} == {"Alpha X1 sensor"}
    assert {e.surface_text for e in components} == {"Alpha X1的1-inch sensor", "sensor"}
    assert all(e.attributes == {"sensor_size": "1-inch"} for e in components)


def test_possessive_noun_phrase_is_retained_as_an_alias_not_a_separate_identity():
    m = source("Alpha X1的广角镜头。")
    out = normalize(
        m, [row("d", "Alpha X1", "product", scope="global"), row("c", "广角镜头", "component")]
    )
    components = [e for e in out.entities if e.entity_type == "component"]
    assert {e.surface_text for e in components} == {"广角镜头", "Alpha X1的广角镜头"}
    assert len({entity_identity(e, m).id for e in components}) == 1


def test_parameterized_named_technology_keeps_its_literal_video_parameters():
    m = source("支持4K 60帧的HDR视频。")
    out = normalize(m, [row("h", "4K 60帧的HDR视频", "technology", "4K 60帧HDR视频", "global")])
    assert {e.canonical_name for e in out.entities} == {"HDR"}
    assert {e.surface_text for e in out.entities} == {"HDR", "4K 60帧的HDR视频"}
    assert all(
        e.attributes == {"video_resolution": "4K", "frame_rate": "60帧"} for e in out.entities
    )
    assert len(prediction_entities(out)) == 1


def test_multiple_technology_concepts_and_video_configurations_are_not_silently_dropped():
    for surface in ["4K HDR and GPS video", "4K/2K HDR视频"]:
        assert core_name(row("h", surface, "technology")) == (surface, {})


def test_existing_reviewed_acronym_alias_is_not_expanded_into_a_duplicate_identity():
    m = source("Alpha X1 has an FCS (Flight Control System) board. The FCS board was replaced.")
    out = normalize(
        m,
        [
            row("d", "Alpha X1", "product", scope="global"),
            row("c1", "FCS (Flight Control System) board", "component", "Alpha X1 FCS board"),
            row("c2", "FCS board", "component", "Alpha X1 FCS board"),
        ],
    )
    components = [e for e in out.entities if e.entity_type == "component"]
    assert {e.canonical_name for e in components} == {"Alpha X1 FCS board"}
    assert len({entity_identity(e, m).id for e in components}) == 1
    assert {e.surface_text for e in components} >= {
        "FCS (Flight Control System) board",
        "FCS board",
    }


def test_a_unique_explicit_possessive_can_correct_a_wrong_reviewed_owner():
    m = source("Alpha X1 compared with Beta Y2's camera.")
    out = normalize(
        m,
        [
            row("a", "Alpha X1", "product", scope="global"),
            row("b", "Beta Y2", "product", scope="global"),
            row("c", "camera", "component", "Alpha X1 camera"),
        ],
    )
    components = [e for e in out.entities if e.entity_type == "component"]
    assert {e.canonical_name for e in components} == {"Beta Y2 camera"}
