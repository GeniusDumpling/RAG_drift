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

    def queue(self, *, retry=False, version=StubExtractor.version):
        with Session(self.engine) as session, session.begin():
            return enqueue_videos(session, extractor_version=version, retry_failed=retry)

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
