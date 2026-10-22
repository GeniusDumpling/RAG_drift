"""Durable forum pipeline tests. No external forum/model calls.

Default: temporary SQLite with portable copies of existing table definitions.
Set FORUM_TEST_DATABASE_URL to also exercise PostgreSQL in an isolated random schema;
only that generated schema is created/dropped, never existing application tables.
"""
# ruff: noqa: E402

from __future__ import annotations

import copy
import importlib
import io
import os
import subprocess
import sys
import uuid
from contextlib import contextmanager
from email.message import Message
from pathlib import Path
from types import SimpleNamespace
from urllib.error import HTTPError

import pytest
from sqlalchemy import JSON, DefaultClause, MetaData, Text, create_engine, func, select, text
from sqlalchemy.dialects.postgresql import JSONB, TSVECTOR
from sqlalchemy.orm import Session

SCRIPTS = Path(__file__).resolve().parents[2] / "tools/forum-crawler/scripts"
sys.path.insert(0, str(SCRIPTS))
common = importlib.import_module("discourse_common")
storage = importlib.import_module("discourse_storage")
ingest = importlib.import_module("discourse_ingest")
processor = importlib.import_module("discourse_process")
topics = importlib.import_module("discourse_topics")
migration = importlib.import_module("discourse_migrate_topics")
filters = importlib.import_module("discourse_filter")

from app.db.base import utcnow
from app.models.content import Author, ContentChunk, ContentItem, RawPage
from app.models.control import CrawlJob, CrawlRun, CrawlRunEvent, SourceSite

TABLES = [
    m.__table__
    for m in (
        SourceSite,
        CrawlJob,
        CrawlRun,
        Author,
        RawPage,
        ContentItem,
        ContentChunk,
        CrawlRunEvent,
        storage.ForumSyncState,
        storage.ForumTopicTask,
    )
]


@pytest.fixture()
def engine(tmp_path):
    url = os.environ.get("FORUM_TEST_DATABASE_URL")
    if url:
        original = create_engine(url)
        schema = "forum_test_" + uuid.uuid4().hex
        with original.begin() as connection:
            connection.execute(text(f'CREATE SCHEMA "{schema}"'))
        database = original.execution_options(schema_translate_map={None: schema})
        try:
            storage.Base.metadata.create_all(database, tables=TABLES)
            yield database
        finally:
            with original.begin() as connection:
                connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
            original.dispose()
    else:
        database = create_engine("sqlite:///" + str(tmp_path / "forum.db"))
        metadata = MetaData()
        for table in TABLES:
            clone = table.to_metadata(metadata)
            for column in clone.columns:
                if isinstance(column.type, JSONB):
                    column.type = JSON()
                if isinstance(column.type, TSVECTOR):
                    column.type = Text()
                if column.server_default and "::jsonb" in str(column.server_default.arg):
                    column.server_default = DefaultClause(
                        text(str(column.server_default.arg).replace("::jsonb", ""))
                    )
        metadata.create_all(database)
        try:
            yield database
        finally:
            database.dispose()


@pytest.fixture()
def session(engine):
    with Session(engine, expire_on_commit=False) as database:
        yield database


class FakeClient:
    def __init__(self, routes):
        self.routes = routes
        self.calls = []

    def get(self, path):
        self.calls.append(path)
        response = self.routes[path]
        if isinstance(response, Exception):
            raise response
        return copy.deepcopy(response)


def topic(count=3, *, topic_id=7, category_id=25):
    posts = [
        {
            "id": 100 + n,
            "topic_id": topic_id,
            "post_number": n,
            "username": f"user{n}",
            "user_id": n,
            "post_type": 1,
            "raw": "PX4 EKF GPS diagnostic report" if n == 1 else f"Set EKF parameter value {n}",
            "reply_to_post_number": n - 1 if n > 1 else None,
            "created_at": "2026-10-20T00:00:00Z",
            "updated_at": "2026-10-20T00:00:00Z",
        }
        for n in range(1, count + 1)
    ]
    return {
        "id": topic_id,
        "title": "GPS drift diagnosis",
        "category_id": category_id,
        "posts_count": count,
        "post_stream": {"stream": [p["id"] for p in posts], "posts": posts},
    }


def environment(session, *, watermark=None):
    source, job = storage.source_and_job(session, "px4", common.FORUMS["px4"], "en")
    state = storage.ForumSyncState(
        forum="px4",
        source_site_id=source.id,
        state_json={} if watermark is None else {"watermark": watermark},
    )
    run = CrawlRun(
        source_site_id=source.id,
        crawl_job_id=job.id,
        trigger_type="manual",
        execution_mode="test",
        status="running",
        started_at=utcnow(),
    )
    session.add_all([state, run])
    session.commit()
    return source, state, run


def snapshot(session, source, run, data):
    raw = storage.archive_response(
        session,
        source,
        run,
        "/t/7.json",
        {
            "topic": data,
            "complete": True,
            "category_path": [
                {"id": 25, "slug": "px4", "name": "PX4 Autopilot"},
            ],
        },
        profile=storage.TOPIC_PROFILE,
    )
    session.commit()
    return raw


def page(*pairs):
    return {"latest_posts": [{"id": pid, "topic_id": tid} for pid, tid in pairs]}


def test_complete_topic_batches_not_just_first_20():
    full = topic(43)
    partial = copy.deepcopy(full)
    partial["post_stream"]["posts"] = partial["post_stream"]["posts"][:20]
    from urllib.parse import urlencode

    routes = {"/t/7.json?include_raw=true": partial}
    for start in (20, 40):
        posts = full["post_stream"]["posts"][start : start + 20]
        query = urlencode([("post_ids[]", p["id"]) for p in posts] + [("include_raw", "true")])
        routes[f"/t/7/posts.json?{query}"] = {"post_stream": {"posts": posts}}
    archived = []
    result = common.fetch_topic(FakeClient(routes), 7, archive=lambda p, d: archived.append(p))
    assert len(result["post_stream"]["posts"]) == 43
    assert len(archived) == 3


def test_missing_batch_is_failure_not_truncated_success():
    data = topic(2)
    data["post_stream"]["posts"] = data["post_stream"]["posts"][:1]
    client = FakeClient(
        {
            "/t/7.json?include_raw=true": data,
            "/t/7/posts.json?post_ids%5B%5D=102&include_raw=true": {"post_stream": {"posts": []}},
        }
    )
    with pytest.raises(common.DiscourseError, match="missing post IDs"):
        common.fetch_topic(client, 7)


def test_transport_retries_429_and_honors_retry_after():
    headers = Message()
    headers["Retry-After"] = "5"
    attempts = []
    waits = []

    @contextmanager
    def opener(request, timeout):
        attempts.append(request.full_url)
        if len(attempts) == 1:
            raise HTTPError(request.full_url, 429, "slow", headers, None)
        yield io.BytesIO(b'{"ok": true}')

    client = common.DiscourseClient(
        "https://example.invalid", delay=0, opener=opener, sleep=waits.append
    )
    assert client.get("/posts.json") == {"ok": True}
    assert len(attempts) == 2
    assert 5 in waits


def test_long_retry_after_is_deferred_and_404_not_retried():
    for status, wait in [(429, "300"), (404, "0")]:
        headers = Message()
        headers["Retry-After"] = wait
        calls = []

        def opener(request, timeout, status=status, headers=headers, calls=calls):
            calls.append(1)
            raise HTTPError(request.full_url, status, "error", headers, None)

        with pytest.raises(common.DiscourseError) as error:
            common.DiscourseClient(
                "https://example.invalid", delay=0, opener=opener, sleep=lambda _: None
            ).get("/posts.json")
        assert error.value.status == status
        assert len(calls) == 1


def test_discovery_resume_does_not_advance_watermark_early(session):
    source, state, run = environment(session, watermark=100)
    client = FakeClient(
        {
            "/posts.json": page((110, 1), (109, 2)),
            "/posts.json?before=109": page((108, 7), (100, 8)),
        }
    )
    first = ingest.discover_stream(
        session, source, state, client, run, mode="incremental", max_pages=1
    )
    assert not first["discovery_complete"]
    assert state.state_json["watermark"] == 100
    assert state.state_json["scan_before"] == 109
    session.expire_all()  # Simulate restart/reload of durable progress.
    second = ingest.discover_stream(
        session, source, state, client, run, mode="incremental", max_pages=1
    )
    assert second["discovery_complete"]
    assert state.state_json["watermark"] == 110
    tasks = session.scalars(select(storage.ForumTopicTask)).all()
    assert {t.topic_id for t in tasks} == {1, 2, 7}


def test_failed_discovery_keeps_committed_page_and_retries_next_page(session):
    source, state, run = environment(session, watermark=100)
    client = FakeClient(
        {
            "/posts.json": page((110, 7), (109, 7)),
            "/posts.json?before=109": common.DiscourseError("offline"),
        }
    )
    with pytest.raises(common.DiscourseError):
        ingest.discover_stream(session, source, state, client, run, mode="incremental", max_pages=2)
    session.rollback()
    assert state.state_json["watermark"] == 100
    assert state.state_json["scan_before"] == 109
    assert session.scalar(select(func.count()).select_from(storage.ForumTopicTask)) == 1


def test_queue_checkpoint_roll_back_together(session, monkeypatch):
    source, state, run = environment(session, watermark=100)

    def fail_after_enqueue(session, source_id, ids, **kwargs):
        storage.enqueue(session, source_id, ids, **kwargs)
        session.flush()
        raise RuntimeError("interrupted before cursor commit")

    monkeypatch.setattr(ingest, "enqueue", fail_after_enqueue)
    with pytest.raises(RuntimeError):
        ingest.discover_stream(
            session,
            source,
            state,
            FakeClient({"/posts.json": page((110, 7))}),
            run,
            mode="incremental",
            max_pages=1,
        )
    session.rollback()
    assert state.state_json == {"watermark": 100}
    assert session.scalar(select(func.count()).select_from(storage.ForumTopicTask)) == 0


def test_bootstrap_and_backfill_are_independent(session):
    source, state, run = environment(session)
    ingest.discover_stream(
        session,
        source,
        state,
        FakeClient({"/posts.json": page((110, 7))}),
        run,
        mode="incremental",
        max_pages=1,
    )
    assert state.state_json["watermark"] == 110
    ingest.discover_stream(
        session,
        source,
        state,
        FakeClient({"/posts.json": page((110, 7))}),
        run,
        mode="backfill",
        max_pages=1,
    )
    assert state.state_json["backfill_before"] == 110
    assert state.state_json["watermark"] == 110


def test_partial_capture_commits_raw_and_keeps_retryable_task(session):
    source, _, run = environment(session)
    storage.enqueue(session, source.id, [7])
    session.commit()
    data = topic(2)
    data["post_stream"]["posts"] = data["post_stream"]["posts"][:1]
    client = FakeClient(
        {
            "/t/7.json?include_raw=true": data,
            "/t/7/posts.json?post_ids%5B%5D=102&include_raw=true": common.DiscourseError("offline"),
        }
    )
    stats, errors = ingest.capture_tasks(session, source, run, client, [], limit=1)
    task = session.get(storage.ForumTopicTask, (source.id, 7))
    assert stats["failed"] == 1 and errors
    assert task.capture_status == "retry"
    assert task.snapshot_id is None
    assert session.scalar(select(func.count()).select_from(RawPage)) == 1
    assert session.scalar(select(func.count()).select_from(ContentItem)) == 0


def test_capture_process_relationships_and_idempotent_replay(session):
    source, _, run = environment(session)
    data = topic()
    raw = snapshot(session, source, run, data)
    policy = filters.load_policy()
    first = processor.normalize_topic(session, source, raw, policy, run)
    session.commit()
    assert first["posts_created"] == 3
    item = session.scalar(select(ContentItem))
    assert first["items_created"] == 1
    assert item.thread_root_id == item.id and item.parent_item_id is None
    assert item.metadata_json["posts"]["103"]["reply_to_post_number"] == 2
    assert item.metadata_json["post_count"] == 3
    assert "Set EKF parameter value 3" in item.cleaned_text
    chunks = session.scalars(select(ContentChunk)).all()
    assert all(c.content_item_id == item.id for c in chunks)
    assert {c.chunk_metadata_json["post_id"] for c in chunks} == {101, 102, 103}
    assert all(
        c.chunk_metadata_json["canonical_url"].endswith(
            "/" + str(c.chunk_metadata_json["post_number"])
        )
        for c in chunks
    )
    assert any("Question: PX4 EKF" in c.embed_text for c in chunks)
    assert any("Parent reply: Set EKF parameter value 2" in c.embed_text for c in chunks)
    second = processor.normalize_topic(session, source, raw, policy, run)
    session.commit()
    assert second["deduped"] == 3
    assert second["chunks_created"] == 0
    assert session.scalar(select(func.count()).select_from(ContentItem)) == 1
    assert session.scalar(select(func.count()).select_from(ContentChunk)) == len(chunks)


def test_edits_update_identity_and_retire_old_chunks(session):
    source, _, run = environment(session)
    data = topic()
    processor.normalize_topic(
        session, source, snapshot(session, source, run, data), filters.load_policy(), run
    )
    session.commit()
    root = session.scalar(select(ContentItem).where(ContentItem.item_type == "thread"))
    root_id = root.id
    data["post_stream"]["posts"][0]["raw"] = "PX4 GPS issue solved with revised EKF settings"
    outcome = processor.normalize_topic(
        session, source, snapshot(session, source, run, data), filters.load_policy(), run
    )
    session.commit()
    assert outcome["posts_created"] == 0
    assert outcome["posts_updated"] == 3  # Reply embedding context also changes.
    assert root.id == root_id
    assert session.scalar(select(func.count()).select_from(ContentItem)) == 1
    assert (
        session.scalar(
            select(func.count())
            .select_from(ContentChunk)
            .where(ContentChunk.embed_status == "obsolete")
        )
        > 0
    )


def test_new_reply_old_topic_and_removed_post(session):
    source, _, run = environment(session)
    processor.normalize_topic(
        session, source, snapshot(session, source, run, topic(2)), filters.load_policy(), run
    )
    session.commit()
    outcome = processor.normalize_topic(
        session, source, snapshot(session, source, run, topic(3)), filters.load_policy(), run
    )
    session.commit()
    assert outcome["posts_created"] == 1
    data = topic(3)
    data["post_stream"]["posts"].pop(1)
    data["post_stream"]["stream"].remove(102)
    processor.normalize_topic(
        session, source, snapshot(session, source, run, data), filters.load_policy(), run
    )
    session.commit()
    old = session.scalar(select(ContentItem)).metadata_json["posts"]["102"]
    assert old["source_visibility"] == "missing"
    assert not old["rag_eligible"]
    assert all(
        c.embed_status == "obsolete"
        for c in session.scalars(
            select(ContentChunk).where(
                ContentChunk.chunk_metadata_json["post_id"].as_integer() == 102
            )
        )
    )


def test_filter_mixed_categories_and_courtesy_without_losing_confirmations():
    policy = filters.load_policy()
    data = topic()
    for category in ["general", "uncategorized"]:
        decision = filters.classify_topic(
            "px4", data, data["post_stream"]["posts"], [{"id": 1, "slug": category}], policy
        )
        assert decision["decision"] == "keep"
    assert (
        filters.classify_topic("px4", data, [], [{"id": 28, "slug": "looking-for-jobs"}], policy)[
            "decision"
        ]
        == "reject"
    )
    assert not filters.classify_post({"post_number": 2, "raw": "Thank you!"})["indexable"]
    assert filters.classify_post({"post_number": 2, "raw": "Thanks, that fixed it!"})["indexable"]
    assert (
        filters.classify_topic("px4", {"title": "Hello everybody"}, [], [], policy)["decision"]
        == "review"
    )


def test_processing_failure_preserves_snapshot_and_continues(session, monkeypatch):
    source, _, run = environment(session)
    raw = snapshot(session, source, run, topic())
    task = storage.ForumTopicTask(
        source_site_id=source.id,
        topic_id=7,
        snapshot_id=raw.id,
        capture_status="captured",
        process_status="pending",
    )
    session.add(task)
    session.commit()

    def fail(*args, **kwargs):
        raise RuntimeError("chunker failed")

    monkeypatch.setattr(processor, "build_chunks", fail)
    result = processor.process_forum(
        session, "px4", processor.parse_args([]), filters.load_policy()
    )
    assert result["status"] == "failed"
    assert task.process_status == "retry"
    assert session.get(RawPage, raw.id).raw_json["complete"]
    assert session.scalar(select(func.count()).select_from(ContentItem)) == 0


def test_forum_failure_does_not_block_other_forum(engine, monkeypatch):
    calls = []

    def fake(session, forum, args):
        calls.append(forum)
        if forum == "ardupilot":
            raise RuntimeError("offline")
        return {"forum": forum, "status": "success"}

    monkeypatch.setattr(ingest, "collect_forum", fake)
    result = ingest.ingest(ingest.parse_args([]), engine=engine)
    assert calls == ["ardupilot", "px4"]
    assert result["status"] == "partial"


def test_aliases_and_cli_validation():
    assert ingest.parse_args(["--forums", "px4", "dronecode"]).forums == ["px4"]
    assert ingest.parse_args([]).forums == ["ardupilot", "px4"]
    for argv in [
        ["--topic-id", "7"],
        ["--max-pages", "0"],
        ["--request-delay", "-1"],
        ["--since", "invalid"],
        ["--mode", "search"],
    ]:
        with pytest.raises(SystemExit):
            ingest.parse_args(argv)


def test_wrapper_processes_archives_even_when_capture_fails(tmp_path):
    fake = tmp_path / "fake-python"
    log = tmp_path / "commands.log"
    fake.write_text(
        '#!/bin/bash\necho "$@" >> "$COMMAND_LOG"\n'
        '[[ "$1" != *discourse_ingest.py ]] || exit 1\n'
    )
    fake.chmod(0o755)
    result = subprocess.run(
        ["bash", str(SCRIPTS / "run_scheduled_discourse.sh")],
        env={
            **os.environ,
            "PY": str(fake),
            "COMMAND_LOG": str(log),
            "FORUM_LOCK_FILE": str(tmp_path / "lock"),
        },
        capture_output=True,
        text=True,
    )
    assert result.returncode == 1
    assert "discourse_ingest.py" in log.read_text()
    assert "discourse_process.py" in log.read_text()
    assert "--query" not in log.read_text()


def test_advisory_lock_is_shared_between_capture_and_processing(engine):
    if engine.dialect.name != "postgresql":
        pytest.skip("PostgreSQL advisory lock integration")
    with storage.forum_session(engine, "px4") as first:
        assert first is not None
        with storage.forum_session(engine, "px4") as second:
            assert second is None
        with storage.forum_session(engine, "ardupilot") as other:
            assert other is not None


def test_removed_post_restored_rebuilds_searchable_chunks(session):
    source, _, run = environment(session)
    data = topic(2)
    processor.normalize_topic(
        session, source, snapshot(session, source, run, data), filters.load_policy(), run
    )
    session.commit()
    processor.normalize_topic(
        session, source, snapshot(session, source, run, topic(1)), filters.load_policy(), run
    )
    session.commit()
    outcome = processor.normalize_topic(
        session, source, snapshot(session, source, run, data), filters.load_policy(), run
    )
    session.commit()
    assert outcome["posts_created"] == 0
    restored = session.scalar(select(ContentItem))
    assert restored.metadata_json["posts"]["102"]["rag_eligible"]
    assert (
        session.scalar(
            select(func.count())
            .select_from(ContentChunk)
            .where(
                ContentChunk.content_item_id == restored.id,
                ContentChunk.embed_status == "pending",
                ContentChunk.chunk_metadata_json["post_id"].as_integer() == 102,
            )
        )
        > 0
    )


def test_incremental_work_precedes_historical_backfill(session):
    source, _, run = environment(session)
    storage.enqueue(session, source.id, [1, 2, 3], priority=10)
    storage.enqueue(session, source.id, [7], priority=0)
    session.commit()
    client = FakeClient({"/t/7.json?include_raw=true": topic()})
    stats, errors = ingest.capture_tasks(session, source, run, client, [], limit=1)
    assert stats["topics_captured"] == 1
    assert not errors
    assert client.calls == ["/t/7.json?include_raw=true"]


class FakeIndexer:
    url = "memory://forum-pipeline-test"
    collection = "forum-pipeline-test"
    backend_name = "memory"
    embedding = SimpleNamespace(model_name="test-model", dimension=3)

    def __init__(self):
        self.fail = False
        self.points = {}

    def ensure_collection(self):
        pass

    def upsert_chunk(self, *, chunk_id, embed_text, payload):
        if self.fail:
            raise RuntimeError("embedding offline")
        self.points[str(chunk_id)] = (embed_text, payload)
        return str(chunk_id)

    def delete_chunk(self, *, point_id):
        if self.fail:
            raise RuntimeError("delete offline")
        self.points.pop(point_id, None)


def test_embedding_retries_without_new_capture_and_cleans_obsolete_vectors(session):
    source, _, run = environment(session)
    data = topic(2)
    processor.normalize_topic(
        session, source, snapshot(session, source, run, data), filters.load_policy(), run
    )
    session.commit()
    indexer = FakeIndexer()
    indexer.fail = True
    failed = processor.embed_pending(session, source.id, limit=100, indexer=indexer)
    assert failed["vector_failed"] > 0
    assert session.scalar(select(func.count()).select_from(RawPage)) == 1
    indexer.fail = False
    retried = processor.embed_pending(session, source.id, limit=100, indexer=indexer)
    assert retried["embedded"] == failed["vector_failed"]
    old_ids = set(indexer.points)
    data["post_stream"]["posts"][0]["raw"] = "PX4 GPS issue fixed with EKF config"
    processor.normalize_topic(
        session, source, snapshot(session, source, run, data), filters.load_policy(), run
    )
    session.commit()
    indexer.fail = True
    processor.embed_pending(session, source.id, limit=100, indexer=indexer)
    # Failed cleanup must preserve old point IDs for another attempt.
    assert old_ids.issubset(indexer.points)
    indexer.fail = False
    final = processor.embed_pending(session, source.id, limit=100, indexer=indexer)
    assert final["vectors_deleted"] == len(old_ids)
    assert not old_ids.intersection(indexer.points)
    assert final["embedded"] > 0


def test_vector_limit_not_consumed_by_obsolete_rows_without_points(session):
    source, _, run = environment(session)
    data = topic(1)
    processor.normalize_topic(
        session, source, snapshot(session, source, run, data), filters.load_policy(), run
    )
    session.commit()
    for _ in range(3):
        data["post_stream"]["posts"][0]["raw"] += " revised"
        processor.normalize_topic(
            session, source, snapshot(session, source, run, data), filters.load_policy(), run
        )
        session.commit()
    result = processor.embed_pending(session, source.id, limit=1, indexer=FakeIndexer())
    assert result["embedded"] == 1


def seed_legacy_topic(session, source, run, data):
    """Represent the v2 layout with original per-post bodies and fingerprints."""
    raw = snapshot(session, source, run, data)
    processor.normalize_topic(session, source, raw, filters.load_policy(), run)
    session.commit()
    root = session.scalar(select(ContentItem))
    original_meta = dict(root.metadata_json)
    chunks = session.scalars(select(ContentChunk)).all()
    old_items = {}
    for state in original_meta["posts"].values():
        number = state["post_number"]
        item = (
            root
            if number == 1
            else ContentItem(
                source_site_id=source.id,
                raw_page_id=raw.id,
                crawl_run_id=run.id,
                content_hash="placeholder",
                cleaned_text="",
                canonical_url=state["canonical_url"],
                item_type="comment",
                dedup_key="placeholder-" + str(number),
            )
        )
        if number != 1:
            session.add(item)
        item.item_type = "thread" if number == 1 else "comment"
        item.author_id = uuid.UUID(state["author_id"])
        item.title = data["title"] if number == 1 else f'Reply {number} · {data["title"]}'
        item.cleaned_text = state["cleaned_text"]
        item.raw_text = state["raw_text"]
        item.summary_text = state["raw_text"][:240]
        item.canonical_url = state["canonical_url"]
        item.tags = list(root.tags)
        item.structured_by = topics.POST_CHUNK_VERSION
        item.content_hash = processor.stable_hash(state["raw_text"])
        item.dedup_key = processor.stable_hash(
            f'discourse:{source.base_url}:post:{state["post_id"]}'
        )
        item.metadata_json = {
            **state,
            "forum": "px4",
            "topic_id": data["id"],
            "topic_filter": original_meta["topic_filter"],
        }
        session.flush()
        old_items[number] = item
    # First free all root indices, then restore each post's local indices.
    for c in chunks:
        c.chunk_index += 1000
    session.flush()
    for c in chunks:
        meta = c.chunk_metadata_json
        c.content_item_id = old_items[meta["post_number"]].id
        c.chunk_index = meta["post_chunk_index"]
        c.char_start, c.char_end = meta["post_char_start"], meta["post_char_end"]
        c.chunk_metadata_json = {
            key: meta[key]
            for key in (
                "post_id",
                "topic_id",
                "processing_fingerprint",
                "chunker_version",
                "embed_text_hash",
                "start_char",
                "end_char",
            )
        }
    for number, item in old_items.items():
        item.thread_root_id = root.id
        item.parent_item_id = None if number == 1 else old_items[number - 1].id
    session.commit()
    return root, old_items, raw


def test_legacy_migration_preserves_chunks_references_and_replay(session):
    source, _, run = environment(session)
    root, items, raw = seed_legacy_topic(session, source, run, topic(3))
    root_id = root.id
    event = CrawlRunEvent(
        crawl_run_id=run.id,
        related_content_item_id=items[3].id,
        stage="test",
        level="info",
        event_type="test",
        message="reply reference",
    )
    session.add(event)
    session.commit()
    before = migration.chunk_digest(session.scalars(select(ContentChunk)).all())
    item, count = topics.migrate_legacy_topic(session, source, 7)
    session.commit()
    assert count == 2 and item.id == root_id
    assert item.dedup_key == topics.topic_key(source.base_url, 7)
    assert session.scalar(select(func.count()).select_from(ContentItem)) == 1
    assert migration.chunk_digest(session.scalars(select(ContentChunk)).all()) == before
    session.refresh(event)
    assert event.related_content_item_id == root_id
    assert len(item.metadata_json["posts"]) == 3
    # Unchanged v2 fingerprints retain all old chunks on the first v3 replay.
    outcome = processor.normalize_topic(session, source, raw, filters.load_policy(), run)
    session.commit()
    assert outcome["deduped"] == 3 and outcome["chunks_created"] == 0
    assert topics.migrate_legacy_topic(session, source, 7)[1] == 0
    assert migration.chunk_digest(session.scalars(select(ContentChunk)).all()) == before
    for chunk in session.scalars(select(ContentChunk)):
        meta = chunk.chunk_metadata_json
        state = item.metadata_json["posts"][str(meta["post_id"])]
        assert (
            item.cleaned_text[chunk.char_start : chunk.char_end]
            == state["cleaned_text"][meta["post_char_start"] : meta["post_char_end"]]
        )


def test_auto_migration_and_only_changed_reply_is_rechunked(session):
    source, _, run = environment(session)
    data = topic(3)
    root, _, raw = seed_legacy_topic(session, source, run, data)
    original = {c.id: c.embed_text for c in session.scalars(select(ContentChunk))}
    replay = processor.normalize_topic(session, source, raw, filters.load_policy(), run)
    session.commit()
    assert replay["items_merged"] == 2 and replay["chunks_created"] == 0
    data["post_stream"]["posts"][2]["raw"] += " Use the revised PX4 EKF diagnostic settings."
    outcome = processor.normalize_topic(
        session, source, snapshot(session, source, run, data), filters.load_policy(), run
    )
    session.commit()
    assert outcome["posts_updated"] == 1
    assert outcome["deduped"] == 2
    assert session.scalar(select(func.count()).select_from(ContentItem)) == 1
    assert session.scalar(select(ContentItem)).id == root.id
    for c in session.scalars(select(ContentChunk)):
        if c.id in original:
            assert c.embed_text == original[c.id]
            assert (c.embed_status == "obsolete") == (c.chunk_metadata_json["post_id"] == 103)


def test_migration_keeps_obsolete_generations_and_filtered_posts(session):
    source, _, run = environment(session)
    data = topic(3)
    data["post_stream"]["posts"][2]["raw"] = "Thank you!"
    root, old, _ = seed_legacy_topic(session, source, run, data)
    # Simulate an old obsolete generation whose index is higher than active chunks.
    c = session.scalar(select(ContentChunk).where(ContentChunk.content_item_id == old[2].id))
    obsolete = ContentChunk(
        content_item_id=old[2].id,
        chunk_index=50,
        display_text="old",
        embed_text="old",
        char_start=0,
        char_end=3,
        embed_status="obsolete",
        chunk_metadata_json=dict(c.chunk_metadata_json),
    )
    session.add(obsolete)
    session.commit()
    before = migration.chunk_digest(session.scalars(select(ContentChunk)).all())
    topics.migrate_legacy_topic(session, source, 7)
    session.commit()
    assert migration.chunk_digest(session.scalars(select(ContentChunk)).all()) == before
    assert obsolete.content_item_id == root.id
    assert obsolete.char_start is None and obsolete.char_end is None
    assert len(root.metadata_json["posts"]) == 3
    assert not root.metadata_json["posts"]["103"]["rag_eligible"]
    assert "Thank you!" in root.cleaned_text


def test_append_reply_preserves_existing_chunks_and_topic_offsets(session):
    source, _, run = environment(session)
    processor.normalize_topic(
        session, source, snapshot(session, source, run, topic(2)), filters.load_policy(), run
    )
    session.commit()
    old = {c.id: (c.embed_text, c.chunk_index) for c in session.scalars(select(ContentChunk))}
    processor.normalize_topic(
        session, source, snapshot(session, source, run, topic(3)), filters.load_policy(), run
    )
    session.commit()
    root = session.scalar(select(ContentItem))
    assert root.metadata_json["reply_count"] == 2
    for c in session.scalars(select(ContentChunk)):
        if c.id in old:
            assert (c.embed_text, c.chunk_index) == old[c.id]
            assert c.embed_status == "pending"
        state = root.metadata_json["posts"][str(c.chunk_metadata_json["post_id"])]
        assert c.char_start >= state["char_start"]
        assert c.char_end <= state["char_end"]


def qdrant_legacy(session, source, run):
    import hashlib

    from app.services.embedding_reindex import _build_payload
    from qdrant_client import QdrantClient
    from qdrant_client.http import models

    root, _, _ = seed_legacy_topic(session, source, run, topic(2))
    client = QdrantClient(":memory:")
    client.create_collection(
        "forum-test",
        vectors_config=models.VectorParams(
            size=3,
            distance=models.Distance.COSINE,
        ),
    )
    url = "http://forum-test:6333"
    for c in session.scalars(select(ContentChunk)):
        owner = session.get(ContentItem, c.content_item_id)
        client.upsert(
            "forum-test",
            points=[
                models.PointStruct(
                    id=str(c.id),
                    vector=[1.0, 0.0, 0.0],
                    payload=_build_payload(content_chunk=c, content_item=owner),
                )
            ],
        )
        c.vector_point_id = c.qdrant_point_id = str(c.id)
        c.vector_backend = "qdrant"
        c.embed_status = "success"
        c.chunk_metadata_json = {
            **c.chunk_metadata_json,
            "vector_collection": "forum-test",
            "vector_store_id": hashlib.sha256(url.encode()).hexdigest(),
        }
    session.commit()
    return root, client, url


def test_vector_payload_migration_is_retryable_without_embedding(session, monkeypatch):
    from app.services import embeddings

    source, _, run = environment(session)
    root, client, url = qdrant_legacy(session, source, run)
    ids = [str(c.id) for c in session.scalars(select(ContentChunk))]
    before = {
        str(p.id): p.vector for p in client.retrieve("forum-test", ids=ids, with_vectors=True)
    }
    topics.migrate_legacy_topic(session, source, 7)
    session.commit()
    monkeypatch.setattr(
        embeddings, "build_embedding_service", lambda *a, **k: pytest.fail("model called")
    )
    original = client.batch_update_points
    monkeypatch.setattr(
        client,
        "batch_update_points",
        lambda **kw: (_ for _ in ()).throw(RuntimeError("temporary vector outage")),
    )
    failed = topics.sync_vector_payloads(session, source.id, url=url, client=client)
    assert failed["payloads_failed"] == len(ids)
    assert len(topics.payload_rows(session, source.id)) == len(ids)
    monkeypatch.setattr(client, "batch_update_points", original)
    done = topics.sync_vector_payloads(session, source.id, url=url, client=client)
    assert done["payloads_updated"] == len(ids) and done["payloads_failed"] == 0
    assert not topics.payload_rows(session, source.id)
    for point in client.retrieve("forum-test", ids=ids, with_vectors=True):
        assert point.vector == before[str(point.id)]
        assert point.payload["content_item_id"] == str(root.id)
        assert point.payload["canonical_url"].endswith("/" + str(point.payload["post_number"]))
        assert point.payload["author_name"] == "user" + str(point.payload["post_number"])


def test_migration_preview_backup_and_apply_are_idempotent(session, tmp_path, monkeypatch):
    import gzip
    import json

    import qdrant_client

    source, _, run = environment(session)
    root, client, url = qdrant_legacy(session, source, run)
    monkeypatch.setattr(qdrant_client, "QdrantClient", lambda **kwargs: client)
    preview = migration.migrate_forum(session, source)
    assert preview["legacy_items"] == 2
    assert session.scalar(select(func.count()).select_from(ContentItem)) == 2
    result = migration.migrate_forum(
        session,
        source,
        apply=True,
        backup_dir=tmp_path,
        url=url,
        store_url=url,
    )
    assert result["items_before"] == 2 and result["items_after"] == 1
    assert result["payloads_failed"] == 0
    with gzip.open(result["backup"], "rt") as f:
        backup = json.load(f)
    assert len(backup["items"]) == 2 and len(backup["vectors"]) > 0
    assert (
        migration.chunk_digest(session.scalars(select(ContentChunk)).all())
        == backup["chunk_digest"]
    )
    again = migration.migrate_forum(
        session,
        source,
        apply=True,
        backup_dir=tmp_path,
        url=url,
        store_url=url,
    )
    assert again["items_merged"] == 0 and again["items_after"] == 1
    assert session.scalar(select(ContentItem)).id == root.id


@pytest.mark.asyncio
async def test_retrieval_keeps_reply_author_link_and_snapshot(session):
    from app.schemas.search import SearchFilters
    from app.services.retrieval import _hydrate_and_rank_evidence, _SearchHit

    source, _, run = environment(session)
    raw = snapshot(session, source, run, topic(2))
    processor.normalize_topic(session, source, raw, filters.load_policy(), run)
    session.commit()
    root = session.scalar(select(ContentItem))
    c = session.scalar(
        select(ContentChunk).where(
            ContentChunk.chunk_metadata_json["post_id"].as_integer() == 102,
        )
    )

    class AsyncAdapter:
        async def execute(self, statement):
            return session.execute(statement)

    evidence = await _hydrate_and_rank_evidence(
        AsyncAdapter(),
        hits=[
            _SearchHit(
                chunk_id=c.id,
                content_item_id=root.id,
                score=0.9,
                matched_by="vector",
                vector_score=0.9,
            )
        ],
        filters=SearchFilters(),
        top_k=5,
        snippet_terms=[],
    )
    assert len(evidence) == 1
    assert evidence[0].content_item_id == root.id
    assert evidence[0].canonical_url.endswith("/t/7/2")
    assert evidence[0].author_name == "user2"
    assert evidence[0].raw_page_id == raw.id


@pytest.mark.asyncio
async def test_old_reply_detail_id_resolves_to_topic(session, engine):
    from app.repositories.contents import ContentsRepository

    if engine.dialect.name != "postgresql":
        pytest.skip("PostgreSQL JSONB alias lookup")
    source, _, run = environment(session)
    root, old, _ = seed_legacy_topic(session, source, run, topic(2))
    old_reply_id = old[2].id
    topics.migrate_legacy_topic(session, source, 7)
    session.commit()

    class AsyncAdapter:
        async def execute(self, statement):
            return session.execute(statement)

        async def scalars(self, statement):
            return session.scalars(statement)

    detail = await ContentsRepository(AsyncAdapter()).get_content_detail(old_reply_id)
    assert detail.item.id == root.id
    assert detail.chunks
    assert all(c.content_item_id == root.id for c in detail.chunks)


def test_payload_query_does_not_duplicate_topic_bodies_per_chunk(session, engine):
    from sqlalchemy import event

    source, _, run = environment(session)
    processor.normalize_topic(
        session, source, snapshot(session, source, run, topic(20)), filters.load_policy(), run
    )
    for c in session.scalars(select(ContentChunk)):
        c.chunk_metadata_json = {**c.chunk_metadata_json, "vector_payload_pending": True}
    session.commit()
    statements = []

    def record(conn, cursor, statement, parameters, context, many):
        statements.append(statement)

    event.listen(engine, "before_cursor_execute", record)
    try:
        rows = topics.payload_rows(session, source.id)
    finally:
        event.remove(engine, "before_cursor_execute", record)
    assert len(rows) >= 20
    assert len(statements) == 2
    assert "content_items.cleaned_text" not in statements[0].split("FROM")[0]
    assert "content_items.metadata_json" not in statements[0].split("FROM")[0]
    assert len({id(item) for _, item in rows}) == 1


def test_migration_accepts_some_topics_already_using_topic_items(session, tmp_path):
    source, _, run = environment(session)
    seed_legacy_topic(session, source, run, topic(3))
    data = topic(2, topic_id=8)
    for p in data["post_stream"]["posts"]:
        p["id"] += 100
    data["post_stream"]["stream"] = [p["id"] for p in data["post_stream"]["posts"]]
    processor.normalize_topic(
        session, source, snapshot(session, source, run, data), filters.load_policy(), run
    )
    session.commit()
    result = migration.migrate_forum(
        session,
        source,
        apply=True,
        backup_dir=tmp_path,
        url="http://unused:6333",
        store_url="http://unused:6333",
    )
    assert result["items_before"] == 4 and result["items_after"] == 2
    assert result["items_merged"] == 2
    assert sum(len(i.metadata_json["posts"]) for i in session.scalars(select(ContentItem))) == 5
