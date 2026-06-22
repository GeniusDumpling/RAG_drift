from __future__ import annotations

from datetime import UTC, datetime

import pytest
from app.models.content import ContentChunk, ContentItem, RawPage
from app.models.control import CrawlJob, CrawlRun, SourceSite
from app.services.embedding_reindex import reindex_embeddings
from app.services.retrieval import _MEMORY_COLLECTIONS
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession


class FakeEmbedding:
    model_name = "fake-bge-small-zh"
    dimension = 4

    def embed(self, text: str) -> list[float]:
        if "fail-me" in text:
            raise RuntimeError("fake embedding failure")
        return [1.0, 0.0, 0.0, 0.0]


async def _seed_chunk(
    session: AsyncSession,
    *,
    embed_text: str = "遥控器 图传 设置",
) -> ContentChunk:
    now = datetime(2026, 6, 22, 12, 0, tzinfo=UTC)
    source = SourceSite(
        name="BGE Test Source",
        site_type="forum",
        base_url="https://example.test",
        allowed_domains=["example.test"],
        fetch_mode="http",
        default_language="zh",
        active=True,
        config_json={},
    )
    session.add(source)
    await session.flush()

    job = CrawlJob(
        source_site_id=source.id,
        name="BGE Test Job",
        trigger_mode="manual",
        cron_expr=None,
        seed_config_json={},
        parser_profile="test",
        max_pages=1,
        enabled=True,
        agent_policy_json={},
    )
    session.add(job)
    await session.flush()

    run = CrawlRun(
        source_site_id=source.id,
        crawl_job_id=job.id,
        trigger_type="manual",
        execution_mode="agent_assisted",
        seed_url="https://example.test/thread/1",
        status="success",
        started_at=now,
        finished_at=now,
        discovered_count=1,
        fetched_count=1,
        parsed_count=1,
        extracted_count=1,
        deduped_count=0,
        chunked_count=1,
        embedded_count=0,
        error_count=0,
        config_snapshot_json={},
        error_message=None,
        created_at=now,
    )
    session.add(run)
    await session.flush()

    raw_page = RawPage(
        source_site_id=source.id,
        crawl_run_id=run.id,
        requested_url="https://example.test/thread/1",
        final_url="https://example.test/thread/1",
        http_status=200,
        content_type="text/html",
        response_headers_json={},
        raw_html="<p>遥控器 图传 设置</p>",
        raw_text="遥控器 图传 设置",
        raw_json={},
        fetched_at=now,
        fetch_error=None,
        parser_profile="test",
        extraction_method="test",
        extraction_confidence=0.9,
        parse_status="success",
        parse_error=None,
        body_hash="raw-hash",
        created_at=now,
    )
    session.add(raw_page)
    await session.flush()

    item = ContentItem(
        source_site_id=source.id,
        raw_page_id=raw_page.id,
        crawl_run_id=run.id,
        author_id=None,
        parent_item_id=None,
        thread_root_id=None,
        item_type="thread",
        title="遥控器图传设置",
        canonical_url="https://example.test/thread/1",
        source_url="https://example.test/thread/1",
        published_at=now,
        language="zh",
        raw_text="遥控器 图传 设置",
        cleaned_text="遥控器 图传 设置",
        summary_text="图传设置摘要",
        structured_by="test",
        extraction_confidence=0.9,
        tags=["dji", "图传"],
        metadata_json={},
        content_hash="content-hash",
        dedup_key=f"dedup-{embed_text}",
        search_tsv=None,
    )
    session.add(item)
    await session.flush()

    chunk = ContentChunk(
        content_item_id=item.id,
        chunk_index=0,
        char_start=0,
        char_end=8,
        display_text="遥控器 图传 设置",
        embed_text=embed_text,
        token_count=4,
        chunk_metadata_json={"chunker_version": "test"},
        qdrant_point_id=None,
        vector_backend=None,
        vector_point_id=None,
        embedded_at=None,
        embed_status="pending",
        embed_error=None,
    )
    session.add(chunk)
    await session.commit()
    await session.refresh(chunk)
    return chunk


@pytest.mark.asyncio
async def test_reindex_embeddings_indexes_pending_chunk_and_updates_metadata(
    db_session: AsyncSession,
) -> None:
    _MEMORY_COLLECTIONS.clear()
    chunk = await _seed_chunk(db_session)

    summary = await reindex_embeddings(
        db_session,
        qdrant_url="memory://bge-reindex-test",
        collection="content_chunks_bge_small_zh_v1_test",
        embedding=FakeEmbedding(),
        reset_collection=True,
    )

    refreshed = await db_session.scalar(select(ContentChunk).where(ContentChunk.id == chunk.id))
    assert refreshed is not None
    assert summary.total == 1
    assert summary.succeeded == 1
    assert summary.failed == 0
    assert summary.collection == "content_chunks_bge_small_zh_v1_test"
    assert summary.model == "fake-bge-small-zh"
    assert summary.dimension == 4
    assert refreshed.embed_status == "success"
    assert refreshed.embed_error is None
    assert refreshed.vector_backend == "memory"
    assert refreshed.vector_point_id == str(chunk.id)
    assert refreshed.qdrant_point_id is None
    assert refreshed.embedded_at is not None
    assert refreshed.chunk_metadata_json["embedding_model"] == "fake-bge-small-zh"
    assert refreshed.chunk_metadata_json["embedding_dimension"] == 4
    assert (
        refreshed.chunk_metadata_json["vector_collection"]
        == "content_chunks_bge_small_zh_v1_test"
    )
    memory_collection = _MEMORY_COLLECTIONS[
        ("memory://bge-reindex-test", "content_chunks_bge_small_zh_v1_test")
    ]
    assert str(chunk.id) in memory_collection.points
    assert memory_collection.points[str(chunk.id)].payload["source_site_id"]
    assert memory_collection.points[str(chunk.id)].payload["tags"] == ["dji", "图传"]


@pytest.mark.asyncio
async def test_reindex_embeddings_marks_failed_chunk_and_continues(
    db_session: AsyncSession,
) -> None:
    _MEMORY_COLLECTIONS.clear()
    failing_chunk = await _seed_chunk(db_session, embed_text="fail-me")

    summary = await reindex_embeddings(
        db_session,
        qdrant_url="memory://bge-reindex-failure-test",
        collection="content_chunks_bge_failure_test",
        embedding=FakeEmbedding(),
        reset_collection=True,
    )

    refreshed = await db_session.scalar(
        select(ContentChunk).where(ContentChunk.id == failing_chunk.id)
    )
    assert refreshed is not None
    assert summary.total == 1
    assert summary.succeeded == 0
    assert summary.failed == 1
    assert refreshed.embed_status == "failed"
    assert refreshed.vector_backend == "memory"
    assert refreshed.vector_point_id is None
    assert refreshed.qdrant_point_id is None
    assert refreshed.embedded_at is None
    assert refreshed.embed_error is not None
    assert "fake embedding failure" in refreshed.embed_error
