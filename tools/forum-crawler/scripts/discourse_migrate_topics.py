#!/usr/bin/env python3
# ruff: noqa: E402
"""Migrate archived ArduPilot/PX4 post items into one item per topic.

Default is read-only preview. --apply writes a compressed backup before each
forum transaction, then retries durable metadata-only vector updates. It never
fetches a forum or calls an embedding model. Keep old collectors stopped until
the migration completes; both forum advisory locks are held throughout.
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import sys
from contextlib import ExitStack
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "backend"))
sys.path.insert(0, str(ROOT))

from app.core.config import get_settings
from app.models.content import ContentChunk, ContentItem
from app.models.control import SourceSite
from discourse_common import ALIASES, FORUMS, canonical_forums
from discourse_storage import forum_session
from discourse_topics import LAYOUT, migrate_legacy_topic, sync_vector_payloads
from sqlalchemy import create_engine, select


def forum_rows(session, source_id):
    items = session.scalars(
        select(ContentItem).where(ContentItem.source_site_id == source_id).order_by(ContentItem.id)
    ).all()
    chunks = session.scalars(
        select(ContentChunk)
        .join(ContentItem, ContentItem.id == ContentChunk.content_item_id)
        .where(ContentItem.source_site_id == source_id)
        .order_by(ContentChunk.id)
    ).all()
    return items, chunks


def chunk_digest(chunks):
    """Fields that this ownership-only migration must never modify."""
    digest = hashlib.sha256()
    for c in sorted(chunks, key=lambda c: str(c.id)):
        digest.update(
            json.dumps(
                [
                    str(c.id),
                    c.display_text,
                    c.embed_text,
                    c.embed_status,
                    c.embed_error,
                    c.vector_backend,
                    c.vector_point_id,
                    c.qdrant_point_id,
                    str(c.embedded_at),
                    c.token_count,
                ],
                ensure_ascii=False,
            ).encode()
        )
    return digest.hexdigest()


def backup_forum(session, source, items, chunks, directory, *, url, store_url, client=None):
    """Back up relational rows and the exact point payloads/vectors before mutation."""
    from qdrant_client import QdrantClient

    directory.mkdir(parents=True, exist_ok=True)
    target = directory / f'{source.config_json["forum"]}.before.json.gz'
    identity = hashlib.sha256(store_url.encode()).hexdigest()
    grouped = {}
    for c in chunks:
        if not (c.vector_point_id or c.qdrant_point_id):
            continue
        meta = c.chunk_metadata_json
        if c.vector_backend != "qdrant" or meta.get("vector_store_id") != identity:
            raise ValueError(f"Vector store identity mismatch for chunk {c.id}")
        collection = meta.get("vector_collection")
        if not collection:
            raise ValueError(f"Missing vector collection for chunk {c.id}")
        grouped.setdefault(collection, []).append(c)
    client = client or (QdrantClient(url=url, timeout=30) if grouped else None)
    vectors = []
    for collection, group in grouped.items():
        for start in range(0, len(group), 100):
            batch = group[start : start + 100]
            points = client.retrieve(
                collection_name=collection,
                ids=[c.vector_point_id or c.qdrant_point_id for c in batch],
                with_payload=True,
                with_vectors=True,
            )
            by_id = {str(p.id): p for p in points}
            for c in batch:
                pid = c.vector_point_id or c.qdrant_point_id
                point = by_id.get(str(pid))
                if point is None:
                    raise ValueError(f"Missing vector point {pid}; reconcile before migrating")
                if point.payload.get("chunk_id") != str(c.id):
                    raise ValueError(f"Mismatched vector point {pid}")
                vectors.append({"collection": collection, **point.model_dump()})

    def row(item):
        return {column.name: getattr(item, column.key) for column in item.__table__.columns}

    payload = {
        "created_at": datetime.now(UTC).isoformat(),
        "source_site_id": str(source.id),
        "items": [row(i) for i in items],
        "chunks": [row(c) for c in chunks],
        "vectors": vectors,
        "chunk_digest": chunk_digest(chunks),
    }
    # Exclusive creation prevents a retry from overwriting the pre-migration copy.
    with target.open("xb") as stream, gzip.open(stream, "wt", encoding="utf-8") as compressed:
        json.dump(payload, compressed, ensure_ascii=False, default=str)
    return str(target)


def migrate_forum(session, source, *, apply=False, backup_dir=None, url=None, store_url=None):
    items, chunks = forum_rows(session, source.id)
    topic_ids = sorted({int(i.metadata_json["topic_id"]) for i in items})
    legacy = [i for i in items if i.metadata_json.get("forum_item_layout") != LAYOUT]
    original_post_count = sum(
        len(i.metadata_json["posts"]) if i.metadata_json.get("forum_item_layout") == LAYOUT else 1
        for i in items
    )
    result = {
        "forum": source.config_json["forum"],
        "topics": len(topic_ids),
        "items_before": len(items),
        "chunks": len(chunks),
        "legacy_items": len(legacy),
        "vector_refs": sum(bool(c.vector_point_id or c.qdrant_point_id) for c in chunks),
        "status": "preview" if not apply else "success",
    }
    if not apply:
        return result
    if legacy:
        result["backup"] = backup_forum(
            session, source, items, chunks, backup_dir, url=url, store_url=store_url
        )
        before = chunk_digest(chunks)
        merged = 0
        for topic_id in topic_ids:
            _, count = migrate_legacy_topic(session, source, topic_id)
            merged += count
        session.flush()
        after_items, after_chunks = forum_rows(session, source.id)
        if len(after_items) != len(topic_ids) or chunk_digest(after_chunks) != before:
            raise ValueError("Migration invariant failed: item count or chunk content changed")
        if sum(len(i.metadata_json["posts"]) for i in after_items) != original_post_count:
            raise ValueError("Migration invariant failed: post provenance coverage changed")
        session.commit()
        result["items_merged"] = merged
        result["chunk_digest"] = before
    else:
        session.commit()
        result["items_merged"] = 0
    result["items_after"] = len(forum_rows(session, source.id)[0])
    result.update(sync_vector_payloads(session, source.id, url=url, store_url=store_url))
    if result["payloads_failed"]:
        result["status"] = "partial"
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--forums", nargs="+", choices=[*FORUMS, *ALIASES], default=list(FORUMS))
    parser.add_argument("--apply", action="store_true", help="Apply instead of preview")
    parser.add_argument("--backup-dir", type=Path, help="Exclusive per-forum compressed backups")
    parser.add_argument("--qdrant-url", help="Reachable Qdrant URL, default from .env")
    parser.add_argument("--vector-store-url", help="Original URL used to identify existing vectors")
    args = parser.parse_args(argv)
    settings = get_settings()
    url = args.qdrant_url or settings.qdrant_url
    store_url = args.vector_store_url or settings.qdrant_url
    backup_dir = args.backup_dir or ROOT / "data/backups" / (
        "forum-topic-" + datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
    )
    engine = create_engine(
        settings.sync_database_url, pool_pre_ping=True, connect_args={"connect_timeout": 10}
    )
    try:
        results = []
        with ExitStack() as stack:
            sessions = {}
            # Acquire all locks before any write, including when only one forum is selected.
            for forum in canonical_forums(args.forums):
                session = stack.enter_context(forum_session(engine, forum))
                if session is None:
                    raise RuntimeError(f"Forum {forum} is busy; stop its collector and retry")
                sessions[forum] = session
            for forum, session in sessions.items():
                source = session.scalar(
                    select(SourceSite).where(
                        SourceSite.config_json["kind"].as_string() == "discourse",
                        SourceSite.config_json["forum"].as_string() == forum,
                        SourceSite.base_url == FORUMS[forum],
                    )
                )
                if source is None:
                    results.append({"forum": forum, "status": "absent"})
                    continue
                results.append(
                    migrate_forum(
                        session,
                        source,
                        apply=args.apply,
                        backup_dir=backup_dir,
                        url=url,
                        store_url=store_url,
                    )
                )
        result = {
            "status": "success" if all(r["status"] != "partial" for r in results) else "partial",
            "results": results,
        }
    except Exception as exc:
        result = {"status": "failed", "error": str(exc)[:2000]}
    finally:
        engine.dispose()
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["status"] == "success" else 1


if __name__ == "__main__":
    sys.exit(main())
