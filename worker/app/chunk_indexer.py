import uuid
from dataclasses import dataclass
from typing import Any, Literal

from app.core.config import get_settings
from app.db.base import utcnow
from app.models.content import ContentChunk, ContentItem
from app.models.control import CrawlRunEvent
from app.services.chunking import build_chunks
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


async def chunk_and_index_content_items(
    *,
    session: AsyncSession,
    run_id: uuid.UUID,
    content_item_ids: list[uuid.UUID],
) -> ChunkIndexingResult:
    unique_item_ids = list(dict.fromkeys(content_item_ids))
    vector_target = _current_vector_index_target()
    chunks_to_index: list[tuple[ContentChunk, ContentItem]] = []
    newly_created_chunk_count = 0

    for content_item_id in unique_item_ids:
        content_item = await session.get(ContentItem, content_item_id)
        if content_item is None:
            continue

        existing_chunks = await _load_chunks_for_item(session, content_item_id)
        if existing_chunks:
            for existing_chunk in _chunks_requiring_indexing(
                existing_chunks,
                vector_target=vector_target,
            ):
                chunks_to_index.append((existing_chunk, content_item))
            continue

        thread_title = await _thread_title_for_item(session, content_item)
        chunks_for_indexing, created_chunk_count = await _create_chunks_or_reload_existing(
            session=session,
            content_item=content_item,
            thread_title=thread_title,
            vector_target=vector_target,
        )
        for content_chunk in chunks_for_indexing:
            chunks_to_index.append((content_chunk, content_item))
        newly_created_chunk_count += created_chunk_count

    if not chunks_to_index:
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
        await session.flush()
        return ChunkIndexingResult(
            chunked_count=newly_created_chunk_count,
            embedded_count=0,
            failed_count=len(chunks_to_index),
        )

    embedded_count = 0
    failed_count = 0
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


def _chunks_requiring_indexing(
    chunks: list[ContentChunk],
    *,
    vector_target: VectorIndexTarget,
) -> list[ContentChunk]:
    return [
        chunk
        for chunk in chunks
        if _chunk_requires_indexing(chunk, vector_target=vector_target)
    ]


def _chunk_requires_indexing(
    chunk: ContentChunk,
    *,
    vector_target: VectorIndexTarget,
) -> bool:
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


async def _create_chunks_or_reload_existing(
    *,
    session: AsyncSession,
    content_item: ContentItem,
    thread_title: str | None,
    vector_target: VectorIndexTarget,
) -> tuple[list[ContentChunk], int]:
    built_chunks = build_chunks(
        item_type=content_item.item_type,
        title=content_item.title,
        cleaned_text=content_item.cleaned_text,
        summary_text=content_item.summary_text,
        tags=content_item.tags,
        thread_title=thread_title,
    )
    new_chunks = [
        ContentChunk(
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
        for built_chunk in built_chunks
    ]

    try:
        async with session.begin_nested():
            for content_chunk in new_chunks:
                session.add(content_chunk)
            await session.flush()
    except IntegrityError:
        existing_chunks = await _load_chunks_for_item(session, content_item.id)
        if not existing_chunks:
            raise
        return _chunks_requiring_indexing(
            existing_chunks,
            vector_target=vector_target,
        ), 0

    return new_chunks, len(new_chunks)


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
    content_item = await session.get(ContentItem, content_chunk.content_item_id)
    if content_item is None:
        return
    session.add(
        CrawlRunEvent(
            crawl_run_id=run_id,
            stage="index",
            level="error",
            event_type="vector_index_failed",
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
