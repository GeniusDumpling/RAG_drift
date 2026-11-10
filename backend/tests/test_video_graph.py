"""Storage-independent contracts and historical schema migration verification."""

import importlib.util
import os
import unittest
import uuid
from dataclasses import replace
from pathlib import Path

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from app.core.config import Settings
from app.services.video_graph.evidence import timestamp_url
from app.services.video_graph.extraction import LLMVideoExtractor
from app.services.video_graph.identity import identity_name
from app.services.video_graph.material import VideoChunk, VideoMaterial, locate_quote
from app.services.video_graph.validation import validate_extraction
from sqlalchemy import text

from backend.tests.video_graph_fixtures import SourceDatabase, extraction_for

pytestmark = pytest.mark.no_db


class TestVideoGraphContracts(unittest.TestCase):
    def test_whitespace_matching_returns_original_offsets(self):
        body = "prefix PX4\n uses   GPS. suffix"
        start, end = locate_quote(body, "PX4 uses GPS.")
        self.assertEqual(body[start:end], "PX4\n uses   GPS.")
        self.assertIsNone(locate_quote(body, "PX4 causes interference."))

    def test_hallucinated_relation_and_neighbor_only_entity_are_rejected(self):
        chunk = VideoChunk(uuid.uuid4(), "PX4 uses GPS.", "transcript_segment", 0, 20)
        extraction = extraction_for(chunk.text)
        extraction.relations[0].quote = "PX4 supports RTK."
        with self.assertRaisesRegex(ValueError, "关系证据"):
            validate_extraction(chunk, extraction)
        extraction = extraction_for(chunk.text)
        extraction.entities[0].surface_text = "DJI"
        with self.assertRaisesRegex(ValueError, "实体提及"):
            validate_extraction(chunk, extraction)

    def test_summary_never_generates_relation(self):
        chunk = VideoChunk(uuid.uuid4(), "PX4 uses GPS.", "video_summary", None, None)
        result = validate_extraction(chunk, extraction_for(chunk.text))
        self.assertEqual(len(result.entities), 2)
        self.assertEqual(result.relations, [])

    def test_fingerprint_tracks_subtitle_and_timing_even_with_same_summary(self):
        chunk = VideoChunk(uuid.uuid4(), "PX4 uses GPS.", "transcript_segment", 1, 20)
        material = VideoMaterial(
            uuid.uuid4(),
            "youtube:sample",
            "title",
            "https://youtu.be/sample",
            "unchanged summary",
            "manual",
            "en",
            (chunk,),
        )
        self.assertNotEqual(
            material.fingerprint,
            replace(material, chunks=(replace(chunk, text="PX4 uses RTK."),)).fingerprint,
        )
        self.assertNotEqual(
            material.fingerprint,
            replace(material, chunks=(replace(chunk, start_seconds=10),)).fingerprint,
        )

    def test_aliases_are_type_specific(self):
        self.assertEqual(identity_name("大疆", "organization"), "DJI")
        self.assertEqual(identity_name("大疆 Mavic 3", "product"), "DJI Mavic 3")
        self.assertEqual(identity_name(" PX4 ", "software"), "PX4")

    def test_timestamp_url_preserves_video_identifier_and_replaces_old_time(self):
        self.assertEqual(
            timestamp_url("https://www.youtube.com/watch?v=abc&t=9", 62.5),
            "https://www.youtube.com/watch?v=abc&t=62",
        )
        self.assertEqual(
            timestamp_url("https://example.test/video", 62), "https://example.test/video"
        )

    def test_missing_model_key_is_an_error_not_a_fake_graph(self):
        with self.assertRaisesRegex(ValueError, "LLM_API_KEY"):
            LLMVideoExtractor(Settings(_env_file=None, LLM_API_KEY=None))


@unittest.skipUnless(os.getenv("VIDEO_GRAPH_TEST_DATABASE_URL"), "isolated PostgreSQL URL required")
class TestHistoricalSchema(SourceDatabase):
    def test_partial_status_migration_preserves_diagnostics_on_downgrade(self):
        content_id = self.seed_video()
        from app.models.video_graph import VideoGraphBuild
        from sqlalchemy import inspect, select
        from sqlalchemy.orm import Session

        with Session(self.engine) as session, session.begin():
            session.add(
                VideoGraphBuild(
                    content_item_id=content_id,
                    fingerprint="legacy",
                    extractor_version="video-graph-v1:fixture",
                    status="success",
                )
            )
        with Session(self.engine) as session, session.begin():
            build = session.scalar(
                select(VideoGraphBuild).where(VideoGraphBuild.content_item_id == content_id)
            )
            build.status = "partial_success"
            build.output_json = {"chunk_errors": {"one": {"code": "entity_surface_not_found"}}}
        path = (
            Path(__file__).resolve().parents[1]
            / "alembic/versions/0006_video_graph_partial_success.py"
        )
        spec = importlib.util.spec_from_file_location("partial_migration", path)
        migration = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(migration)
        with self.engine.begin() as connection:
            with Operations.context(MigrationContext.configure(connection)):
                migration.downgrade()
            constraint = next(
                c
                for c in inspect(connection).get_check_constraints("video_graph_builds")
                if c["name"].endswith("status_valid")
            )
            self.assertNotIn("partial_success", constraint["sqltext"])
            self.assertEqual(
                connection.scalar(text("SELECT status FROM video_graph_builds")), "failed"
            )
            self.assertIsNotNone(
                connection.scalar(
                    text("SELECT output_json->'chunk_errors' FROM video_graph_builds")
                )
            )
            with Operations.context(MigrationContext.configure(connection)):
                migration.upgrade()
            connection.execute(text("UPDATE video_graph_builds SET status='partial_success'"))
