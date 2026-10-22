"""Collector-owned durable state; existing application tables hold raw/content/chunks."""
# ruff: noqa: E402

from __future__ import annotations

import hashlib
import json
import sys
import uuid
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
for path in (ROOT, ROOT / "backend"):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from app.db.base import Base, utcnow
from app.models.content import RawPage
from app.models.control import CrawlJob, CrawlRunEvent, SourceSite
from app.repositories.contents import stable_hash
from sqlalchemy import (
    JSON,
    DateTime,
    ForeignKey,
    Integer,
    MetaData,
    String,
    Text,
    func,
    select,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column

RAW_PROFILE = "discourse_response_v2"
TOPIC_PROFILE = "discourse_topic_v2"
JSON_TYPE = JSON().with_variant(JSONB(), "postgresql")


class CollectorBase(DeclarativeBase):
    # Keep script-owned tables out of the application's Alembic/model registry.
    metadata = MetaData(naming_convention=Base.metadata.naming_convention)


class ForumSyncState(CollectorBase):
    __tablename__ = "forum_sync_states"

    forum: Mapped[str] = mapped_column(String(40), primary_key=True)
    source_site_id: Mapped[uuid.UUID] = mapped_column(ForeignKey(SourceSite.__table__.c.id))
    state_json: Mapped[dict] = mapped_column(JSON_TYPE, default=dict)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class ForumTopicTask(CollectorBase):
    __tablename__ = "forum_topic_tasks"

    source_site_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey(SourceSite.__table__.c.id), primary_key=True
    )
    topic_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    priority: Mapped[int] = mapped_column(Integer, default=10, index=True)
    capture_status: Mapped[str] = mapped_column(String(30), default="pending", index=True)
    process_status: Mapped[str] = mapped_column(String(30), default="waiting", index=True)
    snapshot_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey(RawPage.__table__.c.id))
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    process_attempts: Mapped[int] = mapped_column(Integer, default=0)
    next_attempt_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    next_process_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    last_captured_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str | None] = mapped_column(Text)
    process_error: Mapped[str | None] = mapped_column(Text)
    queued_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


def ensure_storage(engine) -> None:
    # Only these auxiliary tables are created; application schema remains Alembic-owned.
    with engine.begin() as connection:
        if connection.dialect.name == "postgresql":
            connection.execute(text("select pg_advisory_xact_lock(713192104221)"))
        CollectorBase.metadata.create_all(
            connection, tables=[ForumSyncState.__table__, ForumTopicTask.__table__]
        )


@contextmanager
def forum_session(engine, forum: str):
    """A dedicated connection retains the advisory lock across short transactions."""
    with engine.connect() as connection:
        key = int.from_bytes(
            hashlib.sha256(f"forum-v2:{forum}".encode()).digest()[:8], "big", signed=True
        )
        locked = connection.dialect.name != "postgresql" or connection.scalar(
            text("select pg_try_advisory_lock(:key)"), {"key": key}
        )
        connection.commit()
        if not locked:
            yield None
            return
        try:
            with Session(connection, expire_on_commit=False) as session:
                yield session
        finally:
            if connection.dialect.name == "postgresql":
                connection.rollback()
                connection.execute(text("select pg_advisory_unlock(:key)"), {"key": key})
                connection.commit()


def source_and_job(session: Session, forum: str, base: str, language: str):
    name = f"Discourse: {forum} ({base.split('://', 1)[1]})"
    source = session.scalar(select(SourceSite).where(SourceSite.name == name).limit(1))
    if source is None:
        source = SourceSite(
            name=name,
            site_type="forum",
            base_url=base,
            allowed_domains=[base.split("://", 1)[1]],
            fetch_mode="http_api",
            default_language=language,
            active=True,
            config_json={"kind": "discourse", "forum": forum, "base": base},
        )
        session.add(source)
        session.flush()
    job_name = f"Discourse {forum} 主题与回复同步"
    job = session.scalar(
        select(CrawlJob)
        .where(CrawlJob.source_site_id == source.id, CrawlJob.name == job_name)
        .limit(1)
    )
    if job is None:
        job = CrawlJob(
            source_site_id=source.id,
            name=job_name,
            trigger_mode="manual",
            enabled=True,
            parser_profile=TOPIC_PROFILE,
            max_pages=10,
            seed_config_json={},
            agent_policy_json={"extraction_mode": "deterministic_api_mapping"},
        )
        session.add(job)
        session.flush()
    return source, job


def enqueue(session: Session, source_id, topic_ids: list[int], *, priority: int = 0) -> None:
    for topic_id in set(topic_ids):
        if topic_id <= 0:
            raise ValueError("Topic IDs must be positive")
        task = session.get(ForumTopicTask, (source_id, topic_id))
        if task is None:
            session.add(
                ForumTopicTask(source_site_id=source_id, topic_id=topic_id, priority=priority)
            )
        elif task.capture_status != "pending":
            # Discovery must not cancel a server's Retry-After/backoff.
            if task.capture_status != "retry":
                task.capture_status = "pending"
                task.next_attempt_at = utcnow()
                task.priority = priority
        else:
            task.priority = min(task.priority, priority)


def archive_response(session, source, run, path, payload, *, profile=RAW_PROFILE):
    serialized = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    raw = RawPage(
        source_site_id=source.id,
        crawl_run_id=run.id,
        requested_url=source.base_url + path,
        final_url=source.base_url + path,
        http_status=200,
        content_type="application/json",
        response_headers_json={},
        raw_json=payload,
        raw_html=None,
        raw_text=None,
        fetched_at=utcnow(),
        parser_profile=profile,
        parse_status="pending" if profile == TOPIC_PROFILE else "archived",
        extraction_method="discourse_api",
        body_hash=stable_hash(serialized),
    )
    session.add(raw)
    session.flush()
    return raw


def add_event(session, run_id, stage, event_type, message, *, level="info", counters=None):
    session.add(
        CrawlRunEvent(
            crawl_run_id=run_id,
            stage=stage,
            level=level,
            event_type=event_type,
            message=message,
            counters_json=counters or {},
            agent_trace_json={},
        )
    )


def queue_summary(session, source_id) -> dict[str, int]:
    tasks = session.execute(
        select(ForumTopicTask.capture_status, ForumTopicTask.process_status, func.count())
        .where(ForumTopicTask.source_site_id == source_id)
        .group_by(ForumTopicTask.capture_status, ForumTopicTask.process_status)
    ).all()
    return {
        "capture_pending": sum(n for c, _, n in tasks if c in ("pending", "retry")),
        "process_pending": sum(n for _, p, n in tasks if p in ("pending", "retry")),
    }
