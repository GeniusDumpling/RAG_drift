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
    items = {i.metadata_json["post_number"]: i for i in session.scalars(select(ContentItem))}
    assert items[2].parent_item_id == items[1].id
    assert items[3].parent_item_id == items[2].id
    assert all(i.thread_root_id == items[1].id for i in items.values())
    chunks = session.scalars(select(ContentChunk)).all()
    assert any("Question: PX4 EKF" in c.embed_text for c in chunks)
    assert any("Parent reply: Set EKF parameter value 2" in c.embed_text for c in chunks)
    second = processor.normalize_topic(session, source, raw, policy, run)
    session.commit()
    assert second["deduped"] == 3
    assert second["chunks_created"] == 0
    assert session.scalar(select(func.count()).select_from(ContentItem)) == 3
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
    assert session.scalar(select(func.count()).select_from(ContentItem)) == 3
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
    old = session.scalar(
        select(ContentItem).where(ContentItem.metadata_json["post_id"].as_integer() == 102)
    )
    assert old.metadata_json["source_visibility"] == "missing"
    assert not old.metadata_json["rag_eligible"]


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
    restored = session.scalar(
        select(ContentItem).where(ContentItem.metadata_json["post_id"].as_integer() == 102)
    )
    assert restored.metadata_json["rag_eligible"]
    assert (
        session.scalar(
            select(func.count())
            .select_from(ContentChunk)
            .where(
                ContentChunk.content_item_id == restored.id, ContentChunk.embed_status == "pending"
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
