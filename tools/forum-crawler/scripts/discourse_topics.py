"""One durable item per topic, with independently versioned post chunks.

Ownership changes never alter a chunk's text/ID/vector. Payload changes are an
outbox in chunk metadata, committed before touching Qdrant and safe to retry.
"""

from __future__ import annotations

import hashlib
from collections import defaultdict

from app.models.content import Author, ContentChunk, ContentItem
from app.repositories.contents import stable_hash
from sqlalchemy import Column, MetaData, Table, case, func, inspect, select, update
from sqlalchemy.dialects.postgresql import UUID

LAYOUT = "topic-v1"
PROCESSOR_VERSION = "discourse-v3.0"
# This describes chunk text semantics, independently of item ownership. Keeping
# v2.1 semantics lets an unchanged legacy post reuse its existing chunks/vectors.
POST_CHUNK_VERSION = "discourse-v2.1"


def topic_key(base, topic_id):
    return stable_hash(f"discourse:{base.rstrip('/')}:topic:{int(topic_id)}")


def topic_items(session, source_id, topic_id):
    return session.scalars(
        select(ContentItem)
        .where(
            ContentItem.source_site_id == source_id,
            ContentItem.metadata_json["topic_id"].as_integer() == int(topic_id),
        )
        .order_by(ContentItem.created_at, ContentItem.id)
    ).all()


def post_state_from_item(item):
    meta = item.metadata_json
    return {
        **{
            key: meta.get(key)
            for key in (
                "post_id",
                "post_number",
                "reply_to_post_number",
                "source_updated_at",
                "source_version",
                "accepted_answer",
                "resolution_status",
                "post_role",
                "rag_eligible",
                "processing_fingerprint",
                "source_visibility",
            )
        },
        "author_id": str(item.author_id) if item.author_id else None,
        "author_name": None,
        "published_at": item.published_at.isoformat() if item.published_at else None,
        "canonical_url": item.canonical_url,
        "raw_page_id": str(item.raw_page_id),
        "raw_text": item.raw_text or "",
        "cleaned_text": item.cleaned_text,
        "legacy_item_id": str(item.id),
    }


def _retarget_references(session, mapping):
    """Preserve audit/self references and refuse unknown dependent projections."""
    if not mapping:
        return
    connection = session.connection()
    schema = connection.get_execution_options().get("schema_translate_map", {}).get(None)
    references = session.info.get("forum_item_references")
    if references is None:
        inspector = inspect(connection)
        references = []
        for name in inspector.get_table_names(schema=schema):
            for fk in inspector.get_foreign_keys(name, schema=schema):
                if fk["referred_table"] == "content_items":
                    if len(fk["constrained_columns"]) != 1:
                        raise ValueError(f"Unsupported composite item reference: {name}")
                    references.append((name, fk["constrained_columns"][0]))
        session.info["forum_item_references"] = references
    for name, column_name in references:
        if name == "content_chunks":
            continue  # Already moved, retaining chunk IDs and their references.
        table = Table(name, MetaData(), Column(column_name, UUID(as_uuid=True)))
        column = table.c[column_name]
        predicate = column.in_(list(mapping))
        count = session.scalar(select(func.count()).select_from(table).where(predicate))
        if not count:
            continue
        if name not in {"content_items", "crawl_run_events", "agent_calls"}:
            raise ValueError(f"Migrate dependent {name}.{column_name} before merging items")
        session.execute(
            update(table).where(predicate).values({column_name: case(mapping, value=column)})
        )


def migrate_legacy_topic(session, source, topic_id):
    """In-transaction merge, retaining all chunks, including obsolete generations."""
    items = topic_items(session, source.id, topic_id)
    if not items:
        return None, 0
    migrated = [i for i in items if i.metadata_json.get("forum_item_layout") == LAYOUT]
    if migrated:
        if len(items) != 1:
            raise ValueError(f"Mixed topic/post ownership for topic {topic_id}")
        return migrated[0], 0
    if any(i.structured_by != POST_CHUNK_VERSION for i in items):
        raise ValueError(f"Unsupported legacy processor for topic {topic_id}")
    roots = [i for i in items if i.metadata_json.get("post_number") == 1]
    if len(roots) != 1:
        raise ValueError(f"Expected one root item for topic {topic_id}")
    root = roots[0]
    states = {}
    for item in items:
        state = post_state_from_item(item)
        key = str(state["post_id"])
        if key in states:
            raise ValueError(f"Duplicate legacy post {key} in topic {topic_id}")
        # Author is not necessarily included in chunk display text.
        author = session.get(Author, item.author_id) if item.author_id else None
        state["author_name"] = author.display_name if author else None
        states[key] = state
    chunks = session.scalars(
        select(ContentChunk)
        .where(ContentChunk.content_item_id.in_([i.id for i in items]))
        .order_by(ContentChunk.chunk_index, ContentChunk.id)
    ).all()
    by_item = {i.id: i for i in items}
    next_index = (
        max((c.chunk_index for c in chunks if c.content_item_id == root.id), default=-1) + 1
    )
    for chunk in sorted(
        chunks,
        key=lambda c: (
            by_item[c.content_item_id].metadata_json["post_number"],
            c.chunk_index,
            str(c.id),
        ),
    ):
        old = by_item[chunk.content_item_id]
        chunk.chunk_metadata_json = {
            **chunk.chunk_metadata_json,
            "post_id": old.metadata_json["post_id"],
            "post_chunk_index": chunk.chunk_index,
            "post_char_start": chunk.char_start,
            "post_char_end": chunk.char_end,
            "legacy_item_id": str(old.id),
        }
        if old.id != root.id:
            chunk.content_item_id = root.id
            chunk.chunk_index = next_index
            next_index += 1
    session.flush()  # All chunks must survive the cascading deletion of reply items.
    mapping = {item.id: root.id for item in items if item.id != root.id}
    _retarget_references(session, mapping)
    for item in items:
        if item.id != root.id:
            session.delete(item)
    root.metadata_json = {
        **root.metadata_json,
        "forum_item_layout": LAYOUT,
        "posts": states,
        "legacy_item_ids": [str(i.id) for i in items],
    }
    root.item_type = "thread"
    root.structured_by = PROCESSOR_VERSION
    root.dedup_key = topic_key(source.base_url, topic_id)
    root.canonical_url = f"{source.base_url.rstrip('/')}/t/{topic_id}"
    root.source_url = root.canonical_url + ".json"
    root.parent_item_id = None
    root.thread_root_id = root.id
    layout_topic(session, root, states, chunks)
    session.flush()
    return root, len(mapping)


def chunk_provenance(item, state):
    return {
        "forum_item_layout": LAYOUT,
        "forum": item.metadata_json["forum"],
        "topic_id": item.metadata_json["topic_id"],
        "post_id": state["post_id"],
        "post_number": state["post_number"],
        "post_type": "thread" if state["post_number"] == 1 else "comment",
        "reply_to_post_number": state.get("reply_to_post_number"),
        "author_id": state.get("author_id"),
        "author_name": state.get("author_name"),
        "published_at": state.get("published_at"),
        "canonical_url": state["canonical_url"],
        "raw_page_id": state["raw_page_id"],
        "accepted_answer": state.get("accepted_answer", False),
        "resolution_status": state.get("resolution_status", "unverified"),
        "post_role": state.get("post_role"),
    }


def layout_topic(session, item, states, chunks=None):
    """Recompute full text/spans without re-embedding unchanged individual posts."""
    visible = sorted(
        (s for s in states.values() if s.get("source_visibility") == "visible"),
        key=lambda s: s["post_number"],
    )
    bodies = []
    offset = 0
    for state in visible:
        state["char_start"] = offset
        body = state["cleaned_text"]
        state["char_end"] = offset + len(body)
        bodies.append(body)
        offset += len(body) + 2
    item.cleaned_text = "\n\n".join(bodies)
    item.raw_text = "\n\n".join(
        f'Post #{s["post_number"]} · {s.get("author_name") or "unknown"}\n{s["raw_text"]}'
        for s in visible
    )
    item.content_hash = stable_hash(item.raw_text)
    item.metadata_json = {
        **item.metadata_json,
        "forum_item_layout": LAYOUT,
        "posts": states,
        "post_count": len(visible),
        "reply_count": max(0, len(visible) - 1),
        "rag_eligible": any(s.get("rag_eligible") for s in visible),
    }
    if chunks is None:
        chunks = session.scalars(
            select(ContentChunk).where(ContentChunk.content_item_id == item.id)
        ).all()
    for chunk in chunks:
        meta = chunk.chunk_metadata_json
        state = states.get(str(meta.get("post_id")))
        if state is None:
            raise ValueError(f"Chunk {chunk.id} has no post provenance")
        provenance = chunk_provenance(item, state)
        if (chunk.vector_point_id or chunk.qdrant_point_id) and any(
            meta.get(key) != provenance[key]
            for key in (
                "forum_item_layout",
                "post_id",
                "post_number",
                "canonical_url",
                "author_name",
                "published_at",
                "accepted_answer",
                "resolution_status",
            )
        ):
            meta = {**meta, "vector_payload_pending": True}
        active = chunk.embed_status != "obsolete" and state.get("source_visibility") == "visible"
        start, end = meta.get("post_char_start"), meta.get("post_char_end")
        chunk.char_start = state["char_start"] + start if active and start is not None else None
        chunk.char_end = state["char_start"] + end if active and end is not None else None
        chunk.chunk_metadata_json = {
            **meta,
            **provenance,
            "offset_basis": "cleaned_text",
            "start_char": chunk.char_start,
            "end_char": chunk.char_end,
        }


def payload_rows(session, source_id, *, limit=None):
    query = (
        select(ContentChunk)
        .join(ContentItem, ContentItem.id == ContentChunk.content_item_id)
        .where(
            ContentItem.source_site_id == source_id,
            ContentChunk.chunk_metadata_json["vector_payload_pending"].as_boolean().is_(True),
        )
        .order_by(ContentChunk.id)
    )
    if limit is not None:
        query = query.limit(limit)
    chunks = session.scalars(query).all()
    # A topic may have thousands of chunks. Selecting the full topic JSON/text
    # in the join duplicates it once per chunk before ORM identity deduplication.
    owners = {
        item.id: item
        for item in session.scalars(
            select(ContentItem).where(ContentItem.id.in_({c.content_item_id for c in chunks}))
        )
    }
    return [(c, owners[c.content_item_id]) for c in chunks]


def sync_vector_payloads(session, source_id, *, url, store_url=None, client=None, limit=None):
    """Metadata-only Qdrant writes. Never instantiate or call an embedding model."""
    from app.services.embedding_reindex import _build_payload
    from qdrant_client import QdrantClient
    from qdrant_client.http import models

    rows = payload_rows(session, source_id, limit=limit)
    if not rows:
        return {"payloads_updated": 0, "payloads_failed": 0}
    client = client or QdrantClient(url=url, timeout=30)
    identity = hashlib.sha256((store_url or url).encode()).hexdigest()
    grouped = defaultdict(list)
    failed = 0
    errors = []
    for chunk, item in rows:
        meta = chunk.chunk_metadata_json
        if not (chunk.vector_point_id or chunk.qdrant_point_id):
            chunk.chunk_metadata_json = {**meta, "vector_payload_pending": False}
            continue
        if (
            chunk.vector_backend != "qdrant"
            or meta.get("vector_store_id") != identity
            or not meta.get("vector_collection")
        ):
            failed += 1
            errors.append(f"Vector store/collection mismatch for chunk {chunk.id}")
            continue
        grouped[meta["vector_collection"]].append((chunk, item))
    for collection, group in grouped.items():
        for start in range(0, len(group), 100):
            batch = group[start : start + 100]
            point_ids = [c.vector_point_id or c.qdrant_point_id for c, _ in batch]
            try:
                points = client.retrieve(
                    collection_name=collection, ids=point_ids, with_payload=True, with_vectors=False
                )
                by_id = {str(p.id): p for p in points}
                operations = []
                for (chunk, item), pid in zip(batch, point_ids, strict=True):
                    point = by_id.get(str(pid))
                    if point is None or point.payload.get("chunk_id") != str(chunk.id):
                        raise ValueError(f"Missing/mismatched vector point for chunk {chunk.id}")
                    operations.append(
                        models.SetPayloadOperation(
                            set_payload=models.SetPayload(
                                payload={
                                    **_build_payload(content_chunk=chunk, content_item=item),
                                    "thread_root_id": str(item.id),
                                    "parent_item_id": None,
                                },
                                points=[pid],
                            )
                        )
                    )
                client.batch_update_points(
                    collection_name=collection, update_operations=operations, wait=True
                )
                for chunk, _ in batch:
                    chunk.chunk_metadata_json = {
                        **chunk.chunk_metadata_json,
                        "vector_payload_pending": False,
                    }
                session.commit()
            except Exception as exc:
                session.rollback()
                failed += len(batch)
                errors.append(str(exc)[:1000])
    session.commit()
    result = {"payloads_updated": len(rows) - failed, "payloads_failed": failed}
    if errors:
        result["payload_errors"] = errors[:10]
    return result
