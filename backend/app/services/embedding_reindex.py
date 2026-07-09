from __future__ import annotations

import hashlib
from dataclasses import asdict, dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.base import utcnow
from app.models.content import ContentChunk, ContentItem
from app.services.embeddings import EmbeddingService
from app.services.retrieval import QdrantIndexer

MAX_EMBED_ERROR_CHARS = 1000


@dataclass(frozen=True)
class ReindexSummary:
    total: int
    succeeded: int
    failed: int
    provider: str
    model: str
    dimension: int
    collection: str
    backend_name: str
    reset_collection: bool

    def to_json_dict(self) -> dict[str, Any]:
        return asdict(self)


async def reindex_embeddings(
    session: AsyncSession,
    *,
    qdrant_url: str,
    collection: str,
    embedding: EmbeddingService,
    reset_collection: bool,
) -> ReindexSummary:
    indexer = QdrantIndexer(url=qdrant_url, collection=collection, embedding=embedding)
    if reset_collection:
        indexer.recreate_collection()
    else:
        indexer.ensure_collection()

    rows = (
        await session.execute(
            select(ContentChunk, ContentItem)
            .join(ContentItem, ContentChunk.content_item_id == ContentItem.id)
            .where(ContentChunk.embed_status != "obsolete")
            .order_by(ContentChunk.id.asc())
        )
    ).all()

    succeeded = 0
    failed = 0
    for content_chunk, content_item in rows:
        try:
            point_id = indexer.upsert_chunk(
                chunk_id=content_chunk.id,
                embed_text=content_chunk.embed_text,
                payload=_build_payload(content_chunk=content_chunk, content_item=content_item),
            )
            _mark_success(
                content_chunk=content_chunk,
                point_id=point_id,
                backend_name=indexer.backend_name,
                embedding=embedding,
                collection=collection,
                vector_store_id=_vector_store_id(qdrant_url),
            )
            succeeded += 1
        except Exception as exc:  # noqa: BLE001 - per-chunk failure must be recorded.
            _mark_failed(
                content_chunk=content_chunk,
                backend_name=indexer.backend_name,
                error_message=str(exc),
            )
            failed += 1

    await session.commit()
    return ReindexSummary(
        total=len(rows),
        succeeded=succeeded,
        failed=failed,
        provider=_provider_name(embedding),
        model=embedding.model_name,
        dimension=embedding.dimension,
        collection=collection,
        backend_name=indexer.backend_name,
        reset_collection=reset_collection,
    )


def _build_payload(*, content_chunk: ContentChunk, content_item: ContentItem) -> dict[str, Any]:
    return {
        "chunk_id": str(content_chunk.id),
        "content_item_id": str(content_item.id),
        "source_site_id": str(content_item.source_site_id),
        "item_type": content_item.item_type,
        "canonical_url": content_item.canonical_url,
        "title": content_item.title,
        "published_at": (
            content_item.published_at.isoformat() if content_item.published_at else None
        ),
        "language": content_item.language,
        "tags": list(content_item.tags),
    }


def _mark_success(
    *,
    content_chunk: ContentChunk,
    point_id: str,
    backend_name: str,
    embedding: EmbeddingService,
    collection: str,
    vector_store_id: str,
) -> None:
    content_chunk.embed_status = "success"
    content_chunk.embed_error = None
    content_chunk.vector_backend = backend_name
    content_chunk.vector_point_id = point_id
    content_chunk.qdrant_point_id = point_id if backend_name == "qdrant" else None
    content_chunk.embedded_at = utcnow()
    content_chunk.chunk_metadata_json = {
        **content_chunk.chunk_metadata_json,
        "vector_collection": collection,
        "vector_store_id": vector_store_id,
        "embedding_model": embedding.model_name,
        "embedding_dimension": embedding.dimension,
    }


def _vector_store_id(url: str) -> str:
    return hashlib.sha256(url.encode("utf-8")).hexdigest()


def _mark_failed(*, content_chunk: ContentChunk, backend_name: str, error_message: str) -> None:
    content_chunk.embed_status = "failed"
    content_chunk.embed_error = error_message[:MAX_EMBED_ERROR_CHARS]
    content_chunk.vector_backend = backend_name
    content_chunk.vector_point_id = None
    content_chunk.qdrant_point_id = None
    content_chunk.embedded_at = None


def _provider_name(embedding: EmbeddingService) -> str:
    class_name = embedding.__class__.__name__
    if class_name == "SentenceTransformerEmbeddingService":
        return "sentence-transformers"
    return class_name
