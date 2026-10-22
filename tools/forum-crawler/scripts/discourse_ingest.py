#!/usr/bin/env python3
# ruff: noqa: E402
"""Archive complete public Discourse topics in PostgreSQL; processing runs separately.

Default: incremental public post stream (new topics AND replies).
--mode backfill: resumable history scan with an independent cursor.
--forums px4 --topic-id 49575: archive specific topics.
--query 'GPS spoofing': legacy manual search discovery, never used by the timer.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "backend"))
sys.path.insert(0, str(ROOT))

from app.core.config import get_settings
from app.db.base import utcnow
from app.models.content import RawPage
from app.models.control import CrawlRun
from discourse_common import (
    ALIASES,
    FORUMS,
    DiscourseClient,
    DiscourseError,
    canonical_forums,
    fetch_topic,
    iso_to_dt,
)
from discourse_storage import (
    TOPIC_PROFILE,
    ForumSyncState,
    ForumTopicTask,
    add_event,
    archive_response,
    enqueue,
    ensure_storage,
    forum_session,
    queue_summary,
    source_and_job,
)
from sqlalchemy import create_engine, select


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--forums", nargs="+", choices=[*FORUMS, *ALIASES], default=list(FORUMS))
    parser.add_argument("--forum", choices=[*FORUMS, *ALIASES])
    parser.add_argument(
        "--mode", choices=["incremental", "backfill", "search"], default="incremental"
    )
    parser.add_argument("--query", help="Legacy manual keyword discovery")
    parser.add_argument("--topic-id", type=int, nargs="+")
    parser.add_argument("--max-pages", type=int, default=10)
    parser.add_argument("--max-topics", "--max-results", dest="max_topics", type=int, default=20)
    parser.add_argument("--category-id", type=int, help="Manual search category")
    parser.add_argument("--since", help="Backfill created-at cutoff, e.g. 2024-10-21 (UTC)")
    parser.add_argument("--request-delay", type=float, default=1.0)
    parser.add_argument("--timeout", type=float, default=30)
    parser.add_argument("--retries", type=int, default=3)
    parser.add_argument("--refresh-hours", type=float, default=24)
    parser.add_argument("--refresh-limit", type=int, default=3)
    parser.add_argument("--language", default="en")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    args.forums = canonical_forums([args.forum] if args.forum else args.forums)
    if args.max_pages < 1 or args.max_topics < 1 or args.refresh_limit < 0:
        parser.error("max-pages/max-topics must be positive; refresh-limit must be nonnegative")
    if args.request_delay < 0 or args.timeout <= 0 or args.retries < 0 or args.refresh_hours <= 0:
        parser.error("Invalid delay, timeout, retries or refresh interval")
    if args.topic_id and (any(tid < 1 for tid in args.topic_id) or len(args.forums) != 1):
        parser.error("Explicit topic IDs must be positive and require exactly one forum")
    if args.query:
        if not args.query.strip():
            parser.error("query must not be empty")
        args.mode = "search"
    if args.mode == "search" and not args.query:
        parser.error("search mode requires --query")
    if args.since and (args.mode != "backfill" or iso_to_dt(args.since) is None):
        parser.error("since requires backfill mode and an ISO date")
    return args


def discover_stream(session, source, state, client, run, *, mode, max_pages, since=None):
    """Commit queued topics and each discovery checkpoint in one transaction."""
    data = dict(state.state_json)
    if mode == "backfill" and data.get("backfill_complete"):
        return {"discovered_posts": 0, "discovery_complete": True}
    if mode == "backfill":
        cutoff = iso_to_dt(since)
        if "backfill_since" in data and data["backfill_since"] != since:
            raise ValueError("Backfill cutoff already checkpointed; keep the original --since")
        data["backfill_since"] = since
        before = data.get("backfill_before")
        floor = -1
    else:
        cutoff = None
        floor = int(data.get("watermark", 0))
        before = data.get("scan_before")
    discovered = 0
    complete = False
    for _ in range(max_pages):
        path = "/posts.json" + (f"?before={before}" if before else "")
        session.commit()
        payload = client.get(path)
        posts = payload.get("latest_posts")
        if not isinstance(posts, list) or any(
            not isinstance(p, dict) or not p.get("id") or not p.get("topic_id") for p in posts
        ):
            raise DiscourseError("Invalid public post page; cursor unchanged")
        ids = [int(p["id"]) for p in posts]
        if ids != sorted(set(ids), reverse=True) or (before and any(pid >= before for pid in ids)):
            raise DiscourseError("Public post page did not advance; cursor unchanged")
        archive_response(session, source, run, path, payload)
        selected = [p for p in posts if int(p["id"]) > floor]
        if cutoff:
            selected = [p for p in selected if (iso_to_dt(p.get("created_at")) or cutoff) >= cutoff]
        enqueue(
            session,
            source.id,
            [int(p["topic_id"]) for p in selected],
            priority=10 if mode == "backfill" else 0,
        )
        discovered += len(selected)
        run.discovered_count = (run.discovered_count or 0) + len(selected)
        complete = not posts or (mode == "incremental" and min(ids) <= floor)
        if mode == "backfill":
            # Imported posts can have backdated created_at: cutoff filters, never
            # stops an ID scan early. Explicit --topic-id also retrieves old roots.
            data["backfill_before"] = min(ids) if ids else before
            data["backfill_complete"] = complete
        else:
            if ids and not data.get("scan_head"):
                data["scan_head"] = max(ids)
            # Bootstrap one recent page. History must not stall new-post discovery.
            complete = complete or "watermark" not in data
            if complete:
                data["watermark"] = data.pop("scan_head", floor)
                data.pop("scan_before", None)
            else:
                data["scan_before"] = min(ids)
        state.state_json = dict(data)
        state.updated_at = utcnow()
        session.commit()
        before = min(ids) if ids else before
        if complete:
            break
    return {"discovered_posts": discovered, "discovery_complete": complete}


def refresh_topics(session, source_id, *, hours, limit):
    if not limit:
        return
    tasks = session.scalars(
        select(ForumTopicTask)
        .where(
            ForumTopicTask.source_site_id == source_id,
            ForumTopicTask.capture_status == "captured",
            ForumTopicTask.last_captured_at < utcnow() - timedelta(hours=hours),
        )
        .order_by(ForumTopicTask.last_captured_at)
        .limit(limit)
    ).all()
    for task in tasks:
        task.capture_status = "pending"
        task.next_attempt_at = utcnow()
        task.priority = 20
    session.commit()


def capture_tasks(session, source, run, client, categories, *, limit):
    stats = {"topics_captured": 0, "posts_archived": 0, "raw_pages_created": 0, "failed": 0}
    errors = []
    tasks = session.scalars(
        select(ForumTopicTask)
        .where(
            ForumTopicTask.source_site_id == source.id,
            ForumTopicTask.capture_status.in_(["pending", "retry"]),
            ForumTopicTask.next_attempt_at <= utcnow(),
        )
        .order_by(ForumTopicTask.priority, ForumTopicTask.next_attempt_at, ForumTopicTask.queued_at)
        .limit(limit)
    ).all()
    session.commit()
    for task in tasks:

        def archive(path, payload):
            archive_response(session, source, run, path, payload)
            session.commit()  # Raw pages survive an incomplete topic or later parsing failure.
            stats["raw_pages_created"] += 1

        try:
            topic = fetch_topic(client, task.topic_id, archive=archive)
            category_map = {int(c["id"]): c for c in categories}
            category_path = []
            category = category_map.get(int(topic.get("category_id") or 0))
            seen = set()
            while category and category["id"] not in seen:
                seen.add(category["id"])
                category_path.insert(
                    0, {k: category.get(k) for k in ("id", "name", "slug", "parent_category_id")}
                )
                category = category_map.get(category.get("parent_category_id"))
            snapshot = archive_response(
                session,
                source,
                run,
                f"/t/{task.topic_id}.json",
                {"topic": topic, "category_path": category_path, "complete": True},
                profile=TOPIC_PROFILE,
            )
            if task.snapshot_id:
                previous = session.get(RawPage, task.snapshot_id)
                if previous and previous.parse_status in ("pending", "failed"):
                    previous.parse_status = "superseded"
            task.snapshot_id = snapshot.id
            task.capture_status = "captured"
            task.process_status = "pending"
            task.attempts = 0
            task.last_error = None
            task.last_captured_at = utcnow()
            task.next_process_at = utcnow()
            task.process_attempts = 0
            task.process_error = None
            session.commit()
            stats["topics_captured"] += 1
            stats["posts_archived"] += len(topic["post_stream"]["posts"])
            stats["raw_pages_created"] += 1
        except Exception as exc:
            session.rollback()
            task.attempts += 1
            task.capture_status = "retry"
            task.last_error = str(exc)[:2000]
            wait = max(getattr(exc, "retry_after", 0), min(3600, 30 * 2 ** min(task.attempts, 7)))
            if getattr(exc, "status", None) in (403, 404, 410):
                wait = max(wait, min(604800, 3600 * 2 ** min(task.attempts, 8)))
            task.next_attempt_at = utcnow() + timedelta(seconds=wait)
            add_event(session, run.id, "fetch", "topic_failed", task.last_error, level="error")
            session.commit()
            stats["failed"] += 1
            errors.append({"topic_id": task.topic_id, "error": task.last_error})
    return stats, errors


def collect_forum(session, forum, args, client=None):
    base = FORUMS[forum]
    source, job = source_and_job(session, forum, base, args.language)
    # Exclusive forum lock means a prior running record belongs to an interrupted run.
    for abandoned in session.scalars(
        select(CrawlRun).where(CrawlRun.crawl_job_id == job.id, CrawlRun.status == "running")
    ).all():
        abandoned.status = "failed"
        abandoned.finished_at = utcnow()
        abandoned.error_message = "Previous collector interrupted; durable queue retained"
        abandoned.error_count = max(1, abandoned.error_count)
    run = CrawlRun(
        source_site_id=source.id,
        crawl_job_id=job.id,
        trigger_type="manual",
        execution_mode="discourse_capture",
        seed_url=base,
        status="running",
        started_at=utcnow(),
        config_snapshot_json={
            "mode": args.mode,
            "max_pages": args.max_pages,
            "max_topics": args.max_topics,
            "query": args.query,
        },
    )
    session.add(run)
    state = session.get(ForumSyncState, forum)
    if state is None:
        state = ForumSyncState(forum=forum, source_site_id=source.id, state_json={})
        session.add(state)
    session.flush()
    add_event(session, run.id, "discover", "run_started", f"Capture {forum}: {args.mode}")
    session.commit()
    client = client or DiscourseClient(
        base, delay=args.request_delay, timeout=args.timeout, retries=args.retries
    )
    errors = []
    discovery = {"discovered_posts": 0, "discovery_complete": False}
    stats = {"topics_captured": 0, "posts_archived": 0, "raw_pages_created": 0, "failed": 0}
    try:
        data = dict(state.state_json)
        categories = data.get("categories", [])
        fetched_at = iso_to_dt(data.get("categories_fetched_at"))
        if not fetched_at or fetched_at < utcnow() - timedelta(days=1):
            session.commit()
            try:
                site = client.get("/site.json")
                categories = site["categories"]
                if not isinstance(categories, list):
                    raise DiscourseError("Invalid category tree")
                archive_response(session, source, run, "/site.json", site)
                state.state_json = {
                    **data,
                    "categories": categories,
                    "categories_fetched_at": utcnow().isoformat(),
                }
                session.commit()
            except Exception as exc:
                session.rollback()
                errors.append({"stage": "categories", "error": str(exc)})
        try:
            if args.topic_id:
                enqueue(session, source.id, args.topic_id)
                session.commit()
                discovery = {"discovered_posts": 0, "discovery_complete": True}
            elif args.mode == "search":
                from discourse_search import search_forum

                found = search_forum(
                    forum,
                    args.query,
                    base=base,
                    max_results=args.max_topics,
                    category_id=args.category_id,
                    client=client,
                    max_pages=args.max_pages,
                )
                enqueue(session, source.id, [p["topic_id"] for p in found["items"]])
                session.commit()
                discovery = {
                    "discovered_posts": found["count"],
                    "discovery_complete": not found["has_more"],
                }
            else:
                discovery = discover_stream(
                    session,
                    source,
                    state,
                    client,
                    run,
                    mode=args.mode,
                    max_pages=args.max_pages,
                    since=args.since,
                )
        except Exception as exc:
            session.rollback()
            errors.append({"stage": "discovery", "error": str(exc)})
        refresh_topics(session, source.id, hours=args.refresh_hours, limit=args.refresh_limit)
        stats, failures = capture_tasks(
            session, source, run, client, categories, limit=args.max_topics
        )
        errors.extend(failures)
    except Exception as exc:
        session.rollback()
        errors.append({"stage": "capture", "error": str(exc)})
    run.status = "success" if not errors else ("partial" if stats["topics_captured"] else "failed")
    run.finished_at = utcnow()
    run.discovered_count = max(run.discovered_count, discovery["discovered_posts"])
    run.fetched_count = stats["posts_archived"]
    run.error_count = len(errors)
    run.error_message = json.dumps(errors, ensure_ascii=False) if errors else None
    job.last_run_at = run.finished_at
    add_event(session, run.id, "finalize", "run_finished", run.status, counters=stats)
    session.commit()
    return {
        "forum": forum,
        "run_id": str(run.id),
        "status": run.status,
        **discovery,
        **stats,
        **queue_summary(session, source.id),
        "watermark": state.state_json.get("watermark"),
        "scan_before": state.state_json.get("scan_before"),
        "backfill_before": state.state_json.get("backfill_before"),
        "errors": errors,
    }


def ingest(args, *, engine=None):
    owned = engine is None
    engine = engine or create_engine(
        get_settings().sync_database_url, pool_pre_ping=True, connect_args={"connect_timeout": 10}
    )
    results = []
    try:
        ensure_storage(engine)
        for forum in args.forums:
            try:
                with forum_session(engine, forum) as session:
                    results.append(
                        collect_forum(session, forum, args)
                        if session is not None
                        else {"forum": forum, "status": "skipped", "reason": "already running"}
                    )
            except Exception as exc:
                results.append({"forum": forum, "status": "failed", "error": str(exc)[:2000]})
        statuses = {r["status"] for r in results}
        status = "success" if statuses <= {"success", "skipped"} else "failed"
        if status == "failed" and ("success" in statuses or "partial" in statuses):
            status = "partial"
        return {"status": status, "results": results}
    finally:
        if owned:
            engine.dispose()


def main(argv=None):
    args = parse_args(argv)
    try:
        result = ingest(args)
    except Exception as exc:
        result = {"status": "failed", "error": str(exc)[:2000]}
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["status"] == "success" else 1


if __name__ == "__main__":
    sys.exit(main())
