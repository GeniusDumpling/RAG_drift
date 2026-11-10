"""Shared source-database and reviewed-extraction fixtures; no PostgreSQL graph writer."""

import os
import unittest
import uuid

from app.db.base import Base, utcnow
from app.models.content import ContentChunk, ContentItem, RawPage
from app.models.control import CrawlJob, CrawlRun, SourceSite
from app.schemas.video_graph import ChunkExtraction, VideoAssessment
from app.services.video_graph.identity import extraction_version
from app.services.video_graph.review import review_extraction
from sqlalchemy import create_engine, select, text
from sqlalchemy.orm import Session


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


def reviewed_fixture(material, chunk, draft, context=None):
    from app.services.video_graph.identity import neighbor_context

    if context is None:
        context = neighbor_context(material, chunk)
    response = {
        "entities": [
            dict(
                key=e.key,
                accepted=True,
                entity_type=e.entity_type,
                canonical_name=e.surface_text,
                identity_scope="global",
                identity_source="current_chunk",
                identity_quote=e.surface_text,
                accepted_attributes=list(e.attributes),
                reason="fixture source",
            )
            for e in draft.entities
        ],
        "relations": [
            dict(
                index=i,
                supported=True,
                reason="fixture relation",
                assertion=r.assertion,
                modality=r.qualifiers.get("modality", "actual"),
            )
            for i, r in enumerate(draft.relations)
        ],
    }
    return review_extraction(lambda *_: response, material, chunk, context, draft, "")


class StubExtractor:
    version = extraction_version("video-graph-v2", "fixture")

    def __init__(self, fail_at=None, relevant=True):
        self.calls = self.assessments = 0
        self.fail_at, self.relevant = fail_at, relevant
        self.on_extract = None

    def assess(self, material):
        self.assessments += 1
        return VideoAssessment(relevant=self.relevant, reason="fixture")

    def extract(self, material, chunk, context):
        self.calls += 1
        if self.on_extract:
            self.on_extract()
        if self.calls == self.fail_at:
            raise RuntimeError("simulated provider failure")
        return reviewed_fixture(material, chunk, extraction_for(chunk.text), context)


class SourceDatabase(unittest.TestCase):
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

    def change_transcript(self, content_id):
        with Session(self.engine) as session, session.begin():
            chunk = session.scalar(
                select(ContentChunk).where(ContentChunk.content_item_id == content_id)
            )
            chunk.display_text = "PX4 uses RTK."
