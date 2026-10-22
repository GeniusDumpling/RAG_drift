#!/usr/bin/env python3
# ruff: noqa: E402
"""Process archived topics without re-fetching: filter, normalize, chunk; optional --embed.

Raw captures are committed by discourse_ingest.py independently of this stage.
--reprocess reapplies policy/chunking to the least recently processed topic snapshots.
--embed retries bounded pending vectors, even when there are no new topic snapshots.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import uuid
from datetime import timedelta
from pathlib import Path
from urllib.parse import quote

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "backend"))
sys.path.insert(0, str(ROOT))

from app.core.config import get_settings
from app.db.base import utcnow
from app.models.content import Author, ContentChunk, ContentItem, RawPage
from app.models.control import CrawlRun, SourceSite
from app.repositories.contents import stable_hash
from app.services.chunking import CHUNKER_VERSION, build_chunks
from discourse_common import ALIASES, FORUMS, canonical_forums, iso_to_dt
from discourse_filter import classify_post, classify_topic, load_policy, post_text
from discourse_storage import (
    ForumTopicTask,
    add_event,
    ensure_storage,
    forum_session,
    queue_summary,
    source_and_job,
)
from sqlalchemy import and_, create_engine, or_, select

PROCESSOR_VERSION = "discourse-v2.1"


def obsolete_chunks(session, item, reason):
    chunks = session.scalars(
        select(ContentChunk).where(ContentChunk.content_item_id == item.id)
    ).all()
    offset = max((c.chunk_index for c in chunks), default=-1) + 1
    for index, chunk in enumerate(c for c in chunks if c.embed_status != "obsolete"):
        # Preserve old vector references for asynchronous deletion. Free indices
        # for a new chunk generation without deleting provenance or duplicating IDs.
        chunk.chunk_index = offset + index
        chunk.embed_status = "obsolete"
        chunk.embed_error = reason
    session.flush()


def get_author(session, source_id, post, base):
    username = str(post.get("username") or "unknown")
    external_id = str(post.get("user_id") or username)
    author = session.scalar(
        select(Author)
        .where(
            Author.source_site_id == source_id,
            or_(Author.external_author_id == external_id, Author.handle == username),
        )
        .limit(1)
    )
    if author is None:
        author = Author(
            source_site_id=source_id,
            external_author_id=external_id,
            display_name=username,
            handle=username,
            profile_url=f'{base}/u/{quote(username, safe="")}',
            raw_json={"username": username},
        )
        session.add(author)
        session.flush()
    return author


def normalize_topic(session, source, snapshot, policy, run):
    payload = snapshot.raw_json
    if payload.get("complete") is not True:
        raise ValueError("Only complete topic snapshots may be normalized")
    topic = payload["topic"]
    posts = sorted(topic["post_stream"]["posts"], key=lambda p: int(p["post_number"]))
    visible = set(topic["post_stream"]["stream"])
    if {p["id"] for p in posts} != visible:
        raise ValueError("Snapshot visible ID coverage is incomplete")
    forum = source.config_json["forum"]
    decision = classify_topic(forum, topic, posts, payload.get("category_path", []), policy)
    title = str(topic.get("title") or f'Topic {topic["id"]}')
    base = source.base_url.rstrip("/")
    tags = [t["name"] if isinstance(t, dict) else str(t) for t in topic.get("tags") or []]
    tags = list(dict.fromkeys([forum, *tags]))
    root_post = next(p for p in posts if int(p["post_number"]) == 1)
    root_text = post_text(root_post)
    by_number = {int(p["post_number"]): p for p in posts}
    items = {}
    stats = {"posts_created": 0, "posts_updated": 0, "deduped": 0, "chunks_created": 0}
    for post in posts:
        pid = int(post["id"])
        number = int(post["post_number"])
        key = stable_hash(f"discourse:{base}:post:{pid}")
        candidates = session.scalars(
            select(ContentItem)
            .where(
                ContentItem.source_site_id == source.id,
                or_(
                    ContentItem.dedup_key == key,
                    ContentItem.metadata_json["post_id"].as_integer() == pid,
                ),
            )
            .order_by(ContentItem.created_at, ContentItem.id)
        ).all()
        item = next(
            (c for c in candidates if c.dedup_key == key), candidates[0] if candidates else None
        )
        for duplicate in candidates:
            if duplicate is not item:
                obsolete_chunks(session, duplicate, "superseded legacy duplicate")
                duplicate.metadata_json = {
                    **duplicate.metadata_json,
                    "superseded_by": str(item.id),
                    "rag_eligible": False,
                }
        role = classify_post(post)
        eligible = decision["decision"] == "keep" and role["indexable"]
        raw = post_text(post)
        body = "\n\n".join([f"Topic: {title}", f'Author: {post.get("username", "unknown")}', raw])
        parent_number = int(post.get("reply_to_post_number") or 1)
        parent = by_number.get(parent_number)
        context = root_text[:2000] if number != 1 else ""
        parent_text = (
            post_text(parent)[:1200] if parent and parent_number not in (1, number) else ""
        )
        fingerprint = stable_hash(
            json.dumps(
                {
                    "body": body,
                    "context": context,
                    "parent": parent_text,
                    "tags": tags,
                    "eligible": eligible,
                    "role": role,
                    "policy": policy["version"],
                    "processor": PROCESSOR_VERSION,
                    "chunker": CHUNKER_VERSION,
                },
                ensure_ascii=False,
                sort_keys=True,
            )
        )
        created = item is None
        if created:
            item = ContentItem(
                source_site_id=source.id,
                raw_page_id=snapshot.id,
                crawl_run_id=run.id,
                item_type="thread" if number == 1 else "comment",
                canonical_url=f'{base}/t/{topic["id"]}/{number}',
                cleaned_text=body,
                content_hash=stable_hash(raw),
                dedup_key=key,
                metadata_json={},
            )
            session.add(item)
            session.flush()
        changed = (
            item.metadata_json.get("processing_fingerprint") != fingerprint
            or item.metadata_json.get("rag_eligible") != eligible
            or item.metadata_json.get("source_visibility") != "visible"
        )
        item.raw_page_id = snapshot.id
        item.author_id = get_author(session, source.id, post, base).id
        item.item_type = "thread" if number == 1 else "comment"
        item.title = title if number == 1 else f"Reply {number} · {title}"
        item.canonical_url = f'{base}/t/{topic["id"]}/{number}'
        item.source_url = f"{base}/posts/{pid}.json"
        item.published_at = iso_to_dt(post.get("created_at"))
        item.language = source.default_language
        item.raw_text = raw
        item.cleaned_text = body
        item.summary_text = raw[:240]
        item.tags = tags
        item.structured_by = PROCESSOR_VERSION
        item.content_hash = stable_hash(raw)
        item.dedup_key = key
        item.metadata_json = {
            **item.metadata_json,
            "forum": forum,
            "topic_id": topic["id"],
            "post_id": pid,
            "post_number": number,
            "reply_to_post_number": post.get("reply_to_post_number"),
            "category_id": topic.get("category_id"),
            "category_path": payload.get("category_path", []),
            "source_updated_at": post.get("updated_at"),
            "source_version": post.get("version"),
            "accepted_answer": bool(post.get("accepted_answer")),
            "has_accepted_answer": topic.get("has_accepted_answer"),
            "resolution_status": "accepted" if post.get("accepted_answer") else "unverified",
            "topic_filter": decision,
            "post_role": role["role"],
            "rag_eligible": eligible,
            "processing_fingerprint": fingerprint,
            "source_visibility": "visible",
        }
        if created:
            stats["posts_created"] += 1
        elif changed:
            stats["posts_updated"] += 1
        else:
            stats["deduped"] += 1
        if changed:
            obsolete_chunks(session, item, "replaced or filtered forum content")
            if eligible:
                built = build_chunks(
                    item_type=item.item_type,
                    title=title,
                    cleaned_text=body,
                    summary_text=item.summary_text,
                    tags=tags,
                    thread_title=title,
                )
                for chunk in built:
                    embed = "\n\n".join(
                        p
                        for p in (
                            f"Question: {context}" if context else "",
                            f"Parent reply: {parent_text}" if parent_text else "",
                            chunk.embed_text,
                        )
                        if p
                    )
                    session.add(
                        ContentChunk(
                            content_item_id=item.id,
                            chunk_index=chunk.chunk_index,
                            char_start=chunk.start_char,
                            char_end=chunk.end_char,
                            display_text=chunk.display_text,
                            embed_text=embed,
                            token_count=max(1, len(embed.split())),
                            embed_status="pending",
                            chunk_metadata_json={
                                **chunk.chunk_metadata_json,
                                "ingest_source": "discourse_api_v2",
                                "topic_id": topic["id"],
                                "post_id": pid,
                                "processing_fingerprint": fingerprint,
                            },
                        )
                    )
                    stats["chunks_created"] += 1
        items[number] = item
    root = items[1]
    for number, item in items.items():
        item.thread_root_id = root.id
        item.parent_item_id = (
            None
            if number == 1
            else items.get(int(by_number[number].get("reply_to_post_number") or 1), root).id
        )
    # Confirmed stream removal within a successfully complete visible topic.
    for old in session.scalars(
        select(ContentItem).where(
            ContentItem.source_site_id == source.id,
            ContentItem.metadata_json["topic_id"].as_integer() == int(topic["id"]),
        )
    ).all():
        if old.metadata_json.get("post_id") not in visible:
            obsolete_chunks(session, old, "no longer visible in complete topic stream")
            old.metadata_json = {
                **old.metadata_json,
                "rag_eligible": False,
                "source_visibility": "missing",
            }
    session.flush()
    return {**stats, "decision": decision["decision"]}


def process_forum(session, forum, args, policy):
    source, job = source_and_job(session, forum, FORUMS[forum], "en")
    for abandoned in session.scalars(
        select(CrawlRun).where(CrawlRun.crawl_job_id == job.id, CrawlRun.status == "running")
    ).all():
        abandoned.status = "failed"
        abandoned.finished_at = utcnow()
        abandoned.error_count = max(1, abandoned.error_count)
        abandoned.error_message = "Previous pipeline interrupted; durable queue retained"
    run = CrawlRun(
        source_site_id=source.id,
        crawl_job_id=job.id,
        trigger_type="manual",
        execution_mode="discourse_process",
        status="running",
        started_at=utcnow(),
        config_snapshot_json={"policy": policy["version"], "reprocess": args.reprocess},
    )
    session.add(run)
    session.flush()
    session.commit()
    query = select(ForumTopicTask).where(
        ForumTopicTask.source_site_id == source.id,
        ForumTopicTask.snapshot_id.is_not(None),
    )
    if args.reprocess:
        query = query.order_by(ForumTopicTask.last_processed_at.asc().nullsfirst())
    else:
        query = query.where(
            ForumTopicTask.process_status.in_(["pending", "retry"]),
            ForumTopicTask.next_process_at <= utcnow(),
        ).order_by(ForumTopicTask.priority, ForumTopicTask.next_process_at)
    tasks = session.scalars(query.limit(args.limit)).all()
    stats = {
        "topics_processed": 0,
        "posts_created": 0,
        "posts_updated": 0,
        "deduped": 0,
        "chunks_created": 0,
        "keep": 0,
        "review": 0,
        "reject": 0,
    }
    errors = []
    for task in tasks:
        try:
            snapshot = session.get(RawPage, task.snapshot_id)
            outcome = normalize_topic(session, source, snapshot, policy, run)
            snapshot.parse_status = "parsed"
            snapshot.parse_error = None
            task.process_status = "done"
            task.process_error = None
            task.process_attempts = 0
            task.last_processed_at = utcnow()
            session.commit()
            for key in ("posts_created", "posts_updated", "deduped", "chunks_created"):
                stats[key] += outcome[key]
            stats[outcome["decision"]] += 1
            stats["topics_processed"] += 1
        except Exception as exc:
            session.rollback()
            task.process_status = "retry"
            task.process_attempts += 1
            task.process_error = str(exc)[:2000]
            task.next_process_at = utcnow() + timedelta(
                seconds=min(3600, 30 * 2 ** min(task.process_attempts, 7))
            )
            snapshot = session.get(RawPage, task.snapshot_id)
            if snapshot:
                snapshot.parse_status = "failed"
                snapshot.parse_error = task.process_error
            add_event(
                session, run.id, "parse", "processing_failed", task.process_error, level="error"
            )
            session.commit()
            errors.append({"topic_id": task.topic_id, "error": task.process_error})
    run.status = "success" if not errors else ("partial" if stats["topics_processed"] else "failed")
    run.finished_at = utcnow()
    run.parsed_count = stats["posts_created"] + stats["posts_updated"]
    run.extracted_count = run.parsed_count
    run.deduped_count = stats["deduped"]
    run.chunked_count = stats["chunks_created"]
    run.error_count = len(errors)
    run.error_message = json.dumps(errors) if errors else None
    add_event(session, run.id, "finalize", "run_finished", run.status, counters=stats)
    session.commit()
    return {
        "forum": forum,
        "run_id": str(run.id),
        "status": run.status,
        **stats,
        **queue_summary(session, source.id),
        "errors": errors,
    }


def embed_pending(session, source_id, *, limit, indexer=None):
    """Bounded vector work, independent of whether normalization had new tasks."""
    from app.services.embedding_reindex import _build_payload, _mark_failed, _mark_success
    from app.services.embeddings import build_embedding_service
    from app.services.retrieval import QdrantIndexer

    rows = session.execute(
        select(ContentChunk, ContentItem)
        .join(ContentItem, ContentItem.id == ContentChunk.content_item_id)
        .where(
            ContentItem.source_site_id == source_id,
            or_(
                ContentChunk.embed_status.in_(["pending", "failed"]),
                and_(
                    ContentChunk.embed_status == "obsolete",
                    or_(
                        ContentChunk.vector_point_id.is_not(None),
                        ContentChunk.qdrant_point_id.is_not(None),
                    ),
                ),
            ),
        )
        .order_by(ContentChunk.id)
        .limit(limit)
    ).all()
    # Do not initialize a paid model or remote collection if there is no actual work.
    if not rows:
        return {"embedded": 0, "vectors_deleted": 0, "vector_failed": 0}
    settings = get_settings()
    session.commit()
    stats = {"embedded": 0, "vectors_deleted": 0, "vector_failed": 0}
    try:
        embedding = indexer.embedding if indexer is not None else build_embedding_service(settings)
        indexer = indexer or QdrantIndexer(
            url=settings.qdrant_url, collection=settings.qdrant_collection, embedding=embedding
        )
        indexer.ensure_collection()
    except Exception as exc:
        for chunk, _item in rows:
            if chunk.embed_status == "obsolete":
                chunk.embed_error = str(exc)[:2000]
            else:
                _mark_failed(
                    content_chunk=chunk,
                    backend_name=getattr(indexer, "backend_name", "qdrant"),
                    error_message=str(exc),
                )
        session.commit()
        return {**stats, "vector_failed": len(rows)}
    store_id = hashlib.sha256(indexer.url.encode()).hexdigest()
    for chunk, item in rows:
        try:
            if chunk.embed_status == "obsolete":
                metadata = chunk.chunk_metadata_json
                if metadata.get("vector_store_id", store_id) != store_id:
                    raise ValueError("Obsolete vector belongs to another store; cleanup deferred")
                old_collection = metadata.get("vector_collection", indexer.collection)
                target = (
                    indexer
                    if old_collection == indexer.collection
                    else QdrantIndexer(
                        url=indexer.url, collection=old_collection, embedding=embedding
                    )
                )
                target.delete_chunk(point_id=chunk.vector_point_id or chunk.qdrant_point_id)
                chunk.vector_point_id = chunk.qdrant_point_id = None
                chunk.vector_backend = None
                chunk.embedded_at = None
                stats["vectors_deleted"] += 1
            else:
                if not item.metadata_json.get("rag_eligible", False):
                    chunk.embed_status = "obsolete"
                    session.commit()
                    continue
                point_id = indexer.upsert_chunk(
                    chunk_id=chunk.id,
                    embed_text=chunk.embed_text,
                    payload={
                        **_build_payload(content_chunk=chunk, content_item=item),
                        "topic_id": item.metadata_json.get("topic_id"),
                        "post_id": item.metadata_json.get("post_id"),
                        "thread_root_id": str(item.thread_root_id) if item.thread_root_id else None,
                        "parent_item_id": str(item.parent_item_id) if item.parent_item_id else None,
                    },
                )
                _mark_success(
                    content_chunk=chunk,
                    point_id=point_id,
                    backend_name=indexer.backend_name,
                    embedding=embedding,
                    collection=indexer.collection,
                    vector_store_id=store_id,
                )
                stats["embedded"] += 1
            session.commit()
        except Exception as exc:
            session.rollback()
            if chunk.embed_status != "obsolete":
                _mark_failed(
                    content_chunk=chunk, backend_name=indexer.backend_name, error_message=str(exc)
                )
            else:
                chunk.embed_error = str(exc)[:2000]
            session.commit()
            stats["vector_failed"] += 1
    return stats


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--forums", nargs="+", choices=[*FORUMS, *ALIASES], default=list(FORUMS))
    parser.add_argument("--limit", type=int, default=20, help="Topics per forum")
    parser.add_argument("--policy", help="Versioned policy JSON file")
    parser.add_argument("--reprocess", action="store_true")
    parser.add_argument(
        "--embed", action="store_true", help="Explicitly enable embedding API calls"
    )
    parser.add_argument("--embed-limit", type=int, default=200)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    args.forums = canonical_forums(args.forums)
    if args.limit < 1 or args.embed_limit < 1:
        parser.error("limits must be positive")
    return args


def process(args, *, engine=None):
    owned = engine is None
    engine = engine or create_engine(
        get_settings().sync_database_url, pool_pre_ping=True, connect_args={"connect_timeout": 10}
    )
    try:
        ensure_storage(engine)
        policy = load_policy(args.policy)
        results = []
        for forum in args.forums:
            try:
                with forum_session(engine, forum) as session:
                    if session is None:
                        results.append({"forum": forum, "status": "skipped"})
                        continue
                    result = process_forum(session, forum, args, policy)
                    if args.embed:
                        source = session.scalar(
                            select(SourceSite).where(
                                SourceSite.name
                                == f'Discourse: {forum} ({FORUMS[forum].split("://")[1]})'
                            )
                        )
                        vector_stats = embed_pending(session, source.id, limit=args.embed_limit)
                        result.update(vector_stats)
                        processing_run = session.get(CrawlRun, uuid.UUID(result["run_id"]))
                        processing_run.embedded_count = vector_stats["embedded"]
                        processing_run.error_count += vector_stats["vector_failed"]
                        if vector_stats["vector_failed"]:
                            result["status"] = "partial"
                            processing_run.status = "partial"
                            processing_run.error_message = (
                                "Vector processing failed; retry with --embed"
                            )
                        add_event(
                            session,
                            processing_run.id,
                            "embed",
                            "embedding_finished",
                            result["status"],
                            counters=vector_stats,
                        )
                        session.commit()
                    results.append(result)
            except Exception as exc:
                results.append({"forum": forum, "status": "failed", "error": str(exc)[:2000]})
        return {
            "status": "success"
            if all(r["status"] in ("success", "skipped") for r in results)
            else "failed",
            "results": results,
        }
    finally:
        if owned:
            engine.dispose()


def main(argv=None):
    args = parse_args(argv)
    try:
        result = process(args)
    except Exception as exc:
        result = {"status": "failed", "error": str(exc)[:2000]}
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["status"] == "success" else 1


if __name__ == "__main__":
    sys.exit(main())
