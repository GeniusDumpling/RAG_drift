"""Domain terms propose source mentions without overriding identity or value review."""

from unittest.mock import patch

import pytest
from app.core.config import Settings
from app.schemas.video_graph import ChunkExtraction, EntityType, ExtractedEntity
from app.services.video_graph import LLMVideoExtractor, validate_extraction
from app.services.video_graph_identity import identity_name
from app.services.video_graph_quality import review_extraction
from app.services.video_graph_terms import TERMS, canonical_term, seed_term_mentions, source_terms

from backend.tests.test_video_graph_quality import candidates, decision, material

pytestmark = pytest.mark.no_db


def test_vocabulary_is_small_unambiguous_and_uses_existing_types():
    identities = set()
    for term in TERMS:
        assert set(term) == {"name", "type", "aliases"}
        assert term["type"] in EntityType.__args__
        key = (term["name"], term["type"])
        assert key not in identities
        identities.add(key)
    assert len(TERMS) < 60


def test_source_lookup_keeps_generations_variants_and_family_distinct():
    text = "DJI Neo 3, DJI Mini 3 Pro, Mini 4K, Air 3S, DJI Neo 系列 and RC\nMotion 3."
    rows = source_terms(text)
    assert {r["canonical_name"] for r in rows} == {
        "DJI", "DJI Mini 4K", "DJI Air 3S", "DJI Neo series", "RC Motion 3",
    }
    assert all(r["surface_text"] in text for r in rows)
    assert not source_terms("xHDR or HDRx or STUDIO or Neo 999")


def test_known_brand_in_a_product_name_does_not_seed_an_extra_organization():
    rows = source_terms("DJI Mini 4 Pro")
    assert len(rows) == 1 and rows[0]["entity_type"] == "product"
    assert source_terms("DJI makes things")[0]["entity_type"] == "organization"


def test_alias_normalization_never_invents_a_missing_brand():
    assert canonical_term("Air 3", "product") == "Air 3"
    assert canonical_term("DJI rc motion 3", "product") == "RC Motion 3"
    assert identity_name("固件", "software") == "固件"
    assert identity_name("dji studio", "software") == "DJI Studio"


def test_dictionary_only_seeds_source_terms_and_does_not_replace_unknown_entities():
    candidate = ChunkExtraction(entities=[
        ExtractedEntity(key="unknown", surface_text="WidgetX", entity_type="product"),
        ExtractedEntity(key="hdr", surface_text="HDR", entity_type="technology"),
    ])
    seeded = seed_term_mentions(candidate, "WidgetX has HDR and CMOS. HDR is enabled.")
    assert [e.surface_text for e in seeded.entities] == ["WidgetX", "HDR", "CMOS"]
    assert candidate.entities[0].key == seeded.entities[0].key
    assert all(e.canonical_name is None for e in seeded.entities)


def test_seeded_term_still_requires_independent_value_review():
    m = material("CMOS is mentioned in passing.", title="Camera discussion")
    extractor = LLMVideoExtractor(Settings(_env_file=None, LLM_API_KEY="test"))
    with patch.object(extractor, "_request", side_effect=[
        {"entities": []},
        {"entities": [{"key": "term_0", "accepted": False, "reason": "background mention"}]},
    ]) as request:
        out = extractor.extract(m, m.chunks[0], "")
    assert request.call_count == 2 and not out.entities
    assert request.call_args_list[1].args[1]["entities"][0]["surface_text"] == "CMOS"
    validate_extraction(m.chunks[0], out, material=m, require_review=True)


def test_review_can_narrow_a_literal_boundary_and_preserve_original_source_bytes():
    m = material("The Neo uses the GPS.")
    draft = candidates()
    draft.relations = []
    draft.entities[1].surface_text = "the GPS"
    verdict = decision()
    verdict["entities"][1]["surface_text"] = "GPS"
    out = review_extraction(lambda *_: verdict, m, m.chunks[0], "", draft, "")
    assert out.entities[1].surface_text == "GPS"
    validate_extraction(m.chunks[0], out, material=m, require_review=True)


@pytest.mark.parametrize("replacement", ["Neo", "GPS Pro", "250g"])
def test_review_boundary_cannot_move_to_another_mention_or_invent_words(replacement):
    m = material("The Neo uses GPS. Neo weighs 250g.")
    draft = candidates()
    draft.relations = []
    verdict = decision()
    verdict["entities"][1]["surface_text"] = replacement
    out = review_extraction(lambda *_: verdict, m, m.chunks[0], "", draft, "")
    assert out.entities[1].surface_text == "GPS"
    assert any(r["kind"] == "entity_boundary" for r in out.review.rejected)


@pytest.mark.parametrize("surface,expected", [
    ("firmware", "software"), ("固件", "software"), ("韌體", "software"),
    ("遥控器", "product"), ("the remote controller", "product"),
    ("RC 2 遥控器", "product"), ("DJI RC Motion 3", "product"),
    ("Goggles 3", "product"), ("飞行眼镜", "product"),
])
def test_user_confirmed_types_are_applied_to_complete_objects(surface, expected):
    from app.services.video_graph_terms import confirmed_type

    assert confirmed_type(surface, "component") == expected


@pytest.mark.parametrize("surface", ["remote controller board", "遥控器按钮", "flight controller",
                                     "RC Motion 3 button", "Goggles 3 screen", "firmware chip"])
def test_user_confirmed_types_do_not_reclassify_internal_parts(surface):
    from app.services.video_graph_terms import confirmed_type

    assert confirmed_type(surface, "component") == "component"
