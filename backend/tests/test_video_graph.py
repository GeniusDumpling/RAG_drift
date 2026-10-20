"""Contract tests and optional PostgreSQL integration tests in disposable schemas.

Set VIDEO_GRAPH_TEST_DATABASE_URL to run integration tests. No existing tables are reset.
This module also runs with Python's unittest when pytest is unavailable in the app image.
"""

import importlib.util
import os
import unittest
import uuid
from dataclasses import replace
from datetime import timedelta
from pathlib import Path

import app.models  # noqa: F401
from alembic.migration import MigrationContext
from alembic.operations import Operations
from app.core.config import Settings
from app.db.base import Base, utcnow
from app.models.content import ContentChunk, ContentItem, RawPage
from app.models.control import CrawlJob, CrawlRun, SourceSite
from app.models.video_graph import VideoGraphBuild
from app.schemas.video_graph import ChunkExtraction, GraphRetrieveRequest, VideoAssessment
from app.services.video_graph import (
    DeepSeekVideoExtractor,
    LeaseLostError,
    VideoChunk,
    VideoMaterial,
    activate_build,
    canonical_name,
    claim_build,
    enqueue_videos,
    locate_quote,
    process_next,
    validate_extraction,
    withdraw_video,
)
from app.services.video_graph_queries import (
    get_graph,
    list_entities,
    overview,
    relation_evidence,
    retrieve_context,
    timestamp_url,
    video_status,
)
from sqlalchemy import create_engine, inspect, select, text
from sqlalchemy.orm import Session

try:
    import pytest

    pytestmark = pytest.mark.no_db
except ImportError:
    pass


def extraction_for(body: str) -> ChunkExtraction:
    target = "RTK" if "RTK" in body else "GPS"
    return ChunkExtraction.model_validate(
        {
            "entities": [
                {"key": "p", "surface_text": "PX4", "entity_type": "software"},
                {"key": "g", "surface_text": target, "entity_type": "technology"},
            ],
            "relations": [
                {
                    "subject": "p",
                    "object": "g",
                    "predicate": "uses",
                    "quote": body,
                    "assertion": "asserted",
                    "confidence": 0.8,
                    "qualifiers": {},
                }
            ],
        }
    )


class StubExtractor:
    version = "test-v1:model"

    def __init__(self, fail_at: int | None = None, relevant: bool = True):
        self.calls = 0
        self.assessments = 0
        self.fail_at = fail_at
        self.relevant = relevant
        self.on_extract = None

    def assess(self, material):
        self.assessments += 1
        return VideoAssessment(relevant=self.relevant, reason="测试主题判断")

    def extract(self, material, chunk, context):
        self.calls += 1
        if self.on_extract:
            self.on_extract()
        if self.fail_at == self.calls:
            raise RuntimeError("simulated provider failure")
        return extraction_for(chunk.text)


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
        self.assertEqual(canonical_name("大疆", "organization"), "DJI")
        self.assertEqual(canonical_name("大疆", "product"), "大疆")
        self.assertEqual(canonical_name(" PX4 ", "software"), "PX4")

    def test_timestamp_url_preserves_video_identifier_and_replaces_old_time(self):
        self.assertEqual(
            timestamp_url("https://www.youtube.com/watch?v=abc&t=9", 62.5),
            "https://www.youtube.com/watch?v=abc&t=62",
        )
        self.assertEqual(
            timestamp_url("https://example.test/video", 62), "https://example.test/video"
        )

    def test_missing_model_key_is_an_error_not_a_fake_graph(self):
        with self.assertRaisesRegex(ValueError, "DEEPSEEK_API_KEY"):
            DeepSeekVideoExtractor(Settings(_env_file=None, DEEPSEEK_API_KEY=None))


@unittest.skipUnless(
    os.getenv("VIDEO_GRAPH_TEST_DATABASE_URL"), "isolated PostgreSQL URL not configured"
)
class TestVideoGraphPersistence(unittest.TestCase):
    def setUp(self):
        self.schema = f"video_graph_test_{uuid.uuid4().hex}"
        self.admin_engine = create_engine(os.environ["VIDEO_GRAPH_TEST_DATABASE_URL"])
        with self.admin_engine.begin() as connection:
            connection.execute(text(f'CREATE SCHEMA "{self.schema}"'))
        self.engine = create_engine(
            os.environ["VIDEO_GRAPH_TEST_DATABASE_URL"],
            connect_args={"options": f"-csearch_path={self.schema}"},
        )
        Base.metadata.create_all(self.engine)

    def tearDown(self):
        self.engine.dispose()
        with self.admin_engine.begin() as connection:
            connection.execute(text(f'DROP SCHEMA "{self.schema}" CASCADE'))
        self.admin_engine.dispose()

    def seed_video(self, *, chunks=1, kind="transcript_segment", item_type="video_description"):
        with Session(self.engine) as session, session.begin():
            source = SourceSite(
                name="test",
                site_type="video_aggregator",
                base_url="https://youtu.be",
                fetch_mode="http_api",
            )
            session.add(source)
            session.flush()
            job = CrawlJob(
                source_site_id=source.id,
                name="test",
                trigger_mode="manual",
                parser_profile="video_transcript_evidence",
            )
            session.add(job)
            session.flush()
            run = CrawlRun(
                source_site_id=source.id,
                crawl_job_id=job.id,
                status="success",
                trigger_type="manual",
                execution_mode="test",
            )
            session.add(run)
            session.flush()
            raw = RawPage(
                source_site_id=source.id,
                crawl_run_id=run.id,
                requested_url="https://youtu.be/test",
                fetched_at=utcnow(),
            )
            session.add(raw)
            session.flush()
            item = ContentItem(
                source_site_id=source.id,
                crawl_run_id=run.id,
                raw_page_id=raw.id,
                item_type=item_type,
                title="无人机 PX4",
                canonical_url=f"https://youtu.be/{uuid.uuid4().hex}",
                cleaned_text="PX4 uses GPS.",
                content_hash="constant-summary-hash",
                dedup_key=uuid.uuid4().hex,
                metadata_json={"caption_source": "manual", "caption_language": "en"},
            )
            session.add(item)
            session.flush()
            for index in range(chunks):
                session.add(
                    ContentChunk(
                        content_item_id=item.id,
                        chunk_index=index,
                        display_text="PX4 uses GPS.",
                        embed_text="PX4 uses GPS.",
                        embed_status="pending",
                        chunk_metadata_json={
                            "evidence_type": kind,
                            "start_seconds": 10 + index * 20,
                            "end_seconds": 30 + index * 20,
                        },
                    )
                )
            return item.id

    def queue(self, *, retry=False, retry_partial=False, version=StubExtractor.version):
        with Session(self.engine) as session, session.begin():
            return enqueue_videos(
                session, extractor_version=version, retry_failed=retry, retry_partial=retry_partial
            )

    def change_transcript(self, content_id):
        with Session(self.engine) as session, session.begin():
            chunk = session.scalar(
                select(ContentChunk).where(ContentChunk.content_item_id == content_id)
            )
            chunk.display_text = "PX4 uses RTK."

    def test_only_video_is_queued_and_repeated_run_is_idempotent(self):
        self.seed_video(item_type="post")
        content_id = self.seed_video()
        self.assertEqual(self.queue()["scanned"], 1)
        extractor = StubExtractor()
        self.assertEqual(process_next(self.engine, extractor)["status"], "success")
        self.queue()
        self.assertIsNone(process_next(self.engine, extractor))
        with Session(self.engine) as session:
            result = overview(session)
            self.assertEqual(result["entities"], 2)
            self.assertEqual(result["relations"], 1)
            graph = get_graph(session, content_id=content_id)
            self.assertEqual(len(graph.nodes), 3)
            self.assertEqual(len(graph.edges), 3)
            relation = next(edge for edge in graph.edges if edge.kind == "relation")
            evidence = relation_evidence(session, uuid.UUID(relation.id))
            self.assertEqual(evidence["items"][0].start_seconds, 10)
            self.assertTrue(evidence["items"][0].video_url.endswith("?t=10"))

    def test_update_replaces_one_sources_evidence_and_keeps_other_source(self):
        first, second = self.seed_video(), self.seed_video()
        self.queue()
        process_next(self.engine, StubExtractor())
        process_next(self.engine, StubExtractor())
        self.change_transcript(first)
        self.queue()
        self.assertEqual(process_next(self.engine, StubExtractor())["status"], "success")
        with Session(self.engine) as session:
            self.assertEqual(overview(session)["relations"], 2)
            names = {item["name"] for item in list_entities(session)["items"]}
            self.assertEqual(names, {"PX4", "GPS", "RTK"})
        with Session(self.engine) as session, session.begin():
            withdraw_video(session, second)
        with Session(self.engine) as session:
            self.assertEqual(overview(session)["relations"], 1)
            self.assertEqual(
                {item["name"] for item in list_entities(session)["items"]}, {"PX4", "RTK"}
            )

    def test_failed_update_preserves_active_graph(self):
        content_id = self.seed_video()
        self.queue()
        process_next(self.engine, StubExtractor())
        self.change_transcript(content_id)
        self.queue()
        self.assertEqual(process_next(self.engine, StubExtractor(fail_at=1))["status"], "failed")
        with Session(self.engine) as session:
            self.assertTrue(video_status(session, content_id)["source_changed"])
            self.assertEqual(overview(session)["relations"], 1)
            relation = next(edge for edge in get_graph(session).edges if edge.kind == "relation")
            self.assertEqual(
                relation_evidence(session, uuid.UUID(relation.id))["items"][0].quote,
                "PX4 uses GPS.",
            )

    def test_retry_resumes_successful_chunk_checkpoints(self):
        self.seed_video(chunks=2)
        self.queue()
        extractor = StubExtractor(fail_at=2)
        self.assertEqual(process_next(self.engine, extractor)["status"], "failed")
        self.queue(retry=True)
        extractor.fail_at = None
        self.assertEqual(process_next(self.engine, extractor)["status"], "success")
        self.assertEqual(extractor.calls, 3)
        self.assertEqual(extractor.assessments, 1)
        with Session(self.engine) as session:
            self.assertEqual(overview(session)["evidence"], 2)

    def test_failure_details_persist_with_checkpoints_and_clear_after_success(self):
        content_id = self.seed_video(chunks=2)
        self.queue()
        extractor = StubExtractor()
        original_extract = extractor.extract

        def invalid_second_chunk(material, chunk, context):
            result = original_extract(material, chunk, context)
            if extractor.calls == 2:
                result.entities[0].surface_text = "DJI PX4"
            return result

        extractor.extract = invalid_second_chunk
        result = process_next(self.engine, extractor)
        self.assertEqual(result["status"], "partial_success")
        detail = next(iter(result["chunk_errors"].values()))
        self.assertEqual(detail["code"], "entity_surface_not_found")
        self.assertEqual(detail["chunk_index"], 2)
        self.assertEqual(detail["surface_text"], "DJI PX4")
        with Session(self.engine) as session:
            build = session.scalar(
                select(VideoGraphBuild).where(VideoGraphBuild.content_item_id == content_id)
            )
            self.assertEqual(build.output_json["chunk_errors"][detail["chunk_id"]], detail)
            self.assertEqual(len(build.output_json["chunks"]), 1)
            self.assertIn("部分完成", build.error_message)
        self.queue(retry=True)
        extractor.extract = original_extract
        self.assertEqual(process_next(self.engine, extractor)["status"], "success")
        with Session(self.engine) as session:
            build = session.scalar(
                select(VideoGraphBuild).where(VideoGraphBuild.content_item_id == content_id)
            )
            self.assertNotIn("last_error", build.output_json)
            self.assertIsNone(build.error_message)

    def test_skips_invalid_middle_chunk_and_only_retries_that_chunk(self):
        content_id = self.seed_video(chunks=3)
        self.queue()
        extractor = StubExtractor()
        original = extractor.extract

        def invalid_middle(material, chunk, context):
            result = original(material, chunk, context)
            if extractor.calls == 2:
                result.entities[0].surface_text = "not in this transcript"
            return result

        extractor.extract = invalid_middle
        result = process_next(self.engine, extractor)
        self.assertEqual(result["status"], "partial_success")
        self.assertEqual(extractor.calls, 3)
        self.assertEqual(result["coverage"]["passed_chunks"], 2)
        self.assertEqual(result["coverage"]["skipped_chunks"], 1)
        self.assertAlmostEqual(result["coverage"]["coverage_ratio"], 2 / 3)
        with Session(self.engine) as session:
            self.assertEqual(overview(session)["evidence"], 2)
            self.assertEqual(overview(session)["partial_videos"], 1)
            self.assertEqual(video_status(session, content_id)["status"], "partial_success")
        self.queue()
        self.assertIsNone(process_next(self.engine, extractor))
        self.queue(retry_partial=True)
        extractor.extract = original
        result = process_next(self.engine, extractor)
        self.assertEqual(result["status"], "success")
        self.assertEqual(extractor.calls, 4)
        self.assertEqual(result["chunk_errors"], {})
        self.assertTrue(result["coverage"]["complete"])
        with Session(self.engine) as session:
            self.assertEqual(overview(session)["evidence"], 3)

    def test_all_chunks_invalid_is_failed_while_valid_empty_extractions_are_success(self):
        self.seed_video(chunks=2)
        self.queue()
        extractor = StubExtractor()
        extractor.extract = lambda *args: ChunkExtraction.model_validate({
            "entities": [{"key": "bad", "surface_text": "missing", "entity_type": "product"}],
        })
        result = process_next(self.engine, extractor)
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["error_detail"]["code"], "all_chunks_invalid")
        self.assertEqual(result["coverage"]["skipped_chunks"], 2)
        with Session(self.engine) as session:
            self.assertEqual(overview(session)["active_videos"], 0)
        self.queue(retry=True)
        extractor.extract = lambda *args: ChunkExtraction(entities=[], relations=[])
        result = process_next(self.engine, extractor)
        self.assertEqual(result["status"], "success")
        self.assertEqual(result["coverage"]["passed_chunks"], 2)
        self.assertEqual(result["chunk_errors"], {})

    def test_schema_failure_is_skipped_but_network_failure_aborts(self):
        self.seed_video(chunks=3)
        self.queue()
        extractor = StubExtractor()
        original = extractor.extract

        def schema_first(material, chunk, context):
            result = original(material, chunk, context)
            if extractor.calls == 1:
                return ChunkExtraction.model_validate({"type": "json_object"})
            return result

        extractor.extract = schema_first
        result = process_next(self.engine, extractor)
        self.assertEqual(result["status"], "partial_success")
        self.assertEqual(extractor.calls, 3)
        error = next(iter(result["chunk_errors"].values()))
        self.assertEqual(error["code"], "schema_validation_failed")
        with Session(self.engine) as session:
            evidence_before = overview(session)["evidence"]
        self.queue(retry_partial=True)
        extractor.extract = lambda *args: (_ for _ in ()).throw(RuntimeError("network failure"))
        result = process_next(self.engine, extractor)
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["error_detail"]["code"], "model_request_error")
        with Session(self.engine) as session:
            self.assertEqual(overview(session)["evidence"], evidence_before)

    def test_partial_new_version_keeps_previous_projection_and_reuses_unchanged_chunks(self):
        content_id = self.seed_video(chunks=3)
        self.queue()
        old_result = process_next(self.engine, StubExtractor())
        with Session(self.engine) as session, session.begin():
            chunks = session.scalars(select(ContentChunk).where(
                ContentChunk.content_item_id == content_id
            ).order_by(ContentChunk.chunk_index)).all()
            chunks[1].display_text = "PX4 uses RTK."
        self.queue()
        extractor = StubExtractor()
        original = extractor.extract

        def invalid_changed(material, chunk, context):
            result = original(material, chunk, context)
            result.entities[0].surface_text = "missing"
            return result

        extractor.extract = invalid_changed
        result = process_next(self.engine, extractor)
        self.assertEqual(result["status"], "partial_success")
        self.assertEqual(extractor.calls, 1)
        self.assertEqual(result["coverage"]["passed_chunks"], 2)
        self.assertFalse(result["projection_activated"])
        with Session(self.engine) as session:
            status = video_status(session, content_id)
            self.assertEqual(status["active_build_id"], old_result["build_id"])
            self.assertTrue(status["source_changed"])
            self.assertEqual(status["active_coverage"]["passed_chunks"], 3)
            self.assertEqual(overview(session)["evidence"], 3)
        self.queue(retry_partial=True)
        extractor.extract = original
        result = process_next(self.engine, extractor)
        self.assertEqual(result["status"], "success")
        self.assertEqual(extractor.calls, 2)
        self.assertTrue(result["projection_activated"])
        with Session(self.engine) as session:
            self.assertNotEqual(
                video_status(session, content_id)["active_build_id"], old_result["build_id"]
            )
            self.assertEqual(overview(session)["relations"], 2)

    def test_changed_extractor_version_does_not_reuse_old_chunk_results(self):
        self.seed_video(chunks=2)
        self.queue()
        process_next(self.engine, StubExtractor())
        self.queue(version="test-v2:model")
        extractor = StubExtractor()
        extractor.version = "test-v2:model"
        self.assertEqual(process_next(self.engine, extractor)["status"], "success")
        self.assertEqual(extractor.calls, 2)

    def test_retry_cannot_remove_previously_published_chunk_on_same_build(self):
        content_id = self.seed_video(chunks=3)
        self.queue()
        result = process_next(self.engine, StubExtractor())
        with Session(self.engine) as session, session.begin():
            build = session.get(VideoGraphBuild, uuid.UUID(result["build_id"]))
            output = dict(build.output_json)
            chunks = dict(output["chunks"])
            key = next(iter(chunks))
            del chunks[key]
            output["chunks"] = chunks
            build.output_json = output
            build.status = "partial_success"
        self.queue(retry_partial=True)
        extractor = StubExtractor()
        extractor.extract = lambda *args: ChunkExtraction.model_validate({
            "entities": [{"key": "bad", "surface_text": "missing", "entity_type": "product"}],
        })
        retried = process_next(self.engine, extractor)
        self.assertEqual(retried["status"], "partial_success")
        self.assertFalse(retried["projection_activated"])
        with Session(self.engine) as session:
            self.assertEqual(overview(session)["evidence"], 3)
            self.assertEqual(
                video_status(session, content_id)["active_coverage"]["passed_chunks"], 3
            )

    def test_partial_projection_can_be_withdrawn_and_restored(self):
        content_id = self.seed_video(chunks=2)
        self.queue()
        extractor = StubExtractor()
        original = extractor.extract

        def invalid_last(material, chunk, context):
            result = original(material, chunk, context)
            if extractor.calls % 2 == 0:
                result.entities[0].surface_text = "missing"
            return result

        extractor.extract = invalid_last
        self.assertEqual(process_next(self.engine, extractor)["status"], "partial_success")
        with Session(self.engine) as session, session.begin():
            withdraw_video(session, content_id)
        with Session(self.engine) as session:
            self.assertEqual(overview(session)["evidence"], 0)
        with Session(self.engine) as session, session.begin():
            withdraw_video(session, content_id, restore=True)
        self.queue()
        extractor.extract = original
        self.assertEqual(process_next(self.engine, extractor)["status"], "success")
        self.assertEqual(extractor.calls, 3)

    def test_partial_status_migration_preserves_diagnostics_on_downgrade(self):
        content_id = self.seed_video()
        self.queue()
        with Session(self.engine) as session, session.begin():
            build = session.scalar(select(VideoGraphBuild).where(
                VideoGraphBuild.content_item_id == content_id
            ))
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
                c for c in inspect(connection).get_check_constraints("video_graph_builds")
                if c["name"].endswith("status_valid")
            )
            self.assertNotIn("partial_success", constraint["sqltext"])
            self.assertEqual(
                connection.scalar(text("SELECT status FROM video_graph_builds")), "failed"
            )
            self.assertIsNotNone(connection.scalar(text(
                "SELECT output_json->'chunk_errors' FROM video_graph_builds"
            )))
            with Operations.context(MigrationContext.configure(connection)):
                migration.upgrade()
            connection.execute(text("UPDATE video_graph_builds SET status='partial_success'"))

    def test_source_changed_during_extraction_cannot_activate(self):
        content_id = self.seed_video()
        self.queue()
        extractor = StubExtractor()
        extractor.on_extract = lambda: self.change_transcript(content_id)
        self.assertEqual(process_next(self.engine, extractor)["status"], "obsolete")
        with Session(self.engine) as session:
            self.assertEqual(overview(session)["relations"], 0)

    def test_expired_worker_cannot_commit_after_lease_takeover(self):
        self.seed_video()
        self.queue()
        with Session(self.engine) as session, session.begin():
            build_id, old_token = claim_build(session)
            session.get(VideoGraphBuild, build_id).lease_until = utcnow() - timedelta(seconds=1)
        with Session(self.engine) as session, session.begin():
            new_build_id, new_token = claim_build(session)
            self.assertEqual(build_id, new_build_id)
            self.assertNotEqual(old_token, new_token)
        with Session(self.engine) as session, session.begin():
            with self.assertRaises(LeaseLostError):
                activate_build(session, build_id, old_token, {})

    def test_withdraw_and_restore_reuses_cached_build(self):
        content_id = self.seed_video()
        self.queue()
        extractor = StubExtractor()
        process_next(self.engine, extractor)
        with Session(self.engine) as session, session.begin():
            withdraw_video(session, content_id)
        self.assertEqual(self.queue()["withdrawn"], 1)
        with Session(self.engine) as session:
            self.assertEqual(overview(session)["relations"], 0)
        with Session(self.engine) as session, session.begin():
            withdraw_video(session, content_id, restore=True)
        self.queue()
        self.assertEqual(process_next(self.engine, extractor)["status"], "success")
        self.assertEqual(extractor.calls, 1)

    def test_summary_only_and_irrelevant_videos_have_no_technical_relations(self):
        content_id = self.seed_video(kind="video_summary")
        self.queue()
        self.assertEqual(process_next(self.engine, StubExtractor())["status"], "success")
        with Session(self.engine) as session:
            self.assertTrue(video_status(session, content_id)["summary_only"])
            self.assertEqual(overview(session)["entities"], 2)
            self.assertEqual(overview(session)["relations"], 0)
        self.queue(version="test-v2:model")
        extractor = StubExtractor(relevant=False)
        extractor.version = "test-v2:model"
        process_next(self.engine, extractor)
        with Session(self.engine) as session:
            self.assertEqual(overview(session)["entities"], 0)

    def test_graph_bounds_closed_edges_and_retrieval_budget(self):
        self.seed_video()
        self.queue()
        process_next(self.engine, StubExtractor())
        with Session(self.engine) as session:
            graph = get_graph(session, max_nodes=2, max_edges=1)
            self.assertLessEqual(len(graph.nodes), 2)
            self.assertLessEqual(len(graph.edges), 1)
            node_ids = {node.id for node in graph.nodes}
            self.assertTrue(
                all(edge.source in node_ids and edge.target in node_ids for edge in graph.edges)
            )
            context = retrieve_context(
                session, GraphRetrieveRequest(query="PX4 使用什么技术", max_chars=500)
            )
            self.assertEqual(len(context.evidence), 1)
            self.assertLessEqual(len(context.context), 500)
            self.assertIn("PX4 uses GPS.", context.context)

    def seed_connected_graph(self, *, noise=0, kind="transcript_segment"):
        content_id = self.seed_video(kind=kind)
        names = [f"AAA PX4 {index:02}" for index in range(noise)]
        with Session(self.engine) as session, session.begin():
            chunk = session.scalar(
                select(ContentChunk).where(ContentChunk.content_item_id == content_id)
            )
            chunk.display_text = "PX4 uses GPS. GPS uses RTK. " + " ".join(names)
        self.queue()
        extractor = StubExtractor()

        def extract(material, chunk, context):
            result = extraction_for("PX4 uses GPS.").model_dump()
            result["entities"].append(
                {"key": "r", "surface_text": "RTK", "entity_type": "technology"}
            )
            result["entities"].extend(
                {"key": f"n{index}", "surface_text": name, "entity_type": "product"}
                for index, name in enumerate(names)
            )
            result["relations"].append(
                {
                    "subject": "g", "object": "r", "predicate": "uses",
                    "quote": "GPS uses RTK.", "assertion": "uncertain",
                    "confidence": 0.8, "qualifiers": {},
                }
            )
            return ChunkExtraction.model_validate(result)

        extractor.extract = extract
        self.assertEqual(process_next(self.engine, extractor)["status"], "success")
        return content_id

    def test_relation_pairs_take_priority_over_alphabetical_isolated_entities(self):
        content_id = self.seed_connected_graph(noise=20)
        with Session(self.engine) as session:
            for kwargs in ({}, {"q": "PX4"}, {"content_id": content_id}):
                with self.subTest(filters=kwargs):
                    bound = 3 if "content_id" in kwargs else 2
                    graph = get_graph(session, max_nodes=bound, max_edges=3, **kwargs)
                    relations = [edge for edge in graph.edges if edge.kind == "relation"]
                    self.assertEqual(len(relations), 1)
                    self.assertTrue(graph.truncated)
                    self.assertLessEqual(len(graph.nodes), bound)
                    self.assertLessEqual(len(graph.edges), 3)
                    node_ids = {node.id for node in graph.nodes}
                    self.assertTrue(all(
                        edge.source in node_ids and edge.target in node_ids
                        for edge in graph.edges
                    ))
                    self.assertFalse(any(node.label.startswith("AAA") for node in graph.nodes))

    def test_adding_isolated_mentions_does_not_displace_visible_relations(self):
        self.seed_connected_graph()
        with Session(self.engine) as session:
            before = get_graph(session, max_nodes=2, max_edges=1)
        self.seed_connected_graph(noise=20, kind="video_summary")
        with Session(self.engine) as session:
            after = get_graph(session, max_nodes=2, max_edges=1)
        self.assertEqual([edge.id for edge in before.edges], [edge.id for edge in after.edges])
        self.assertEqual(len(after.edges), 1)

    def test_relation_priority_preserves_focus_hops_type_and_assertion_filters(self):
        self.seed_connected_graph(noise=20)
        with Session(self.engine) as session:
            px4 = uuid.UUID(
                list_entities(session, q="PX4", entity_type="software")["items"][0]["id"]
            )
            one = get_graph(session, entity_id=px4, hops=1)
            two = get_graph(session, entity_id=px4, hops=2)
            self.assertEqual({node.label for node in one.nodes}, {"PX4", "GPS"})
            self.assertEqual({node.label for node in two.nodes}, {"PX4", "GPS", "RTK"})
            self.assertEqual(len(one.edges), 1)
            self.assertEqual(len(two.edges), 2)
            typed = get_graph(session, entity_type="technology", max_nodes=2)
            self.assertEqual({node.label for node in typed.nodes}, {"GPS", "RTK"})
            self.assertEqual(len(typed.edges), 1)
            asserted = get_graph(session, assertion="asserted", max_nodes=2)
            self.assertEqual({node.label for node in asserted.nodes}, {"PX4", "GPS"})
            self.assertEqual(len(asserted.edges), 1)

    def test_empty_material_deactivates_previous_projection(self):
        content_id = self.seed_video()
        self.queue()
        process_next(self.engine, StubExtractor())
        with Session(self.engine) as session, session.begin():
            chunk = session.scalar(
                select(ContentChunk).where(ContentChunk.content_item_id == content_id)
            )
            chunk.embed_status = "obsolete"
        self.queue()
        self.assertEqual(process_next(self.engine, StubExtractor())["status"], "success")
        with Session(self.engine) as session:
            self.assertEqual(overview(session)["relations"], 0)

    def test_superseded_pending_tasks_are_retired(self):
        content_id = self.seed_video()
        self.queue()
        self.change_transcript(content_id)
        self.queue()
        with Session(self.engine) as session:
            statuses = session.scalars(select(VideoGraphBuild.status)).all()
            self.assertEqual(sorted(statuses), ["obsolete", "pending"])
        self.assertEqual(process_next(self.engine, StubExtractor())["status"], "success")

    def test_migration_upgrade_and_downgrade_then_worker(self):
        path = Path(app.__file__).parent.parent / "alembic/versions/0005_video_knowledge_graph.py"
        spec = importlib.util.spec_from_file_location("video_graph_migration", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        with self.engine.begin() as connection:
            with Operations.context(MigrationContext.configure(connection)):
                module.downgrade()
                self.assertNotIn("video_graph_builds", inspect(connection).get_table_names())
                module.upgrade()
                for name in (
                    "video_graph_builds",
                    "video_graph_states",
                    "video_graph_aliases",
                    "video_graph_relations",
                    "video_graph_evidence",
                ):
                    self.assertEqual(
                        {c["name"] for c in inspect(connection).get_columns(name)},
                        set(Base.metadata.tables[name].c.keys()),
                    )
        self.seed_video()
        self.queue()
        self.assertEqual(process_next(self.engine, StubExtractor())["status"], "success")

    def test_api_scope_validation_and_retrieval(self):
        from app.api.deps import get_session
        from app.main import create_app
        from fastapi.testclient import TestClient

        content_id = self.seed_video()
        self.queue()
        process_next(self.engine, StubExtractor())
        engine = self.engine

        class ReadSession:
            async def run_sync(self, callback):
                with Session(engine) as session:
                    return callback(session)

        async def override():
            yield ReadSession()

        api = create_app()
        api.dependency_overrides[get_session] = override
        with TestClient(api) as client:
            graph = client.get("/video-graph/graph", params={"content_item_id": str(content_id)})
            self.assertEqual(graph.status_code, 200)
            relation = next(edge for edge in graph.json()["edges"] if edge["kind"] == "relation")
            evidence = client.get(
                f'/video-graph/relations/{relation["id"]}/evidence',
                params={"content_item_id": str(content_id)},
            )
            self.assertEqual(evidence.status_code, 200)
            self.assertEqual(evidence.json()["total"], 1)
            self.assertEqual(client.get("/video-graph/graph?hops=3").status_code, 422)
            self.assertEqual(client.get("/video-graph/missing").status_code, 404)
            result = client.post(
                "/video-graph/retrieve", json={"query": "PX4", "content_item_id": str(content_id)}
            )
            self.assertEqual(result.status_code, 200)
            self.assertIn("PX4 uses GPS.", result.json()["context"])


if __name__ == "__main__":
    unittest.main()
