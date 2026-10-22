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
from discourse_topics import (
    LAYOUT,
    POST_CHUNK_VERSION,
    PROCESSOR_VERSION,
    layout_topic,
    migrate_legacy_topic,
    sync_vector_payloads,
    topic_key,
)
from sqlalchemy import and_, create_engine, or_, select


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
    if {p["id"] for p in posts} != visible or len(visible) != len(posts):
        raise ValueError("Snapshot visible ID coverage is incomplete")
    root_post = next(p for p in posts if int(p["post_number"]) == 1)
    if len({int(p["post_number"]) for p in posts}) != len(posts):
        raise ValueError("Duplicate post numbers in snapshot")
    forum = source.config_json["forum"]
    decision = classify_topic(forum, topic, posts, payload.get("category_path", []), policy)
    title = str(topic.get("title") or f'Topic {topic["id"]}')
    base = source.base_url.rstrip("/")
    tags = [t["name"] if isinstance(t, dict) else str(t) for t in topic.get("tags") or []]
    tags = list(dict.fromkeys([forum, *tags]))
    root_text = post_text(root_post)
    by_number = {int(p["post_number"]): p for p in posts}
    item, merged = migrate_legacy_topic(session, source, topic["id"])
    created = item is None
    if created:
        item = ContentItem(
            source_site_id=source.id,
            raw_page_id=snapshot.id,
            crawl_run_id=run.id,
            item_type="thread",
            canonical_url=f'{base}/t/{topic["id"]}',
            cleaned_text="",
            content_hash=stable_hash(""),
            dedup_key=topic_key(base, topic["id"]),
            metadata_json={"forum": forum, "topic_id": topic["id"], "forum_item_layout": LAYOUT},
        )
        session.add(item)
        session.flush()
    # Copy nested state so SQLAlchemy sees the complete JSON assignment below.
    states = {key: dict(value) for key, value in item.metadata_json.get("posts", {}).items()}
    chunks = session.scalars(
        select(ContentChunk).where(ContentChunk.content_item_id == item.id)
    ).all()
    chunks_by_post = {}
    for chunk in chunks:
        chunks_by_post.setdefault(str(chunk.chunk_metadata_json["post_id"]), []).append(chunk)
    next_index = max((c.chunk_index for c in chunks), default=-1) + 1
    stats = {
        "posts_created": 0,
        "posts_updated": 0,
        "deduped": 0,
        "chunks_created": 0,
        "items_created": int(created),
        "items_merged": merged,
    }
    for post in posts:
        pid = int(post["id"])
        key = str(pid)
        number = int(post["post_number"])
        previous = states.get(key)
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
                    "processor": POST_CHUNK_VERSION,
                    "chunker": CHUNKER_VERSION,
                },
                ensure_ascii=False,
                sort_keys=True,
            )
        )
        changed = (
            previous is None
            or previous.get("processing_fingerprint") != fingerprint
            or previous.get("rag_eligible") != eligible
            or previous.get("source_visibility") != "visible"
        )
        author = get_author(session, source.id, post, base)
        states[key] = {
            **(previous or {}),
            "post_id": pid,
            "post_number": number,
            "reply_to_post_number": post.get("reply_to_post_number"),
            "source_updated_at": post.get("updated_at"),
            "source_version": post.get("version"),
            "accepted_answer": bool(post.get("accepted_answer")),
            "resolution_status": "accepted" if post.get("accepted_answer") else "unverified",
            "post_role": role["role"],
            "rag_eligible": eligible,
            "processing_fingerprint": fingerprint,
            "source_visibility": "visible",
            "author_id": str(author.id),
            "author_name": post.get("username", "unknown"),
            "published_at": (
                iso_to_dt(post["created_at"]).isoformat() if post.get("created_at") else None
            ),
            "canonical_url": f'{base}/t/{topic["id"]}/{number}',
            "raw_page_id": str(snapshot.id),
            "raw_text": raw,
            "cleaned_text": body,
        }
        if previous is None:
            stats["posts_created"] += 1
        elif changed:
            stats["posts_updated"] += 1
        else:
            stats["deduped"] += 1
        if changed:
            for old in chunks_by_post.get(key, []):
                if old.embed_status != "obsolete":
                    old.embed_status = "obsolete"
                    old.embed_error = "replaced or filtered forum content"
            if eligible:
                built = build_chunks(
                    item_type="thread" if number == 1 else "comment",
                    title=title,
                    cleaned_text=body,
                    summary_text=raw[:240],
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
                    row = ContentChunk(
                        content_item_id=item.id,
                        chunk_index=next_index,
                        char_start=chunk.start_char,
                        char_end=chunk.end_char,
                        display_text=chunk.display_text,
                        embed_text=embed,
                        token_count=max(1, len(embed.split())),
                        embed_status="pending",
                        chunk_metadata_json={
                            **chunk.chunk_metadata_json,
                            "ingest_source": "discourse_api_v3",
                            "topic_id": topic["id"],
                            "post_id": pid,
                            "post_chunk_index": chunk.chunk_index,
                            "post_char_start": chunk.start_char,
                            "post_char_end": chunk.end_char,
                            "processing_fingerprint": fingerprint,
                        },
                    )
                    session.add(row)
                    chunks.append(row)
                    next_index += 1
                    stats["chunks_created"] += 1
    # Only a complete visible stream can confirm that an individual post disappeared.
    for key, state in states.items():
        if int(key) not in visible:
            state["rag_eligible"] = False
            state["source_visibility"] = "missing"
            for chunk in chunks_by_post.get(key, []):
                if chunk.embed_status != "obsolete":
                    chunk.embed_status = "obsolete"
                    chunk.embed_error = "no longer visible in complete topic stream"
    item.raw_page_id = snapshot.id
    item.author_id = get_author(session, source.id, root_post, base).id
    item.title = title
    item.published_at = iso_to_dt(root_post.get("created_at"))
    item.language = source.default_language
    item.summary_text = root_text[:240]
    item.tags = tags
    item.structured_by = PROCESSOR_VERSION
    item.thread_root_id = item.id
    item.parent_item_id = None
    # Post-specific fields live in states/chunks; root fields are retained for
    # compatibility with older readers that only understand topic metadata.
    item.metadata_json = {
        **item.metadata_json,
        "forum": forum,
        "topic_id": topic["id"],
        "post_id": int(root_post["id"]),
        "post_number": 1,
        "category_id": topic.get("category_id"),
        "category_path": payload.get("category_path", []),
        "topic_filter": decision,
        "has_accepted_answer": topic.get("has_accepted_answer"),
        "source_visibility": "visible",
    }
    layout_topic(session, item, states, chunks)
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
        "items_created": 0,
        "items_merged": 0,
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
            for key in (
                "posts_created",
                "posts_updated",
                "deduped",
                "chunks_created",
                "items_created",
                "items_merged",
            ):
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

    chunks = session.scalars(
        select(ContentChunk)
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
    owners = {
        item.id: item
        for item in session.scalars(
            select(ContentItem).where(ContentItem.id.in_({c.content_item_id for c in chunks}))
        )
    }
    rows = [(c, owners[c.content_item_id]) for c in chunks]
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
                        "post_id": chunk.chunk_metadata_json.get("post_id"),
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
                    source = session.scalar(
                        select(SourceSite).where(
                            SourceSite.name
                            == f'Discourse: {forum} ({FORUMS[forum].split("://")[1]})'
                        )
                    )
                    payload_stats = sync_vector_payloads(
                        session, source.id, url=get_settings().qdrant_url, limit=args.embed_limit
                    )
                    result.update(payload_stats)
                    if payload_stats["payloads_failed"]:
                        result["status"] = "partial"
                        processing_run = session.get(CrawlRun, uuid.UUID(result["run_id"]))
                        processing_run.status = "partial"
                        processing_run.error_count += payload_stats["payloads_failed"]
                        processing_run.error_message = (
                            "Vector metadata update pending; retry processing"
                        )
                        session.commit()
                    if args.embed:
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
