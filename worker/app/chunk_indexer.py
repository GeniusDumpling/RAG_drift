import hashlib
import uuid
from dataclasses import dataclass
from typing import Any, Literal

from app.core.config import get_settings
from app.db.base import utcnow
from app.models.content import ContentChunk, ContentItem
from app.models.control import CrawlRunEvent
from app.services.chunking import BuiltChunk, build_chunks
from app.services.embeddings import DeterministicEmbeddingService
from app.services.retrieval import QdrantIndexer
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession


@dataclass(frozen=True)
class ChunkIndexingResult:
    chunked_count: int
    embedded_count: int
    failed_count: int


VectorIndexMetadata = dict[str, str | int]


@dataclass(frozen=True)
class VectorIndexTarget:
    backend_name: Literal["memory", "qdrant"]
    metadata: VectorIndexMetadata


@dataclass(frozen=True)
class ObsoleteVectorPoint:
    content_chunk: ContentChunk
    backend_name: str
    point_id: str
    collection: str | None
    store_id: str | None


async def chunk_and_index_content_items(
    *,
    session: AsyncSession,
    run_id: uuid.UUID,
    content_item_ids: list[uuid.UUID],
) -> ChunkIndexingResult:
    unique_item_ids = list(dict.fromkeys(content_item_ids))
    vector_target = _current_vector_index_target()
    chunks_to_index: list[tuple[ContentChunk, ContentItem]] = []
    obsolete_vector_points: list[ObsoleteVectorPoint] = []
    newly_created_chunk_count = 0

    for content_item_id in unique_item_ids:
        content_item = await session.get(ContentItem, content_item_id)
        if content_item is None:
            continue

        thread_title = await _thread_title_for_item(session, content_item)
        (
            chunks_for_indexing,
            created_chunk_count,
            item_obsolete_vector_points,
        ) = await _sync_chunks_for_item(
            session=session,
            content_item=content_item,
            thread_title=thread_title,
            vector_target=vector_target,
        )
        for content_chunk in chunks_for_indexing:
            chunks_to_index.append((content_chunk, content_item))
        obsolete_vector_points.extend(item_obsolete_vector_points)
        newly_created_chunk_count += created_chunk_count

    if not chunks_to_index and not obsolete_vector_points:
        return ChunkIndexingResult(chunked_count=0, embedded_count=0, failed_count=0)

    await session.commit()

    failed_initialization_backend_name: Literal["memory", "qdrant"] = "qdrant"
    try:
        indexer = _build_qdrant_indexer()
        index_target = _vector_index_target_from_indexer(indexer, fallback=vector_target)
        failed_initialization_backend_name = indexer.backend_name
        indexer.ensure_collection()
    except Exception as exc:
        error_message = _error_message(exc)
        for content_chunk, _content_item in chunks_to_index:
            _mark_chunk_failed(
                content_chunk=content_chunk,
                backend_name=failed_initialization_backend_name,
                error_message=error_message,
            )
            await _record_vector_index_failure_event(
                session=session,
                run_id=run_id,
                content_chunk=content_chunk,
                error_message=error_message,
            )
        blocked_obsolete_vector_points = [
            obsolete_vector_point
            for obsolete_vector_point in obsolete_vector_points
            if obsolete_vector_point.backend_name == vector_target.backend_name
        ]
        for obsolete_vector_point in blocked_obsolete_vector_points:
            await _record_vector_delete_failure_event(
                session=session,
                run_id=run_id,
                content_chunk=obsolete_vector_point.content_chunk,
                error_message=_vector_delete_error_message(
                    point_id=obsolete_vector_point.point_id,
                    cause=error_message,
                ),
            )
        await session.flush()
        return ChunkIndexingResult(
            chunked_count=newly_created_chunk_count,
            embedded_count=0,
            failed_count=len(chunks_to_index) + len(blocked_obsolete_vector_points),
        )

    embedded_count = 0
    failed_count = await _delete_obsolete_vector_points(
        session=session,
        run_id=run_id,
        indexer=indexer,
        obsolete_vector_points=obsolete_vector_points,
    )
    for content_chunk, content_item in chunks_to_index:
        try:
            point_id = indexer.upsert_chunk(
                chunk_id=content_chunk.id,
                embed_text=content_chunk.embed_text,
                payload=_build_chunk_payload(
                    content_item=content_item,
                    chunk_id=content_chunk.id,
                ),
            )
        except Exception as exc:
            failed_count += 1
            error_message = _error_message(exc)
            _mark_chunk_failed(
                content_chunk=content_chunk,
                backend_name=indexer.backend_name,
                error_message=error_message,
            )
            await _record_vector_index_failure_event(
                session=session,
                run_id=run_id,
                content_chunk=content_chunk,
                error_message=error_message,
            )
            continue

        embedded_count += 1
        _mark_chunk_success(
            content_chunk=content_chunk,
            backend_name=indexer.backend_name,
            point_id=point_id,
            index_metadata=index_target.metadata,
        )

    await session.flush()
    return ChunkIndexingResult(
        chunked_count=newly_created_chunk_count,
        embedded_count=embedded_count,
        failed_count=failed_count,
    )


async def _load_chunks_for_item(
    session: AsyncSession, content_item_id: uuid.UUID
) -> list[ContentChunk]:
    chunks = await session.scalars(
        select(ContentChunk)
        .where(ContentChunk.content_item_id == content_item_id)
        .order_by(ContentChunk.chunk_index.asc())
    )
    return list(chunks.all())


async def _sync_chunks_for_item(
    *,
    session: AsyncSession,
    content_item: ContentItem,
    thread_title: str | None,
    vector_target: VectorIndexTarget,
) -> tuple[list[ContentChunk], int, list[ObsoleteVectorPoint]]:
    built_chunks = build_chunks(
        item_type=content_item.item_type,
        title=content_item.title,
        cleaned_text=content_item.cleaned_text,
        summary_text=content_item.summary_text,
        tags=content_item.tags,
        thread_title=thread_title,
    )
    existing_chunks = await _load_chunks_for_item(session, content_item.id)
    return await _reconcile_chunks_for_item(
        session=session,
        content_item=content_item,
        built_chunks=built_chunks,
        existing_chunks=existing_chunks,
        vector_target=vector_target,
    )


async def _reconcile_chunks_for_item(
    *,
    session: AsyncSession,
    content_item: ContentItem,
    built_chunks: list[BuiltChunk],
    existing_chunks: list[ContentChunk],
    vector_target: VectorIndexTarget,
) -> tuple[list[ContentChunk], int, list[ObsoleteVectorPoint]]:
    existing_by_index = {chunk.chunk_index: chunk for chunk in existing_chunks}
    expected_indices = {built_chunk.chunk_index for built_chunk in built_chunks}
    chunks_to_index: list[ContentChunk] = []
    new_chunks: list[ContentChunk] = []
    obsolete_vector_points: list[ObsoleteVectorPoint] = []

    for built_chunk in built_chunks:
        existing_chunk = existing_by_index.get(built_chunk.chunk_index)
        if existing_chunk is None:
            new_chunk = _new_content_chunk(content_item=content_item, built_chunk=built_chunk)
            new_chunks.append(new_chunk)
            chunks_to_index.append(new_chunk)
            continue

        if _chunk_definition_is_stale(existing_chunk, built_chunk):
            _apply_built_chunk_to_existing(existing_chunk, built_chunk)
            chunks_to_index.append(existing_chunk)
            continue

        if _chunk_requires_indexing(existing_chunk, vector_target=vector_target):
            chunks_to_index.append(existing_chunk)

    for existing_chunk in existing_chunks:
        if existing_chunk.chunk_index not in expected_indices:
            obsolete_vector_point = _mark_chunk_obsolete(existing_chunk)
            if obsolete_vector_point is not None:
                obsolete_vector_points.append(obsolete_vector_point)

    if not new_chunks:
        await session.flush()
        return chunks_to_index, 0, obsolete_vector_points

    try:
        async with session.begin_nested():
            for content_chunk in new_chunks:
                session.add(content_chunk)
            await session.flush()
    except IntegrityError:
        reloaded_chunks = await _load_chunks_for_item(session, content_item.id)
        if not reloaded_chunks:
            raise
        return await _reconcile_chunks_for_item(
            session=session,
            content_item=content_item,
            built_chunks=built_chunks,
            existing_chunks=reloaded_chunks,
            vector_target=vector_target,
        )

    return chunks_to_index, len(new_chunks), obsolete_vector_points


def _new_content_chunk(*, content_item: ContentItem, built_chunk: BuiltChunk) -> ContentChunk:
    return ContentChunk(
        content_item_id=content_item.id,
        chunk_index=built_chunk.chunk_index,
        char_start=built_chunk.start_char,
        char_end=built_chunk.end_char,
        display_text=built_chunk.display_text,
        embed_text=built_chunk.embed_text,
        token_count=built_chunk.token_count,
        chunk_metadata_json=built_chunk.chunk_metadata_json,
        qdrant_point_id=None,
        vector_backend=None,
        vector_point_id=None,
        embedded_at=None,
        embed_status="pending",
        embed_error=None,
    )


def _chunk_definition_is_stale(chunk: ContentChunk, built_chunk: BuiltChunk) -> bool:
    metadata = chunk.chunk_metadata_json
    built_metadata = built_chunk.chunk_metadata_json
    return (
        metadata.get("chunker_version") != built_metadata.get("chunker_version")
        or metadata.get("embed_text_hash") != built_metadata.get("embed_text_hash")
    )


def _apply_built_chunk_to_existing(chunk: ContentChunk, built_chunk: BuiltChunk) -> None:
    chunk.char_start = built_chunk.start_char
    chunk.char_end = built_chunk.end_char
    chunk.display_text = built_chunk.display_text
    chunk.embed_text = built_chunk.embed_text
    chunk.token_count = built_chunk.token_count
    chunk.chunk_metadata_json = built_chunk.chunk_metadata_json
    chunk.qdrant_point_id = None
    chunk.vector_backend = None
    chunk.vector_point_id = None
    chunk.embedded_at = None
    chunk.embed_status = "pending"
    chunk.embed_error = None


def _mark_chunk_obsolete(chunk: ContentChunk) -> ObsoleteVectorPoint | None:
    previous_backend = chunk.vector_backend
    previous_point_id = chunk.vector_point_id or chunk.qdrant_point_id
    previous_collection = _metadata_string(chunk.chunk_metadata_json, "vector_collection")
    previous_store_id = _metadata_string(chunk.chunk_metadata_json, "vector_store_id")

    chunk.embed_status = "obsolete"
    chunk.embed_error = "obsolete chunk"

    if previous_backend is None or previous_point_id is None:
        return None
    return ObsoleteVectorPoint(
        content_chunk=chunk,
        backend_name=previous_backend,
        point_id=previous_point_id,
        collection=previous_collection,
        store_id=previous_store_id,
    )


def _chunk_requires_indexing(
    chunk: ContentChunk,
    *,
    vector_target: VectorIndexTarget,
) -> bool:
    if chunk.embed_status == "obsolete":
        return False
    if chunk.embed_status in {"pending", "failed"}:
        return True
    if chunk.embed_status != "success":
        return True
    if chunk.vector_backend != vector_target.backend_name:
        return True

    metadata = chunk.chunk_metadata_json
    return any(
        metadata.get(key) != value for key, value in vector_target.metadata.items()
    )


async def _delete_obsolete_vector_points(
    *,
    session: AsyncSession,
    run_id: uuid.UUID,
    indexer: QdrantIndexer,
    obsolete_vector_points: list[ObsoleteVectorPoint],
) -> int:
    failed_count = 0
    for obsolete_vector_point in obsolete_vector_points:
        try:
            deletion_indexer = _deletion_indexer_for_obsolete_vector_point(
                active_indexer=indexer,
                obsolete_vector_point=obsolete_vector_point,
            )
            if deletion_indexer is None:
                continue
            deletion_indexer.delete_chunk(point_id=obsolete_vector_point.point_id)
            _mark_obsolete_vector_delete_success(obsolete_vector_point.content_chunk)
        except Exception as exc:
            failed_count += 1
            await _record_vector_delete_failure_event(
                session=session,
                run_id=run_id,
                content_chunk=obsolete_vector_point.content_chunk,
                error_message=_vector_delete_error_message(
                    point_id=obsolete_vector_point.point_id,
                    cause=_error_message(exc),
                ),
            )
    return failed_count


def _deletion_indexer_for_obsolete_vector_point(
    *,
    active_indexer: QdrantIndexer,
    obsolete_vector_point: ObsoleteVectorPoint,
) -> QdrantIndexer | None:
    if obsolete_vector_point.backend_name != active_indexer.backend_name:
        return None
    if obsolete_vector_point.store_id is not None:
        active_store_id = _vector_store_id(active_indexer.url)
        if obsolete_vector_point.store_id != active_store_id:
            return None
    if (
        obsolete_vector_point.collection is None
        or obsolete_vector_point.collection == active_indexer.collection
    ):
        return active_indexer
    return QdrantIndexer(
        url=active_indexer.url,
        collection=obsolete_vector_point.collection,
        embedding=active_indexer.embedding,
    )


async def _thread_title_for_item(
    session: AsyncSession, content_item: ContentItem
) -> str | None:
    if content_item.item_type == "thread":
        return content_item.title
    if content_item.item_type != "comment":
        return None

    related_ids = [content_item.thread_root_id, content_item.parent_item_id]
    for related_id in related_ids:
        if related_id is None or related_id == content_item.id:
            continue
        related_item = await session.get(ContentItem, related_id)
        if related_item is not None and related_item.title:
            return related_item.title
    return None


def _build_qdrant_indexer() -> QdrantIndexer:
    settings = get_settings()
    return QdrantIndexer(
        url=settings.qdrant_url,
        collection=settings.qdrant_collection,
        embedding=DeterministicEmbeddingService(),
    )


def _current_vector_index_target() -> VectorIndexTarget:
    settings = get_settings()
    embedding = DeterministicEmbeddingService()
    return VectorIndexTarget(
        backend_name=_vector_backend_name(settings.qdrant_url),
        metadata={
            "vector_collection": settings.qdrant_collection,
            "vector_store_id": _vector_store_id(settings.qdrant_url),
            "embedding_model": embedding.model_name,
            "embedding_dimension": embedding.dimension,
        },
    )


def _vector_index_target_from_indexer(
    indexer: QdrantIndexer,
    *,
    fallback: VectorIndexTarget,
) -> VectorIndexTarget:
    embedding = getattr(indexer, "embedding", None)
    return VectorIndexTarget(
        backend_name=indexer.backend_name,
        metadata={
            "vector_collection": _string_attribute(
                indexer,
                "collection",
                fallback=str(fallback.metadata["vector_collection"]),
            ),
            "vector_store_id": _vector_store_id_from_indexer(indexer, fallback=fallback),
            "embedding_model": _string_attribute(
                embedding,
                "model_name",
                fallback=str(fallback.metadata["embedding_model"]),
            ),
            "embedding_dimension": _int_attribute(
                embedding,
                "dimension",
                fallback=int(fallback.metadata["embedding_dimension"]),
            ),
        },
    )


def _vector_backend_name(url: str) -> Literal["memory", "qdrant"]:
    if url == ":memory:" or url.startswith("memory://"):
        return "memory"
    return "qdrant"


def _vector_store_id(url: str) -> str:
    return hashlib.sha256(url.encode("utf-8")).hexdigest()


def _vector_store_id_from_indexer(
    indexer: object,
    *,
    fallback: VectorIndexTarget,
) -> str:
    url = getattr(indexer, "url", None)
    if isinstance(url, str) and url.strip():
        return _vector_store_id(url.strip())
    fallback_store_id = fallback.metadata.get("vector_store_id")
    if isinstance(fallback_store_id, str) and fallback_store_id.strip():
        return fallback_store_id
    return _vector_store_id(get_settings().qdrant_url)


def _string_attribute(obj: object, name: str, *, fallback: str) -> str:
    value = getattr(obj, name, fallback)
    if isinstance(value, str) and value.strip():
        return value.strip()
    return fallback


def _int_attribute(obj: object, name: str, *, fallback: int) -> int:
    value = getattr(obj, name, fallback)
    if isinstance(value, int):
        return value
    return fallback


def _build_chunk_payload(
    *,
    content_item: ContentItem,
    chunk_id: uuid.UUID,
) -> dict[str, Any]:
    return {
        "chunk_id": str(chunk_id),
        "content_item_id": str(content_item.id),
        "source_site_id": str(content_item.source_site_id),
        "item_type": content_item.item_type,
        "canonical_url": content_item.canonical_url,
        "title": content_item.title,
        "author_id": str(content_item.author_id) if content_item.author_id else None,
        "published_at": (
            content_item.published_at.isoformat() if content_item.published_at else None
        ),
        "language": content_item.language,
        "source_section": _metadata_string(content_item.metadata_json, "source_section"),
        "thread_root_id": (
            str(content_item.thread_root_id) if content_item.thread_root_id else None
        ),
        "parent_item_id": (
            str(content_item.parent_item_id) if content_item.parent_item_id else None
        ),
        "tags": list(content_item.tags),
    }


def _metadata_string(metadata_json: dict[str, Any], key: str) -> str | None:
    value = metadata_json.get(key)
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


def _mark_chunk_success(
    *,
    content_chunk: ContentChunk,
    backend_name: str,
    point_id: str,
    index_metadata: VectorIndexMetadata,
) -> None:
    content_chunk.embed_status = "success"
    content_chunk.embed_error = None
    content_chunk.vector_backend = backend_name
    content_chunk.vector_point_id = point_id
    content_chunk.qdrant_point_id = point_id if backend_name == "qdrant" else None
    content_chunk.embedded_at = utcnow()
    content_chunk.chunk_metadata_json = {
        **content_chunk.chunk_metadata_json,
        **index_metadata,
    }


def _mark_obsolete_vector_delete_success(content_chunk: ContentChunk) -> None:
    content_chunk.qdrant_point_id = None
    content_chunk.vector_backend = None
    content_chunk.vector_point_id = None
    content_chunk.embedded_at = None
    content_chunk.chunk_metadata_json = {
        **content_chunk.chunk_metadata_json,
        "vector_cleanup_status": "deleted",
    }


def _mark_chunk_failed(
    *,
    content_chunk: ContentChunk,
    backend_name: str,
    error_message: str,
) -> None:
    content_chunk.embed_status = "failed"
    content_chunk.embed_error = error_message
    content_chunk.vector_backend = backend_name
    content_chunk.vector_point_id = None
    content_chunk.qdrant_point_id = None
    content_chunk.embedded_at = None


async def _record_vector_index_failure_event(
    *,
    session: AsyncSession,
    run_id: uuid.UUID,
    content_chunk: ContentChunk,
    error_message: str,
) -> None:
    await _record_vector_failure_event(
        session=session,
        run_id=run_id,
        content_chunk=content_chunk,
        error_message=error_message,
        event_type="vector_index_failed",
    )


async def _record_vector_delete_failure_event(
    *,
    session: AsyncSession,
    run_id: uuid.UUID,
    content_chunk: ContentChunk,
    error_message: str,
) -> None:
    await _record_vector_failure_event(
        session=session,
        run_id=run_id,
        content_chunk=content_chunk,
        error_message=error_message,
        event_type="vector_delete_failed",
    )


async def _record_vector_failure_event(
    *,
    session: AsyncSession,
    run_id: uuid.UUID,
    content_chunk: ContentChunk,
    error_message: str,
    event_type: str,
) -> None:
    content_item = await session.get(ContentItem, content_chunk.content_item_id)
    if content_item is None:
        return
    session.add(
        CrawlRunEvent(
            crawl_run_id=run_id,
            stage="index",
            level="error",
            event_type=event_type,
            message=error_message,
            related_url=content_item.canonical_url,
            related_raw_page_id=content_item.raw_page_id,
            related_content_item_id=content_chunk.content_item_id,
            counters_json={"chunk_id": str(content_chunk.id), "error_count_increment": 1},
            agent_trace_json={},
        )
    )


def _error_message(exc: Exception) -> str:
    message = str(exc) or exc.__class__.__name__
    return f"{exc.__class__.__name__}: {message}"[:2000]


def _vector_delete_error_message(*, point_id: str, cause: str) -> str:
    return f"Failed to delete obsolete vector point {point_id}: {cause}"[:2000]
