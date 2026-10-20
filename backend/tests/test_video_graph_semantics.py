"""Offline geographic entity and relation contracts; no model or database calls."""

import unittest
import uuid
from typing import get_args
from unittest.mock import patch

import pytest
from app.core.config import Settings
from app.schemas.video_graph import ChunkExtraction, EntityType, Predicate
from app.services.video_graph import (
    DeepSeekVideoExtractor,
    VideoChunk,
    VideoMaterial,
    validate_extraction,
)
from app.services.video_graph_semantics import (
    ENTITY_TYPE_DEFINITIONS,
    RELATION_TYPE_RULES,
    EntitySemanticError,
    RelationSemanticError,
)

pytestmark = pytest.mark.no_db


def extraction(entities, *, predicate=None, quote=None):
    return ChunkExtraction.model_validate(
        {
            "entities": [
                {"key": str(index), "surface_text": surface, "entity_type": kind}
                for index, (surface, kind) in enumerate(entities)
            ],
            "relations": []
            if predicate is None
            else [
                {
                    "subject": "0",
                    "object": "1",
                    "predicate": predicate,
                    "quote": quote,
                    "assertion": "asserted",
                    "confidence": 0.8,
                    "qualifiers": {},
                }
            ],
        }
    )


def chunk(body, kind="transcript_segment"):
    return VideoChunk(uuid.uuid4(), body, kind, 0, 20)


class TestVideoGraphSemantics(unittest.TestCase):
    def test_definitions_cover_schema_types_and_relations(self):
        self.assertEqual(set(ENTITY_TYPE_DEFINITIONS), set(get_args(EntityType)))
        self.assertEqual(set(RELATION_TYPE_RULES), set(get_args(Predicate)))

    def test_country_and_region_mentions_are_accepted_without_mutation(self):
        body = "Ukraine 乌克兰 Україна Украина Europe 基辅 东欧"
        entities = [(name, "country") for name in ("Ukraine", "乌克兰", "Україна", "Украина")]
        entities += [(name, "region") for name in ("Europe", "基辅", "东欧")]
        result = extraction(entities)
        before = result.model_dump()
        self.assertIs(validate_extraction(chunk(body), result), result)
        self.assertEqual(result.model_dump(), before)

    def test_geographic_names_cannot_be_forced_into_technical_or_organization_types(self):
        for kind in set(get_args(EntityType)) - {"country"}:
            with self.subTest(kind=kind), self.assertRaisesRegex(EntitySemanticError, "country"):
                validate_extraction(chunk("Ukraine"), extraction([("Ukraine", kind)]))
        with self.assertRaisesRegex(EntitySemanticError, "region"):
            validate_extraction(chunk("东欧"), extraction([("东欧", "organization")]))

    def test_geographic_names_are_normalized_only_for_type_checks(self):
        with self.assertRaises(EntitySemanticError):
            validate_extraction(
                chunk("ＵＫＲＡＩＮＥ"), extraction([("ＵＫＲＡＩＮＥ", "organization")])
            )
        self.assertEqual(
            validate_extraction(chunk("UKRAINE"), extraction([("UKRAINE", "country")]))
            .entities[0]
            .surface_text,
            "UKRAINE",
        )

    def test_country_substrings_do_not_reclassify_full_organizations(self):
        for name in ("乌克兰政府", "Ukraine International Airlines", "中国移动", "欧盟"):
            with self.subTest(name=name):
                result = validate_extraction(chunk(name), extraction([(name, "organization")]))
                self.assertEqual(result.entities[0].entity_type, "organization")

    def test_ambiguous_and_unknown_names_are_not_forced_to_geographic_types(self):
        for name, kind in (
            ("Jordan", "organization"),
            ("Java", "software"),
            ("Georgia", "region"),
            ("未收录的城市", "region"),
        ):
            with self.subTest(name=name):
                validate_extraction(chunk(name), extraction([(name, kind)]))

    def test_geography_still_requires_an_exact_source_mention(self):
        with self.assertRaisesRegex(ValueError, "实体提及"):
            validate_extraction(chunk("Ukraine"), extraction([("乌克兰", "country")]))

    def test_existing_technical_relation_roles_remain_supported(self):
        examples = [
            ([("DJI", "organization"), ("GPS", "technology")], "uses"),
            ([("Mavic 3", "product"), ("相机", "component")], "contains"),
            ([("PX4", "software"), ("GPS", "technology")], "compatible_with"),
            ([("校准", "method"), ("IMU", "component")], "applied_to"),
            ([("Mavic 3", "product"), ("失联", "fault")], "reports_fault"),
            ([("滤波", "method"), ("定位漂移", "fault")], "mitigates"),
        ]
        for entities, predicate in examples:
            body = " ".join(surface for surface, kind in entities)
            with self.subTest(predicate=predicate):
                result = validate_extraction(
                    chunk(body), extraction(entities, predicate=predicate, quote=body)
                )
                self.assertEqual(len(result.relations), 1)

    def test_usage_and_geographic_hierarchy_relations_are_supported(self):
        examples = [
            (
                "Mavic 3 is used in Ukraine.",
                [("Mavic 3", "product"), ("Ukraine", "country")],
                "used_in",
            ),
            ("GPS is used in 东欧.", [("GPS", "technology"), ("东欧", "region")], "used_in"),
            (
                "Kyiv is located in Ukraine.",
                [("Kyiv", "region"), ("Ukraine", "country")],
                "located_in",
            ),
            (
                "Ukraine is located in Europe.",
                [("Ukraine", "country"), ("Europe", "region")],
                "located_in",
            ),
        ]
        for body, entities, predicate in examples:
            with self.subTest(predicate=predicate, body=body):
                result = validate_extraction(
                    chunk(body), extraction(entities, predicate=predicate, quote=body)
                )
                self.assertEqual(len(result.relations), 1)

    def test_geographic_relation_directions_and_technical_roles_are_checked(self):
        examples = [
            ([("Ukraine", "country"), ("GPS", "technology")], "uses"),
            ([("Ukraine", "country"), ("GPS", "technology")], "compatible_with"),
            ([("Ukraine", "country"), ("GPS", "technology")], "used_in"),
            ([("GPS", "technology"), ("Ukraine", "country")], "reports_fault"),
            ([("GPS", "technology"), ("Ukraine", "country")], "located_in"),
            ([("Ukraine", "country"), ("France", "country")], "located_in"),
        ]
        for entities, predicate in examples:
            body = " ".join(surface for surface, kind in entities)
            with self.subTest(predicate=predicate, entities=entities):
                with self.assertRaises(RelationSemanticError):
                    validate_extraction(
                        chunk(body), extraction(entities, predicate=predicate, quote=body)
                    )

    def test_geographic_relation_quote_must_name_its_geographic_object(self):
        quote = "Mavic 3 is used overseas."
        body = quote + " Ukraine is mentioned elsewhere."
        with self.assertRaisesRegex(RelationSemanticError, "地理关系证据"):
            validate_extraction(
                chunk(body),
                extraction(
                    [("Mavic 3", "product"), ("Ukraine", "country")],
                    predicate="used_in",
                    quote=quote,
                ),
            )

    def test_summary_mentions_are_checked_but_never_create_geographic_relations(self):
        body = "Mavic 3 is used in Ukraine."
        result = validate_extraction(
            chunk(body, "video_summary"),
            extraction(
                [("Mavic 3", "product"), ("Ukraine", "country")],
                predicate="used_in",
                quote=body,
            ),
        )
        self.assertEqual(len(result.entities), 2)
        self.assertEqual(result.relations, [])
        with self.assertRaises(EntitySemanticError):
            validate_extraction(chunk(body, "video_summary"), extraction([("Ukraine", "product")]))

    def test_extraction_prompt_explains_types_roles_and_abstention(self):
        material = VideoMaterial(
            uuid.uuid4(), "youtube:test", "test", "https://youtu.be/test", "", "manual", "en", ()
        )
        extractor = DeepSeekVideoExtractor(Settings(_env_file=None, DEEPSEEK_API_KEY="test-key"))
        with patch.object(
            extractor, "_request", return_value={"entities": [], "relations": []}
        ) as request:
            extractor.extract(material, chunk("Ukraine"), "")
        system = request.call_args.args[0]
        for rule in ("country", "region", "used_in", "located_in", "无法判断", "乌克兰政府"):
            self.assertIn(rule, system)
