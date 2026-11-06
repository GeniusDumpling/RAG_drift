"""Identity, source-review and cache invalidation regressions from the database audit."""

import uuid
from dataclasses import replace
from unittest.mock import patch

import httpx
import pytest
from app.core.config import Settings
from app.schemas.video_graph import ChunkExtraction, ExtractedEntity
from app.services.video_graph import (
    LLMVideoExtractor,
    VideoChunk,
    VideoMaterial,
    validate_extraction,
)
from app.services.video_graph_identity import (
    entity_identity,
    extraction_version,
    identity_name,
    input_hash,
    neighbor_context,
    policy_id,
)
from app.services.video_graph_quality import review_extraction

pytestmark = pytest.mark.no_db


def material(body="The Neo uses GPS.", title="DJI Neo tutorial"):
    chunk = VideoChunk(uuid.uuid4(), body, "transcript_segment", 0, 10)
    return VideoMaterial(
        uuid.uuid4(), "youtube:test", title, "https://youtu.be/test", "", "manual", "en", (chunk,)
    )


def entity(
    surface, kind, canonical=None, scope="global", *, key="s", basis=None, source="current_chunk"
):
    return ExtractedEntity(
        key=key,
        surface_text=surface,
        entity_type=kind,
        canonical_name=canonical or surface,
        identity_scope=scope,
        identity_quote=basis or surface,
        identity_source=source,
    )


def candidates():
    return ChunkExtraction.model_validate(
        {
            "entities": [
                {"key": "s", "surface_text": "The Neo", "entity_type": "product"},
                {"key": "o", "surface_text": "GPS", "entity_type": "technology"},
            ],
            "relations": [
                {
                    "subject": "s",
                    "object": "o",
                    "predicate": "uses",
                    "quote": "The Neo uses GPS.",
                    "assertion": "asserted",
                    "confidence": 0.9,
                }
            ],
        }
    )


def pipeline_responses(*, supported=True):
    responses = [
        candidates().model_dump(),
        {"entities": decision()["entities"]},
        {
            "relations": [
                {
                    "index": 0,
                    "supported": True,
                    "predicate": "uses",
                    "quote": "The Neo uses GPS.",
                    "assertion": "asserted",
                    "reason": "explicit source",
                }
            ]
        },
        {"relations": decision(supported=supported)["relations"]},
    ]
    return responses


def decision(*, supported=True, quote="The Neo uses GPS."):
    return {
        "entities": [
            {
                "key": "s",
                "accepted": True,
                "canonical_name": "DJI Neo",
                "identity_scope": "global",
                "identity_quote": "DJI Neo",
                "identity_source": "title",
                "reason": "明确型号",
            },
            {
                "key": "o",
                "accepted": True,
                "canonical_name": "GPS",
                "identity_scope": "global",
                "identity_quote": "GPS",
                "identity_source": "current_chunk",
                "reason": "明确技术",
            },
        ],
        "relations": [
            {
                "index": 0,
                "supported": supported,
                "quote": quote,
                "reason": "来源审核",
                "basis": "use",
                "modality": "actual",
            }
        ],
    }


def reviewed(m=None):
    m = m or material()
    return review_extraction(lambda *_: decision(), m, m.chunks[0], "", candidates(), "")


def test_unresolved_generic_devices_do_not_merge_across_videos():
    first, second = material(), material()
    e = entity("drone", "product", scope="video")
    assert entity_identity(e, first).id != entity_identity(e, second).id
    assert entity_identity(e, first).id == entity_identity(e, first).id
    with pytest.raises(ValueError, match="泛称"):
        entity_identity(e.model_copy(update={"identity_scope": "global"}), first)


def test_owner_prefix_does_not_strip_the_component_or_accessory_noun():
    assert identity_name("DJI Avata 2 motion controller", "product") == (
        "DJI Avata 2 motion controller"
    )
    assert identity_name("DJI O4 Lite air unit", "product") == "DJI O4 Lite"
    with pytest.raises(ValueError, match="短型号"):
        entity_identity(entity("O1", "product"), material())
    assert identity_name("threeaxis gimbal", "component") == "3-axis gimbal"
    assert identity_name("three-axis gimbal", "component") == "3-axis gimbal"
    assert identity_name("two axis gimbal", "component") != "3-axis gimbal"
    assert identity_name("2-in OLED sensor", "component") == "2-inch OLED sensor"


def test_named_variants_merge_but_product_generations_stay_distinct():
    m = material()
    ids = {
        entity_identity(entity(surface, "product", canonical), m).id
        for surface, canonical in [
            ("Neo", "DJI Neo"),
            ("The Neo", "DJI Neo"),
            ("DJI Neo", "DJI Neo"),
        ]
    }
    assert len(ids) == 1
    second = {
        entity_identity(entity(surface, "product", canonical), m).id
        for surface, canonical in [
            ("Neo2", "DJI Neo 2"),
            ("Neo 2", "DJI Neo 2"),
            ("DJI Neo2", "DJI Neo2"),
        ]
    }
    assert len(second) == 1 and ids.isdisjoint(second)
    assert identity_name("Mavic 3Pro", "product", "DJI") == identity_name(
        "DJI Mavic 3 Pro", "product"
    )


def test_resolved_pronoun_has_contextual_alias_and_global_canonical_identity():
    m = material("It uses GPS.")
    e = entity("It", "product", "DJI Neo", basis="DJI Neo", source="title")
    identity = entity_identity(e, m)
    assert identity.scope_key == "v2:global"
    assert identity.alias_scope == f"v2:video:{m.id}"
    assert identity.id == entity_identity(entity("DJI Neo", "product"), m).id


def test_unrelated_brand_mention_does_not_complete_a_short_model_name():
    m = material("Acme Neo is being compared with DJI.", "Mixed-brand comparison")
    unresolved = entity("Neo", "product", "Neo", scope="global")
    with pytest.raises(ValueError, match="制造商"):
        entity_identity(unresolved, m)
    resolved = entity("Acme Neo", "product", "Acme Neo")
    assert entity_identity(resolved, m).name == "Acme Neo"
    assert (
        entity_identity(unresolved.model_copy(update={"identity_scope": "video"}), m).name == "Neo"
    )


def test_future_version_bumps_keep_quality_pipeline_and_unknown_tags_are_rejected():
    from app.services.video_graph_identity import is_quality_version

    assert is_quality_version(extraction_version("video-graph-v3", "fixture"))
    assert is_quality_version(extraction_version("video-graph-v2-domain-fix", "fixture"))
    with pytest.raises(ValueError, match="抽取版本"):
        extraction_version("arbitrary-tag", "fixture")


@pytest.mark.parametrize(
    "name", ["192.168.1.12", "31分钟", "600 MHz", "$400", "IHX文件", "firmware.bin"]
)
def test_attribute_values_cannot_become_component_entities(name):
    with pytest.raises(ValueError, match="属性值"):
        entity_identity(entity(name, "component"), material())


def test_controlled_software_and_country_aliases_normalize():
    assert identity_name("DJI fly app", "software") == identity_name("DJI Fly", "software")
    assert identity_name("美國", "country") == identity_name("United States", "country")
    assert identity_name("大疆", "organization") == identity_name("DJI", "organization")


def test_independent_reviewer_rejects_high_confidence_wrong_relation():
    m = material()
    out = review_extraction(
        lambda *_: decision(supported=False), m, m.chunks[0], "", candidates(), ""
    )
    assert len(out.entities) == 2
    assert not out.relations
    assert out.review.candidate_relations == 1
    assert out.review.rejected[0]["kind"] == "relation"
    assert validate_extraction(m.chunks[0], out, material=m, require_review=True) is out


def test_acceptance_receipt_binds_source_identity_context_and_result():
    m = material()
    out = reviewed(m)
    assert len(out.relations) == 1
    assert out.review.policy == policy_id()
    changed = out.model_copy(deep=True)
    changed.relations[0].predicate = "contains"
    with pytest.raises(ValueError, match="审核凭据"):
        validate_extraction(m.chunks[0], changed, material=m, require_review=True)
    with pytest.raises(ValueError, match="审核凭据"):
        validate_extraction(
            m.chunks[0], out, material=replace(m, title="Different video"), require_review=True
        )
    with pytest.raises(ValueError, match="审核凭据"):
        validate_extraction(m.chunks[0], candidates(), material=m, require_review=True)


def test_neighbor_change_invalidates_unchanged_current_chunk():
    m = material()
    adjacent = VideoChunk(uuid.uuid4(), "DJI Neo 2 comparison", "transcript_segment", 10, 20)
    changed = replace(m, chunks=(*m.chunks, adjacent))
    assert input_hash(m, m.chunks[0]) != input_hash(changed, m.chunks[0])
    assert neighbor_context(changed, m.chunks[0]) == adjacent.text


def test_no_usable_entity_review_fails_instead_of_replacing_the_graph():
    m = material()
    with pytest.raises(ValueError, match="未完整覆盖"):
        review_extraction(
            lambda *_: dict(entities=[], relations=[]), m, m.chunks[0], "", candidates(), ""
        )


def test_missing_relation_review_is_quarantined_without_losing_entities():
    m = material()
    out = review_extraction(
        lambda *_: {**decision(), "relations": []}, m, m.chunks[0], "", candidates(), ""
    )
    assert len(out.entities) == 2 and not out.relations
    assert any(r["kind"] == "review_format" for r in out.review.rejected)
    validate_extraction(m.chunks[0], out, material=m, require_review=True)


def test_fabricated_identity_anchor_cannot_publish_a_global_identity():
    m = material()
    response = decision()
    response["entities"][0]["identity_quote"] = "DJI Neo 99"
    response["entities"][0]["canonical_name"] = "DJI Neo 99"
    out = review_extraction(lambda *_: response, m, m.chunks[0], "", candidates(), "")
    assert out.entities[0].identity_scope == "video"
    assert out.entities[0].canonical_name == "Neo"
    assert len(out.relations) == 1  # The literal source fact remains valid.
    assert out.review.rejected[0]["kind"] == "identity"
    assert out.entities[0].identity_quote in m.chunks[0].text


def test_composite_identity_can_use_reordered_source_words_and_explicit_brand():
    m = material("Creator combo of Pocket 3 contains battery handle.", "DJI Pocket 3 tutorial")
    draft = candidates()
    draft.entities[0].surface_text = "Creator combo"
    draft.entities[1].surface_text = "battery handle"
    draft.entities[1].entity_type = "component"
    draft.relations[0].predicate = "contains"
    draft.relations[0].quote = m.chunks[0].text
    verdict = decision(quote=m.chunks[0].text)
    verdict["entities"][0].update(
        canonical_name="DJI Pocket 3 Creator Combo",
        identity_quote="Creator combo of Pocket 3",
        identity_source="current_chunk",
    )
    verdict["entities"][1].update(
        canonical_name="battery handle", identity_quote="battery handle", identity_scope="video"
    )
    verdict["relations"][0]["basis"] = "composition"
    out = review_extraction(lambda *_: verdict, m, m.chunks[0], "", draft, "")
    assert out.entities[0].canonical_name == "DJI Pocket 3 Creator Combo"
    assert out.entities[0].identity_scope == "global"
    assert len(out.relations) == 1 and not out.review.rejected


def test_year_digits_cannot_supply_a_missing_product_generation():
    m = material(title="DJI Neo 3 in 2026")
    verdict = decision()
    verdict["entities"][0].update(canonical_name="DJI Neo 2", identity_quote="DJI Neo 3")
    out = review_extraction(lambda *_: verdict, m, m.chunks[0], "", candidates(), "")
    assert out.entities[0].identity_scope == "video"
    assert out.entities[0].canonical_name == "Neo"


def test_invalid_optional_attribute_does_not_erase_valid_entity_or_relation():
    m = material()
    draft = candidates()
    draft.entities[0].attributes = {"weight": "unsupported value"}
    verdict = decision()
    verdict["entities"][0]["accepted_attributes"] = ["weight"]
    out = review_extraction(lambda *_: verdict, m, m.chunks[0], "", draft, "")
    assert len(out.entities) == 2 and len(out.relations) == 1
    assert out.entities[0].attributes == {}
    assert out.review.rejected[0]["kind"] == "attribute"


def test_bad_confidence_format_gets_bounded_schema_feedback_before_review():
    m = material()
    extractor = LLMVideoExtractor(Settings(_env_file=None, LLM_API_KEY="test"))
    invalid = candidates().model_dump()
    invalid["entities"][0]["confidence"] = "high"
    with patch.object(extractor, "_request", side_effect=[invalid, *pipeline_responses()]) as req:
        out = extractor.extract(m, m.chunks[0], "")
    assert len(out.relations) == 1 and req.call_count == 5
    assert "format_correction" in req.call_args_list[1].args[1]


def reviewed_pair(body, subject, subject_type, obj, object_type, candidate_predicate, **repair):
    from app.services.video_graph_grounding import BASIS_BY_PREDICATE

    predicate = repair.get("predicate", candidate_predicate)
    basis = {"uses": "use", "contains": "composition"}.get(
        predicate, sorted(BASIS_BY_PREDICATE[predicate])[0]
    )
    m = material(body)
    draft = ChunkExtraction.model_validate(
        {
            "entities": [
                {"key": "s", "surface_text": subject, "entity_type": subject_type},
                {"key": "o", "surface_text": obj, "entity_type": object_type},
            ],
            "relations": [
                {
                    "subject": "s",
                    "object": "o",
                    "predicate": candidate_predicate,
                    "quote": body,
                    "assertion": "asserted",
                    "confidence": 0.9,
                }
            ],
        }
    )
    verdict = {
        "entities": [
            dict(
                key=key,
                accepted=True,
                canonical_name=name,
                identity_scope="video",
                identity_source="current_chunk",
                identity_quote=name,
                reason="explicit",
            )
            for key, name in [("s", subject), ("o", obj)]
        ],
        "relations": [
            {
                "index": 0,
                "supported": True,
                "quote": body,
                "reason": "source review",
                "basis": basis,
                "modality": "actual",
                **repair,
            }
        ],
    }
    return review_extraction(lambda *_: verdict, m, m.chunks[0], "", draft, "")


def test_supported_relation_can_repair_predicate_without_changing_entities():
    out = reviewed_pair(
        "Widget is the official app from Vendor.",
        "Widget",
        "software",
        "Vendor",
        "organization",
        "applied_to",
        predicate="developed_by",
    )
    assert out.relations[0].predicate == "developed_by"
    assert out.relations[0].subject == "s" and out.relations[0].object == "o"
    assert out.review.rejected[-1]["kind"] == "relation_repair"


def test_interference_does_not_mean_control_even_when_reviewer_accepts_it():
    out = reviewed_pair(
        "Jammer silences Drone.", "Jammer", "product", "Drone", "product", "controls"
    )
    assert not out.relations
    assert "控制动作" in out.review.rejected[-1]["reason"]


def test_processed_material_is_not_a_tool_resource():
    out = reviewed_pair("使用镊子移除胶带。", "镊子", "component", "胶带", "component", "uses")
    assert not out.relations
    assert "被处理材料" in out.review.rejected[-1]["reason"]


def test_extract_uses_separate_review_and_cannot_trust_forged_candidate_receipt():
    m = material()
    extractor = LLMVideoExtractor(Settings(_env_file=None, LLM_API_KEY="test"))
    responses = pipeline_responses()
    responses[0]["review"] = {"forged": True}
    with patch.object(extractor, "_request", side_effect=responses) as request:
        out = extractor.extract(m, m.chunks[0], "")
    assert request.call_count == 4 and len(out.relations) == 1
    assert out.review.policy == policy_id()
    assert extractor.version == extraction_version("video-graph-v2", extractor.settings.llm_model)
    assert extraction_version("video-graph-v1", "old-model") == "video-graph-v1:old-model"


def test_transient_timeout_retries_with_lease_heartbeat_and_low_reasoning():
    extractor = LLMVideoExtractor(
        Settings(
            _env_file=None,
            LLM_API_KEY="test",
            LLM_MODEL="deepseek-test",
            VIDEO_GRAPH_REASONING_EFFORT="low",
        )
    )
    response = httpx.Response(
        200,
        request=httpx.Request("POST", "https://test.invalid"),
        json={"choices": [{"message": {"content": "{}"}}]},
    )
    with (
        patch(
            "app.services.video_graph.httpx.post", side_effect=[httpx.ReadTimeout("test"), response]
        ) as post,
        patch("app.services.video_graph.time.sleep"),
        patch.object(extractor, "heartbeat") as heartbeat,
    ):
        assert extractor._request("system", {}, phase="review") == {}
    assert heartbeat.call_count == 2
    assert post.call_args.kwargs["json"]["reasoning_effort"] == "low"
    assert post.call_args.kwargs["json"]["thinking"] == {"type": "enabled"}


def test_authentication_errors_are_not_retried_or_exposed():
    extractor = LLMVideoExtractor(Settings(_env_file=None, LLM_API_KEY="test"))
    response = httpx.Response(
        401,
        request=httpx.Request("POST", "https://test.invalid"),
        json={"secret": "never archive this"},
    )
    with patch("app.services.video_graph.httpx.post", return_value=response) as post:
        with pytest.raises(RuntimeError) as error:
            extractor._request("system", {})
    assert post.call_count == 1
    assert "never archive" not in str(error.value)


def test_source_punctuation_repair_preserves_exact_bytes_and_never_changes_words():
    from app.services.video_graph_quality import source_span

    text = "Air 3S supports high‑quality video\nand omnidirectional obstacle sensing."
    assert source_span(text, "high-quality video and omnidirectional obstacle sensing") == (
        "high‑quality video\nand omnidirectional obstacle sensing"
    )
    assert source_span(text, "Air 3 supports video") is None
    assert source_span(text, "DJI Air 3S") is None
    assert source_span("GPS and gps", "Gps", ignore_case=True) is None
    chinese = "在 Matrice 400 的上方\n搭载了一整套\n非常夸张的避障系统"
    assert source_span(chinese, "在 Matrice 400 的上方搭载了一整套非常夸张的避障系统") == chinese
    assert source_span("GPS module", "GPSmodule") is None
    assert source_span(chinese, "在 Matrice 400 搭载了毫米波雷达") is None


def test_script_matching_restores_original_text_without_translation_or_ambiguity():
    from app.services.video_graph_quality import source_span

    assert source_span("新相機模組支援錄影。", "相机模组") == "相機模組"
    assert source_span("新相機模組支援錄影。", "camera module") is None
    assert source_span("新相機模組支援錄影。", "摄像头") is None
    assert source_span("模組與模組", "模组") is None
    assert source_span("DJI Mini 4 Pro", "DJI Mini 3 Pro") is None


def test_reviewed_punctuation_is_restored_before_publication():
    m = material("The Neo uses GPS‑aided navigation.")
    draft = candidates()
    draft.entities[1].surface_text = "GPS-aided navigation"
    verdict = decision(quote="The Neo uses GPS-aided navigation.")
    verdict["entities"][1].update(
        canonical_name="GPS-aided navigation", identity_quote="GPS-aided navigation"
    )
    out = review_extraction(lambda *_: verdict, m, m.chunks[0], "", draft, "")
    assert out.entities[1].surface_text == "GPS‑aided navigation"
    assert out.relations[0].quote == m.chunks[0].text
    validate_extraction(m.chunks[0], out, material=m, require_review=True)


def test_noncontiguous_model_mention_gets_one_bounded_source_correction():
    m = material()
    extractor = LLMVideoExtractor(Settings(_env_file=None, LLM_API_KEY="test"))
    broken = candidates().model_dump()
    broken["entities"][0]["surface_text"] = "DJI The Neo"
    with patch.object(extractor, "_request", side_effect=[broken, *pipeline_responses()]) as req:
        out = extractor.extract(m, m.chunks[0], "")
    assert len(out.relations) == 1 and req.call_count == 5
    assert req.call_args_list[1].args[1]["format_correction"]["items"][0]["key"] == "s"


def test_review_metadata_does_not_override_the_supported_field():
    m = material()
    verdict = decision(supported=False)
    verdict["relations"][0]["supported_override"] = True
    verdict["relations"][0]["reason_override"] = "irrelevant metadata"
    out = review_extraction(lambda *_: verdict, m, m.chunks[0], "", candidates(), "")
    assert not out.relations


def test_review_schema_feedback_does_not_retry_a_semantic_rejection():
    m = material()
    invalid = decision()
    invalid["relations"][0]["modality"] = "invalid-class"
    from unittest.mock import Mock

    request = Mock(side_effect=[invalid, decision(supported=False)])
    out = review_extraction(request, m, m.chunks[0], "", candidates(), "")
    assert request.call_count == 2 and not out.relations
    assert request.call_args.args[1]["format_correction"]["fields"]


def test_formatting_retry_cannot_reverse_an_existing_valid_semantic_rejection():
    from copy import deepcopy
    from unittest.mock import Mock

    m = material()
    draft = candidates()
    draft.relations.append(draft.relations[0].model_copy(deep=True))
    draft.relations[0].predicate = "contains"
    first = decision(supported=False)
    first["relations"][0]["basis"] = "composition"
    first["relations"].append(
        {"index": 1, "supported": True, "modality": "invalid-kind", "reason": "format error"}
    )
    second = deepcopy(first)
    second["relations"][0]["supported"] = True
    second["relations"][1]["modality"] = "actual"
    request = Mock(side_effect=[first, second])
    out = review_extraction(request, m, m.chunks[0], "", draft, "")
    assert len(out.relations) == 1 and out.relations[0].predicate == "uses"
    assert out.review.relation_support[0]["source_index"] == "1"


def test_pair_classification_has_one_bounded_correction_attempt():
    m = material()
    extractor = LLMVideoExtractor(Settings(_env_file=None, LLM_API_KEY="test"))
    responses = pipeline_responses()
    invalid = {
        "relations": [
            {"index": 0, "supported": True, "predicate": "invented", "reason": "invalid schema"}
        ]
    }
    with patch.object(
        extractor, "_request", side_effect=[*responses[:2], invalid, *responses[2:]]
    ) as req:
        out = extractor.extract(m, m.chunks[0], "")
    assert len(out.relations) == 1 and req.call_count == 5
    assert "format_correction" in req.call_args_list[3].args[1]


def test_control_can_change_settings_without_literal_control_word():
    out = reviewed_pair(
        "Cursor can change settings in the goggles.",
        "Cursor",
        "software",
        "goggles",
        "component",
        "controls",
    )
    assert len(out.relations) == 1


def test_bare_frequency_value_is_an_attribute_not_a_technical_entity():
    from app.services.video_graph_identity import is_attribute_value

    assert is_attribute_value("2.4")
    assert is_attribute_value("5.8 GHz")
    assert not is_attribute_value("O4")
    assert not is_attribute_value("5G")


def test_pair_candidate_still_requires_independent_source_review():
    m = material()
    extractor = LLMVideoExtractor(Settings(_env_file=None, LLM_API_KEY="test"))
    with patch.object(
        extractor, "_request", side_effect=pipeline_responses(supported=False)
    ) as req:
        out = extractor.extract(m, m.chunks[0], "")
    assert req.call_count == 4 and not out.relations
    assert [call.kwargs.get("phase") for call in req.call_args_list] == [
        None,
        "review",
        "relation",
        "review",
    ]
    assert req.call_args_list[2].args[1]["pairs"] == [{"index": 0, "subject": "s", "object": "o"}]
    assert out.review.candidate_relations == 1


def test_extraction_tasks_have_bounded_prompts_and_independent_review_input():
    m = material()
    extractor = LLMVideoExtractor(Settings(_env_file=None, LLM_API_KEY="test"))
    with patch.object(extractor, "_request", side_effect=pipeline_responses()) as req:
        out = extractor.extract(m, m.chunks[0], "")
    assert len(out.relations) == 1 and req.call_count == 4
    initial, entities, classification, review = req.call_args_list
    assert all(len(call.args[0]) < 2800 for call in req.call_args_list)
    assert sum(len(call.args[0]) for call in req.call_args_list) < 8000
    assert "product=" in initial.args[0]
    assert "relations" not in entities.args[1] or not entities.args[1]["relations"]
    assert classification.args[1]["current_chunk"] == m.chunks[0].text
    assert "explicit source" not in str(review.args[1])
    assert "pairs" not in review.args[1]


def test_entity_review_cannot_publish_invented_mentions_even_if_model_accepts_them():
    m = material()
    typed = candidates().model_dump()
    typed["entities"][0]["surface_text"] = "Invented drone"
    extractor = LLMVideoExtractor(Settings(_env_file=None, LLM_API_KEY="test"))
    responses = [typed, typed, decision()]
    with patch.object(extractor, "_request", side_effect=responses) as req:
        out = extractor.extract(m, m.chunks[0], "")
    assert len(out.entities) == 1 and out.entities[0].surface_text == "GPS"
    assert not out.relations and req.call_count == 3


def test_invalid_pair_answer_does_not_erase_valid_pair_answers_or_entities():
    from unittest.mock import Mock

    from app.services.video_graph_quality import request_verdicts

    good = pipeline_responses()[2]["relations"][0]
    invalid = {"relations": [good, {"index": 1, "supported": True, "predicate": "invented"}]}
    request = Mock(side_effect=[invalid, invalid])
    verdict, errors = request_verdicts(request, "", {}, set(), 2, pairs=True)
    assert request.call_count == 2
    assert verdict.relations[0].supported and not verdict.relations[1].supported
    assert len(errors) == 1 and errors[0]["key"] == "relation:1"


def test_invalid_relation_modality_is_quarantined_after_bounded_repair():
    m = material()
    verdict = decision()
    verdict["relations"][0]["modality"] = "uncertain"
    out = review_extraction(lambda *_: verdict, m, m.chunks[0], "", candidates(), "")
    assert len(out.entities) == 2 and not out.relations
    assert any(r["kind"] == "review_format" for r in out.review.rejected)
    validate_extraction(m.chunks[0], out, material=m, require_review=True)


def test_expanded_attribute_objects_only_select_existing_source_validated_keys():
    m = material()
    draft = candidates()
    draft.entities[1].attributes = {"name": "GPS"}
    verdict = decision()
    verdict["entities"][1]["accepted_attributes"] = [
        {"key": "name", "value": "FORGED", "quote": "FORGED"},
        {"key": "unknown", "value": "GPS"},
    ]
    out = review_extraction(lambda *_: verdict, m, m.chunks[0], "", draft, "")
    assert out.entities[1].attributes == {"name": "GPS"}
    assert any(r["kind"] == "attribute" for r in out.review.rejected)


def test_summary_is_entity_only_and_skips_pair_classification():
    m = material()
    m = replace(m, chunks=(replace(m.chunks[0], evidence_type="video_summary"),))
    extractor = LLMVideoExtractor(Settings(_env_file=None, LLM_API_KEY="test"))
    with patch.object(extractor, "_request", side_effect=pipeline_responses()[:2]) as req:
        out = extractor.extract(m, m.chunks[0], "")
    assert req.call_count == 2 and len(out.entities) == 2 and not out.relations
    validate_extraction(m.chunks[0], out, material=m, require_review=True)


@pytest.mark.parametrize("supported", [True, False])
def test_relation_rejection_does_not_erase_a_source_supported_entity(supported):
    m = material()
    extractor = LLMVideoExtractor(Settings(_env_file=None, LLM_API_KEY="test"))
    with patch.object(extractor, "_request", side_effect=pipeline_responses(supported=supported)):
        out = extractor.extract(m, m.chunks[0], "")
    assert len(out.entities) == 2 and bool(out.relations) == supported
    validate_extraction(m.chunks[0], out, material=m, require_review=True)


def test_pair_answers_cover_negatives_multiple_predicates_and_reject_mixed_decisions():
    from app.services.video_graph_quality import valid_pair_groups

    positive = pipeline_responses()[2]["relations"][0]
    second = {**positive, "predicate": "contains"}
    negative = {"index": 1, "supported": False, "reason": "no relation"}
    groups = valid_pair_groups([positive, second, negative], 2)
    assert len(groups[0]) == 2 and len(groups[1]) == 1
    assert 0 not in valid_pair_groups([positive, {**negative, "index": 0}], 2)
    assert not valid_pair_groups([{**positive, "assertion": None}], 1)


def test_unhashable_review_index_is_isolated_without_losing_entity_review():
    m = material()
    verdict = decision()
    verdict["relations"][0]["index"] = {"bad": 0}
    out = review_extraction(lambda *_: verdict, m, m.chunks[0], "", candidates(), "")
    assert len(out.entities) == 2 and not out.relations
    assert any(r["kind"] == "review_format" for r in out.review.rejected)


def test_duplicate_entity_keys_get_bounded_correction_before_entity_review():
    m = material()
    invalid = candidates().model_dump()
    invalid["entities"][1]["key"] = "s"
    extractor = LLMVideoExtractor(Settings(_env_file=None, LLM_API_KEY="test"))
    with patch.object(extractor, "_request", side_effect=[invalid, *pipeline_responses()]) as req:
        out = extractor.extract(m, m.chunks[0], "")
    assert len(out.relations) == 1 and req.call_count == 5
    assert "format_correction" in req.call_args_list[1].args[1]


def test_review_only_needs_one_relation_label_and_program_dispatches_source_checks():
    from app.services.video_graph_quality import RelationVerdict

    assert "basis" not in RelationVerdict.model_fields
    out = reviewed_pair(
        "Cursor can change settings in goggles.",
        "Cursor",
        "software",
        "goggles",
        "product",
        "controls",
        basis="none",
    )
    assert len(out.relations) == 1
    assert out.review.relation_support[0]["basis"] == "control"
    blocked = reviewed_pair(
        "Jammer silences Drone.", "Jammer", "product", "Drone", "product", "controls", basis="none"
    )
    assert not blocked.relations


@pytest.mark.parametrize("supported", [True, False])
def test_pair_negative_is_pinned_across_unrelated_format_correction(supported):
    from unittest.mock import Mock

    from app.services.video_graph_quality import request_verdicts

    first = {"index": 0, "supported": False, "reason": "no relation"}
    malformed = {"index": 1, "supported": True, "predicate": "invented"}
    positive = pipeline_responses()[2]["relations"][0]
    request = Mock(
        side_effect=[
            {"relations": [first, malformed]},
            {
                "relations": [
                    positive,
                    {
                        "index": 1,
                        "supported": supported,
                        **(
                            {k: v for k, v in positive.items() if k not in {"index", "supported"}}
                            if supported
                            else {"reason": "absent"}
                        ),
                    },
                ]
            },
        ]
    )
    verdict, _ = request_verdicts(request, "", {}, set(), 2, pairs=True)
    by_index = {item.index: item for item in verdict.relations}
    assert not by_index[0].supported
    assert by_index[1].supported == supported


def test_numeric_values_remain_source_validated_entity_attributes():
    m = material("The Neo weighs 250g and uses GPS.")
    draft = candidates()
    draft.relations = []
    draft.entities[0].attributes = {"weight": "250g"}
    verdict = decision()
    verdict["entities"][0]["accepted_attributes"] = ["weight"]
    out = review_extraction(lambda *_: verdict, m, m.chunks[0], "", draft, "")
    assert len(out.entities) == 2 and out.entities[0].attributes == {"weight": "250g"}
    assert all(e.surface_text != "250g" for e in out.entities)


def test_measurement_entities_are_rejected_without_rejecting_models_or_protocols():
    from app.services.video_graph_identity import is_attribute_value

    for value in ["250g", "48MP", "4K", "4K 60fps", "3840×2160", "2026年"]:
        assert is_attribute_value(value)
        with pytest.raises(ValueError, match="属性值"):
            entity_identity(entity(value, "technology"), material())
    for value in ["5G", "DJI Mini 4K", "DJI Neo 2", "CMOS", "HDR", "USB-C"]:
        assert not is_attribute_value(value)


def test_internal_dispatch_keeps_source_verified_military_inventory_projection():
    out = reviewed_pair(
        "阅兵中，反无人机方队包括高能激光。",
        "反无人机方队",
        "organization",
        "高能激光",
        "technology",
        "contains",
    )
    assert len(out.relations) == 1 and out.relations[0].predicate == "equipped_with"
    assert out.review.relation_support[0]["basis"] == "equipment"


def test_brand_ownership_cannot_be_promoted_to_manufacture():
    bad = reviewed_pair(
        "Rover is the latest addition to Vendor's family.",
        "Rover",
        "product",
        "Vendor",
        "organization",
        "manufactured_by",
    )
    assert not bad.relations
    good = reviewed_pair(
        "Rover is manufactured by Vendor.",
        "Rover",
        "product",
        "Vendor",
        "organization",
        "manufactured_by",
    )
    assert len(good.relations) == 1


def test_pair_questions_are_bounded_and_only_use_reviewed_source_entities():
    from app.services.video_graph_quality import relation_pairs

    entities = [entity(f"Device {i}", "product", key=str(i)) for i in range(22)]
    text = " ".join(e.surface_text for e in entities)
    pairs = relation_pairs(entities, text)
    assert len(pairs) == len(set(pairs)) == 96
    assert all(first != second for first, second in pairs)
    assert any("0" in pair for pair in pairs)
    assert not relation_pairs(entities, "Unrelated source text")


def test_relation_batch_review_reindexes_proofs_and_deduplicates_across_batches():
    from unittest.mock import Mock

    from app.services.video_graph_quality import review_relation_batches

    m = material()
    draft = candidates()
    edges, draft.relations = draft.relations, []
    verified = review_extraction(lambda *_: decision(), m, m.chunks[0], "", draft, "")
    request = Mock(return_value={"relations": decision()["relations"]})
    out = review_relation_batches(
        request, m, m.chunks[0], "", verified, edges + edges, batch_size=1
    )
    assert request.call_count == 2 and out.review.candidate_relations == 2
    assert len(out.relations) == len(out.review.relation_support) == 1
    assert out.review.relation_support[0]["index"] == "0"
    validate_extraction(m.chunks[0], out, material=m, require_review=True)


def test_pair_classification_rejects_tampered_entity_receipt_before_model_call():
    from unittest.mock import Mock

    from app.services.video_graph_quality import classify_relations

    m = material()
    verified = reviewed(m)
    verified.entities[0].canonical_name = "Invented model"
    request = Mock()
    with pytest.raises(ValueError, match="凭据"):
        classify_relations(request, m, m.chunks[0], "", verified)
    request.assert_not_called()


def test_explicit_negative_pair_has_no_relation_review_request():
    m = material()
    extractor = LLMVideoExtractor(Settings(_env_file=None, LLM_API_KEY="test"))
    responses = pipeline_responses()[:2] + [
        {"relations": [{"index": 0, "supported": False, "reason": "No supported relationship"}]},
    ]
    with patch.object(extractor, "_request", side_effect=responses) as req:
        out = extractor.extract(m, m.chunks[0], "")
    assert req.call_count == 3 and len(out.entities) == 2 and not out.relations
    assert not any(call.args[0].startswith("\n独立对照完整current_chunk审核每条关系")
                   for call in req.call_args_list)
    validate_extraction(m.chunks[0], out, material=m, require_review=True)
