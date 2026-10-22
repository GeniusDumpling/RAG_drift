from __future__ import annotations

import sys
import uuid
from contextlib import contextmanager
from datetime import UTC, datetime
from typing import Any

from .chunking import Budget
from .common import ROOT, digest

sys.path.insert(0, str(ROOT / "backend"))

import app.models  # noqa: E402, F401
from app.db.base import utcnow  # noqa: E402
from app.models.content import Author, ContentChunk, ContentItem, RawPage  # noqa: E402
from app.models.control import CrawlJob, CrawlRun, CrawlRunEvent, SourceSite  # noqa: E402
from app.services.retrieval import QdrantIndexer  # noqa: E402
from sqlalchemy import select, text  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

OWNER = "ifixit_rag"
LOCK_ID = 453629014288
INDEX_FIELDS = ("vector_collection", "vector_store_id", "embedding_model", "embedding_dimension")


def configured_engine(database_url: str | None, *, with_index: bool) -> tuple[Any, Any]:
    from app.core.config import get_settings
    from app.services.embeddings import build_embedding_service
    from sqlalchemy import create_engine

    settings = get_settings()
    indexer = (
        QdrantIndexer(
            url=settings.qdrant_url,
            collection=settings.qdrant_collection,
            embedding=build_embedding_service(settings),
        )
        if with_index
        else None
    )
    return create_engine(database_url or settings.sync_database_url, pool_pre_ping=True), indexer


@contextmanager
def locked_session(engine: Any):
    # Hold a dedicated connection so session-level lock survives per-guide commits.
    with engine.connect() as connection:
        if connection.dialect.name != "postgresql":
            raise ValueError("the importer requires PostgreSQL")
        acquired = connection.scalar(text("SELECT pg_try_advisory_lock(:key)"), {"key": LOCK_ID})
        connection.commit()
        if not acquired:
            raise RuntimeError("another iFixit import/index process is running")
        try:
            with Session(bind=connection, expire_on_commit=False) as session:
                yield session
        finally:
            connection.rollback()
            connection.execute(text("SELECT pg_advisory_unlock(:key)"), {"key": LOCK_ID})
            connection.commit()


def _timestamp(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromtimestamp(float(value), UTC)
    except (ValueError, OverflowError, TypeError):
        return None


def _event(
    session: Session,
    run: CrawlRun,
    kind: str,
    *,
    error: str | None = None,
    item: ContentItem | None = None,
    counters: dict[str, Any] | None = None,
) -> None:
    session.add(
        CrawlRunEvent(
            crawl_run_id=run.id,
            stage="import",
            level="error" if error else "info",
            event_type=kind,
            message=error or kind,
            related_content_item_id=item.id if item is not None else None,
            counters_json=counters or {},
            agent_trace_json={"owner": OWNER},
        )
    )


def _source_and_job(session: Session) -> tuple[SourceSite, CrawlJob]:
    source = session.scalar(
        select(SourceSite).where(
            SourceSite.name == "iFixit local repair guides",
            SourceSite.base_url == "https://www.ifixit.com",
        )
    )
    if source is None:
        source = SourceSite(
            name="iFixit local repair guides",
            site_type="documentation",
            base_url="https://www.ifixit.com",
            allowed_domains=["www.ifixit.com"],
            fetch_mode="local_file",
            default_language="en",
            active=True,
            config_json={"importer": OWNER, "scheduled_fetch_supported": False},
        )
        session.add(source)
        session.flush()
    job = session.scalar(
        select(CrawlJob).where(
            CrawlJob.source_site_id == source.id,
            CrawlJob.name == "iFixit local bundle import",
        )
    )
    if job is None:
        job = CrawlJob(
            source_site_id=source.id,
            name="iFixit local bundle import",
            trigger_mode="manual",
            parser_profile=OWNER,
            enabled=False,
            max_pages=1,
            seed_config_json={},
            agent_policy_json={"chunk_owner": OWNER},
        )
        session.add(job)
        session.flush()
    return source, job


def _author(session: Session, source: SourceSite, payload: dict[str, Any]) -> uuid.UUID | None:
    data = payload.get("author") or {}
    if not data.get("userid"):
        return None
    external_id = str(data["userid"])
    author = session.scalar(
        select(Author).where(
            Author.source_site_id == source.id,
            Author.external_author_id == external_id,
        )
    )
    if author is None:
        author = Author(source_site_id=source.id, external_author_id=external_id)
        session.add(author)
    author.display_name = data.get("username") or external_id
    author.handle = data.get("unique_username")
    author.profile_url = data.get("url")
    author.raw_json = data
    session.flush()
    return author.id


def _queue_cleanup(chunk: ContentChunk) -> list[dict[str, Any]]:
    metadata = chunk.chunk_metadata_json or {}
    pending = list(metadata.get("pending_vector_deletions", []))
    if chunk.vector_point_id:
        target = {
            "point_id": chunk.vector_point_id,
            "backend": chunk.vector_backend,
            "collection": metadata.get("vector_collection"),
            "store_id": metadata.get("vector_store_id"),
        }
        if target not in pending:
            pending.append(target)
    return pending


def sync_chunks(
    session: Session,
    item: ContentItem,
    definitions: list[dict[str, Any]],
) -> dict[str, int]:
    existing = list(
        session.scalars(
            select(ContentChunk)
            .where(
                ContentChunk.content_item_id == item.id,
            )
            .order_by(ContentChunk.chunk_index)
        )
    )
    by_key = {c.chunk_metadata_json.get("logical_chunk_key"): c for c in existing}
    if any(c.chunk_metadata_json.get("chunk_owner") != OWNER for c in existing):
        raise ValueError("refusing to overwrite chunks owned by another pipeline")
    keys = {d["logical_chunk_key"] for d in definitions}
    counts = {"chunks_created": 0, "chunks_updated": 0, "chunks_obsolete": 0}
    # Free the active index range before reordering. Obsolete rows keep high indices
    # and their cleanup outbox, while logical keys retain stable UUIDs.
    base = max([c.chunk_index for c in existing] + [len(definitions)]) + 1
    needs_reorder = any(
        c.chunk_index != d["chunk_index"]
        for d in definitions
        if (c := by_key.get(d["logical_chunk_key"]))
    )
    if needs_reorder or any(
        c.chunk_metadata_json.get("logical_chunk_key") not in keys
        and c.chunk_index < len(definitions)
        for c in existing
    ):
        for number, chunk in enumerate(existing):
            chunk.chunk_index = base + number
        session.flush()
    for definition in definitions:
        key = definition["logical_chunk_key"]
        chunk = by_key.get(key)
        is_new = chunk is None
        if chunk is None:
            chunk = ContentChunk(id=uuid.uuid5(uuid.NAMESPACE_URL, key), content_item_id=item.id)
            session.add(chunk)
        previous = dict(chunk.chunk_metadata_json or {})
        changed = previous.get("chunk_hash") != definition["chunk_hash"]
        revived = chunk.embed_status == "obsolete"
        pending = (
            _queue_cleanup(chunk)
            if changed or revived
            else previous.get(
                "pending_vector_deletions",
                [],
            )
        )
        chunk.chunk_index = definition["chunk_index"]
        chunk.char_start = definition["char_start"]
        chunk.char_end = definition["char_end"]
        chunk.display_text = definition["display_text"]
        chunk.embed_text = definition["embed_text"]
        if changed or revived or is_new:
            chunk.token_count = definition["token_count"]
        chunk.chunk_metadata_json = {
            **definition["metadata"],
            "chunk_hash": definition["chunk_hash"],
            **{key: previous[key] for key in INDEX_FIELDS if key in previous},
            "pending_vector_deletions": pending,
        }
        if not changed and not revived and chunk.embed_status == "success":
            chunk.chunk_metadata_json = {
                **chunk.chunk_metadata_json,
                **{
                    key: previous[key]
                    for key in ("tokenizer", "token_count_method")
                    if key in previous
                },
            }
        if changed or revived or is_new:
            chunk.embed_status = "pending" if definition["metadata"]["indexable"] else "failed"
            chunk.embed_error = definition["metadata"]["index_error"]
            chunk.vector_point_id = None
            chunk.qdrant_point_id = None
            chunk.vector_backend = None
            chunk.embedded_at = None
            counts["chunks_created" if is_new else "chunks_updated"] += 1
    obsolete_index = max([base + len(existing)] + [len(definitions)])
    for chunk in existing:
        if chunk.chunk_metadata_json.get("logical_chunk_key") not in keys:
            if chunk.embed_status != "obsolete":
                counts["chunks_obsolete"] += 1
            chunk.chunk_metadata_json = {
                **chunk.chunk_metadata_json,
                "pending_vector_deletions": _queue_cleanup(chunk),
            }
            chunk.chunk_index = obsolete_index
            obsolete_index += 1
            chunk.embed_status = "obsolete"
            chunk.embed_error = "obsolete chunk"
    session.flush()
    return counts


def _import_guide(
    session: Session,
    source: SourceSite,
    run: CrawlRun,
    guide: dict[str, Any],
    raw: dict[str, Any],
    steps: list[dict[str, Any]],
    chunks: list[dict[str, Any]],
) -> tuple[ContentItem, str, dict[str, int]]:
    item = session.scalar(select(ContentItem).where(ContentItem.dedup_key == guide["document_id"]))
    if item is not None and (
        item.item_type != "repair_guide" or item.metadata_json.get("chunk_owner") != OWNER
    ):
        raise ValueError("document ID is already owned by another pipeline")
    action = "created" if item is None else "updated"
    unchanged = (
        item is not None
        and item.content_hash == guide["content_hash"]
        and (item.metadata_json.get("source_sha256") == raw["source_sha256"])
    )
    if unchanged:
        action = "unchanged"
    else:
        page = RawPage(
            source_site_id=source.id,
            crawl_run_id=run.id,
            requested_url=guide["source_path"],
            final_url=guide["source_url"],
            http_status=None,
            content_type="application/json",
            raw_text=raw["markdown"],
            raw_json=raw["payload"],
            fetched_at=utcnow(),
            response_headers_json={"timestamp_basis": "local_import"},
            parser_profile=OWNER,
            extraction_method="local_json",
            parse_status="parsed",
            body_hash=raw["source_sha256"],
        )
        session.add(page)
        session.flush()
        author_id = _author(session, source, raw["payload"])
        if item is None:
            item = ContentItem(dedup_key=guide["document_id"], source_site_id=source.id)
            session.add(item)
        item.raw_page_id = page.id
        item.crawl_run_id = run.id
        item.author_id = author_id
        item.item_type = "repair_guide"
        item.title = guide["title"]
        item.canonical_url = guide["source_url"]
        item.source_url = guide["source_url"]
        item.published_at = _timestamp(guide["published_date"])
        item.language = guide["locale"]
        item.raw_text = raw["markdown"]
        item.cleaned_text = guide["cleaned_text"]
        item.summary_text = guide["introduction"] or None
        item.structured_by = OWNER
        item.tags = guide["tags"]
        item.metadata_json = {
            "chunk_owner": OWNER,
            "source_sha256": raw["source_sha256"],
            "ifixit": {k: v for k, v in guide.items() if k not in {"sections", "cleaned_text"}},
            "steps": steps,
        }
        item.content_hash = guide["content_hash"]
        session.flush()
        # search_tsv is kept coherent even though the current keyword retriever also
        # queries title, tags, display_text and embed_text directly.
        session.execute(
            text(
                "UPDATE content_items SET search_tsv = to_tsvector('simple', :body) WHERE id = :id"
            ),
            {"body": item.cleaned_text, "id": item.id},
        )
    assert item is not None
    return item, action, sync_chunks(session, item, chunks)


def import_bundle(session: Session, bundle: dict[str, Any]) -> dict[str, Any]:
    source, job = _source_and_job(session)
    run = CrawlRun(
        source_site_id=source.id,
        crawl_job_id=job.id,
        trigger_type="manual",
        execution_mode="local_import",
        seed_url=bundle["manifest"]["input_dir"],
        status="running",
        started_at=utcnow(),
        config_snapshot_json={"importer": OWNER, "manifest": bundle["manifest"]},
    )
    session.add(run)
    session.flush()
    run_id = run.id
    source_id = source.id
    job_id = job.id
    _event(session, run, "run_started")
    session.commit()
    counts = {
        "created": 0,
        "updated": 0,
        "unchanged": 0,
        "failed": 0,
        "chunks_created": 0,
        "chunks_updated": 0,
        "chunks_obsolete": 0,
    }
    errors = []
    for guide in bundle["guides"]:
        document_id = guide["document_id"]
        try:
            item, action, chunk_counts = _import_guide(
                session,
                source,
                run,
                guide,
                bundle["raws_by_id"][document_id],
                bundle["steps_by_doc"][document_id],
                bundle["chunks_by_doc"][document_id],
            )
            _event(session, run, "guide_" + action, item=item, counters=chunk_counts)
            session.commit()
            counts[action] += 1
            for name, value in chunk_counts.items():
                counts[name] += value
        except Exception as exc:
            session.rollback()
            counts["failed"] += 1
            error = {"document_id": document_id, "error": str(exc)[:1000]}
            errors.append(error)
            run = session.get(CrawlRun, run_id)
            source = session.get(SourceSite, source_id)
            assert run is not None and source is not None
            _event(session, run, "guide_failed", error=f"{document_id}: {error['error']}")
            session.commit()
    run = session.get(CrawlRun, run_id)
    job = session.get(CrawlJob, job_id)
    assert run is not None and job is not None
    preparation_errors = bundle["manifest"]["counts"]["parse_errors"]
    run.status = (
        "success"
        if not counts["failed"] and not preparation_errors
        else "partial"
        if sum(counts[k] for k in ("created", "updated", "unchanged"))
        else "failed"
    )
    run.finished_at = utcnow()
    run.discovered_count = bundle["manifest"]["counts"]["input_guides"]
    run.fetched_count = len(bundle["guides"])
    run.parsed_count = len(bundle["guides"])
    run.extracted_count = counts["created"] + counts["updated"]
    run.deduped_count = counts["unchanged"]
    run.chunked_count = counts["chunks_created"] + counts["chunks_updated"]
    run.error_count = counts["failed"] + preparation_errors
    run.error_message = f"{run.error_count} input/import errors" if run.error_count else None
    job.last_run_at = run.finished_at
    _event(session, run, "run_finished", counters=counts)
    session.commit()
    return {
        "run_id": str(run.id),
        "source_id": str(source_id),
        "status": run.status,
        **counts,
        "preparation_errors": preparation_errors,
        "errors": errors,
    }


def _target(indexer: QdrantIndexer) -> dict[str, Any]:
    return {
        "vector_collection": indexer.collection,
        "vector_store_id": digest(indexer.url.encode("utf-8")),
        "embedding_model": indexer.embedding.model_name,
        "embedding_dimension": indexer.embedding.dimension,
    }


def _delete_pending(chunk: ContentChunk, indexer: QdrantIndexer) -> None:
    pending = list(chunk.chunk_metadata_json.get("pending_vector_deletions", []))
    while pending:
        target = pending[0]
        if target["store_id"] != digest(indexer.url.encode("utf-8")):
            raise ValueError("old vector belongs to a different store; run cleanup on that store")
        if target["backend"] != indexer.backend_name or not target["collection"]:
            raise ValueError("old vector target is incomplete or uses a different backend")
        old_indexer = (
            indexer
            if target["collection"] == indexer.collection
            else QdrantIndexer(
                url=indexer.url,
                collection=target["collection"],
                embedding=indexer.embedding,
            )
        )
        old_indexer.delete_chunk(point_id=target["point_id"])
        if (
            chunk.vector_point_id == target["point_id"]
            and chunk.chunk_metadata_json.get("vector_collection") == target["collection"]
            and chunk.chunk_metadata_json.get("vector_store_id") == target["store_id"]
        ):
            chunk.vector_point_id = None
            chunk.qdrant_point_id = None
            chunk.vector_backend = None
            chunk.embedded_at = None
            if chunk.embed_status == "success":
                chunk.embed_status = "pending"
        pending.pop(0)
        chunk.chunk_metadata_json = {
            **chunk.chunk_metadata_json,
            "pending_vector_deletions": pending,
        }
    if chunk.embed_status == "obsolete":
        chunk.vector_point_id = None
        chunk.qdrant_point_id = None
        chunk.vector_backend = None
        chunk.embedded_at = None


def index_bundle(
    session: Session,
    bundle: dict[str, Any],
    indexer: QdrantIndexer,
    budget: Budget,
) -> dict[str, Any]:
    if budget.tokenizer is None:
        raise ValueError("indexing requires a local tokenizer for actual input-length validation")
    target = _target(indexer)
    # Same dimensions do not imply the same embedding space.
    recorded = session.scalars(select(ContentChunk))
    for chunk in recorded:
        metadata = chunk.chunk_metadata_json
        if (
            metadata.get("vector_collection") == target["vector_collection"]
            and metadata.get("vector_store_id") == target["vector_store_id"]
            and metadata.get("embedding_model") != target["embedding_model"]
            and (chunk.vector_point_id or metadata.get("pending_vector_deletions"))
        ):
            raise ValueError("collection contains another embedding model; use a new collection")
    indexer.ensure_collection()
    if indexer.backend_name == "qdrant":
        info = indexer._require_client().get_collection(collection_name=indexer.collection)
        vectors = info.config.params.vectors
        if (
            isinstance(vectors, dict)
            or vectors is None
            or vectors.size != indexer.embedding.dimension
        ):
            raise ValueError("Qdrant collection dimension/configuration does not match the model")
    counts = {"indexed": 0, "unchanged": 0, "failed": 0, "cleaned_obsolete": 0}
    errors = []
    for guide in bundle["guides"]:
        item = session.scalar(
            select(ContentItem).where(ContentItem.dedup_key == guide["document_id"])
        )
        if item is None or item.metadata_json.get("chunk_owner") != OWNER:
            errors.append(
                {"document_id": guide["document_id"], "error": "guide has not been imported"}
            )
            counts["failed"] += 1
            continue
        if item.content_hash != guide["content_hash"]:
            errors.append(
                {"document_id": guide["document_id"], "error": "database/bundle version mismatch"}
            )
            counts["failed"] += 1
            continue
        chunks = list(
            session.scalars(
                select(ContentChunk)
                .where(
                    ContentChunk.content_item_id == item.id,
                )
                .order_by(ContentChunk.chunk_index)
            )
        )
        for chunk in chunks:
            try:
                if any(chunk.chunk_metadata_json.get(k) != v for k, v in target.items()):
                    if chunk.vector_point_id:
                        chunk.chunk_metadata_json = {
                            **chunk.chunk_metadata_json,
                            "pending_vector_deletions": _queue_cleanup(chunk),
                        }
                        session.commit()
                _delete_pending(chunk, indexer)
                # Persist cleanup before writes. Retrying after a crash repeats only
                # idempotent operations; a prior successful upsert is never deleted later.
                session.commit()
                if chunk.embed_status == "obsolete":
                    counts["cleaned_obsolete"] += 1
                    continue
                if chunk.embed_status == "success" and all(
                    chunk.chunk_metadata_json.get(k) == v for k, v in target.items()
                ):
                    counts["unchanged"] += 1
                    continue
                if not budget.fits(chunk.embed_text):
                    raise ValueError(
                        "embedding input exceeds tokenizer/character budget; re-prepare"
                    )
                payload = {
                    "content_item_id": str(item.id),
                    "source_site_id": str(item.source_site_id),
                    "item_type": item.item_type,
                    "canonical_url": item.canonical_url,
                    "title": item.title,
                    "language": item.language,
                    "tags": list(item.tags),
                    "published_at": item.published_at.isoformat() if item.published_at else None,
                    "guide_id": guide["guide_id"],
                    "device_key": guide["device_key"],
                    "step_order": chunk.chunk_metadata_json.get("step_order"),
                    "logical_chunk_key": chunk.chunk_metadata_json["logical_chunk_key"],
                }
                # Persist the intended point before the remote call. A timeout may
                # occur after Qdrant has accepted the write; later replacement/removal
                # must still know which point to delete even without a success reply.
                chunk.embed_status = "pending"
                chunk.vector_backend = indexer.backend_name
                chunk.vector_point_id = str(chunk.id)
                chunk.qdrant_point_id = str(chunk.id) if indexer.backend_name == "qdrant" else None
                chunk.chunk_metadata_json = {**chunk.chunk_metadata_json, **target}
                session.commit()
                point_id = indexer.upsert_chunk(
                    chunk_id=chunk.id, embed_text=chunk.embed_text, payload=payload
                )
                chunk.embed_status = "success"
                chunk.embed_error = None
                chunk.vector_backend = indexer.backend_name
                chunk.vector_point_id = point_id
                chunk.qdrant_point_id = point_id if indexer.backend_name == "qdrant" else None
                chunk.embedded_at = utcnow()
                chunk.token_count = budget.count(chunk.embed_text)
                chunk.chunk_metadata_json = {
                    **chunk.chunk_metadata_json,
                    **target,
                    "token_count_method": "tokenizer",
                    "tokenizer": budget.tokenizer_name,
                    "pending_vector_deletions": [],
                }
                session.commit()
                counts["indexed"] += 1
            except Exception as exc:
                session.rollback()
                # Rollback expires instances; reload the persisted outbox before marking
                # failure, preserving evidence needed for future cleanup retries.
                if chunk.embed_status != "obsolete":
                    chunk.embed_status = "failed"
                chunk.embed_error = str(exc)[:1000]
                session.commit()
                errors.append({"chunk_id": str(chunk.id), "error": str(exc)[:1000]})
                counts["failed"] += 1
    return {
        "status": "partial" if errors else "success",
        **counts,
        "errors": errors,
        "model": indexer.embedding.model_name,
        "collection": indexer.collection,
    }


class _DeleteOnlyEmbedding:
    model_name = "delete-only"
    dimension = 1

    def embed(self, value: str) -> list[float]:
        raise RuntimeError("cleanup must never embed text")


def cleanup_bundle(session: Session, bundle: dict[str, Any], url: str) -> dict[str, Any]:
    """Consume only existing deletion tasks for this URL; never create new vectors."""
    store_id = digest(url.encode("utf-8"))
    counts = {"cleaned": 0, "pending_other_store": 0, "failed": 0}
    errors = []
    for guide in bundle["guides"]:
        item = session.scalar(
            select(ContentItem).where(
                ContentItem.dedup_key == guide["document_id"],
            )
        )
        if item is None or item.metadata_json.get("chunk_owner") != OWNER:
            continue
        chunks = list(
            session.scalars(
                select(ContentChunk).where(
                    ContentChunk.content_item_id == item.id,
                )
            )
        )
        for chunk in chunks:
            pending = list(chunk.chunk_metadata_json.get("pending_vector_deletions", []))
            matching = [task for task in pending if task["store_id"] == store_id]
            other = [task for task in pending if task["store_id"] != store_id]
            counts["pending_other_store"] += len(other)
            if not matching:
                continue
            try:
                chunk.chunk_metadata_json = {
                    **chunk.chunk_metadata_json,
                    "pending_vector_deletions": matching,
                }
                indexer = QdrantIndexer(
                    url=url, collection=matching[0]["collection"], embedding=_DeleteOnlyEmbedding()
                )
                _delete_pending(chunk, indexer)
                chunk.chunk_metadata_json = {
                    **chunk.chunk_metadata_json,
                    "pending_vector_deletions": other,
                }
                session.commit()
                counts["cleaned"] += len(matching)
            except Exception as exc:
                session.rollback()
                errors.append({"chunk_id": str(chunk.id), "error": str(exc)[:1000]})
                counts["failed"] += 1
    return {
        "status": "partial" if errors or counts["pending_other_store"] else "success",
        **counts,
        "errors": errors,
    }
