import logging
import math
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Literal
from typing import cast as typing_cast
from uuid import UUID

from qdrant_client import QdrantClient
from qdrant_client.http import models as qdrant_models
from sqlalchemy import String, or_, select
from sqlalchemy import cast as sql_cast
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from app.models.content import Author, ContentChunk, ContentItem
from app.models.control import SourceSite
from app.schemas.search import EvidenceObject, SearchFilters
from app.services.embeddings import EmbeddingService

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class VectorSearchHit:
    chunk_id: UUID
    content_item_id: UUID | None
    score: float


@dataclass(frozen=True)
class _SearchHit:
    chunk_id: UUID
    content_item_id: UUID | None
    score: float
    matched_by: Literal["vector", "keyword", "hybrid"]


@dataclass
class _MemoryPoint:
    vector: list[float]
    payload: dict[str, Any]


@dataclass
class _MemoryCollection:
    dimension: int
    points: dict[str, _MemoryPoint] = field(default_factory=dict)


_MEMORY_COLLECTIONS: dict[tuple[str, str], _MemoryCollection] = {}
_TERM_RE = re.compile(r"[\w-]+", re.UNICODE)


class QdrantIndexer:
    """Index content chunks into Qdrant or an explicit in-memory dev/test backend.

    The ``memory://``/``:memory:`` backend is opt-in for local development and tests only.
    Real Qdrant connection or HTTP failures intentionally propagate to callers; they are
    not automatically downgraded to memory mode so ingestion can mark affected chunks
    failed and finish the crawl run as partial instead of silently losing vector writes.
    """

    def __init__(self, url: str, collection: str, embedding: EmbeddingService) -> None:
        self.url = url
        self.collection = collection
        self.embedding = embedding
        self.backend_name: Literal["memory", "qdrant"] = (
            "memory" if _is_memory_url(url) else "qdrant"
        )
        self._client: QdrantClient | None = None
        if self.backend_name == "qdrant":
            self._client = QdrantClient(url=url)

    def ensure_collection(self) -> None:
        if self.backend_name == "memory":
            key = (self.url, self.collection)
            existing = _MEMORY_COLLECTIONS.get(key)
            if existing is not None and existing.dimension != self.embedding.dimension:
                raise ValueError(
                    "Memory collection dimension mismatch: "
                    f"expected {existing.dimension}, got {self.embedding.dimension}"
                )
            _MEMORY_COLLECTIONS.setdefault(
                key, _MemoryCollection(dimension=self.embedding.dimension)
            )
            return

        client = self._require_client()
        if not client.collection_exists(collection_name=self.collection):
            client.create_collection(
                collection_name=self.collection,
                vectors_config=qdrant_models.VectorParams(
                    size=self.embedding.dimension,
                    distance=qdrant_models.Distance.COSINE,
                ),
            )

    def upsert_chunk(self, *, chunk_id: UUID, embed_text: str, payload: dict[str, Any]) -> str:
        point_id = str(chunk_id)
        vector = self.embedding.embed(embed_text)
        point_payload = {**payload, "chunk_id": point_id}

        if self.backend_name == "memory":
            collection = _MEMORY_COLLECTIONS.setdefault(
                (self.url, self.collection),
                _MemoryCollection(dimension=self.embedding.dimension),
            )
            if collection.dimension != len(vector):
                raise ValueError(
                    "Memory collection dimension mismatch: "
                    f"expected {collection.dimension}, got {len(vector)}"
                )
            collection.points[point_id] = _MemoryPoint(vector=vector, payload=point_payload)
            return point_id

        self._require_client().upsert(
            collection_name=self.collection,
            points=[
                qdrant_models.PointStruct(
                    id=point_id,
                    vector=vector,
                    payload=point_payload,
                )
            ],
            wait=True,
        )
        return point_id

    def search_chunks(
        self,
        *,
        query_text: str,
        filters: dict[str, Any],
        top_k: int,
    ) -> list[VectorSearchHit]:
        if top_k < 1 or not query_text.strip():
            return []

        query_vector = self.embedding.embed(query_text)
        if self.backend_name == "memory":
            return self._search_memory(query_vector=query_vector, filters=filters, top_k=top_k)

        scored_points = self._require_client().search(
            collection_name=self.collection,
            query_vector=query_vector,
            query_filter=_qdrant_filter(filters),
            limit=top_k,
            with_payload=True,
            with_vectors=False,
            timeout=1,
        )
        hits: list[VectorSearchHit] = []
        for point in scored_points:
            payload = point.payload or {}
            chunk_id = _uuid_from_payload(payload, "chunk_id")
            if chunk_id is None:
                continue
            hits.append(
                VectorSearchHit(
                    chunk_id=chunk_id,
                    content_item_id=_uuid_from_payload(payload, "content_item_id"),
                    score=float(point.score),
                )
            )
        return hits

    def delete_chunk(self, *, point_id: str) -> None:
        if self.backend_name == "memory":
            collection = _MEMORY_COLLECTIONS.get((self.url, self.collection))
            if collection is not None:
                collection.points.pop(point_id, None)
            return

        self._require_client().delete(
            collection_name=self.collection,
            points_selector=qdrant_models.PointIdsList(points=[point_id]),
            wait=True,
        )

    def _search_memory(
        self,
        *,
        query_vector: Sequence[float],
        filters: dict[str, Any],
        top_k: int,
    ) -> list[VectorSearchHit]:
        collection = _MEMORY_COLLECTIONS.get((self.url, self.collection))
        if collection is None:
            return []

        hits: list[VectorSearchHit] = []
        for point_id, point in collection.points.items():
            if not _memory_payload_matches(point.payload, filters):
                continue
            chunk_id = _uuid_from_payload(point.payload, "chunk_id") or _uuid_from_str(point_id)
            if chunk_id is None:
                continue
            hits.append(
                VectorSearchHit(
                    chunk_id=chunk_id,
                    content_item_id=_uuid_from_payload(point.payload, "content_item_id"),
                    score=_cosine_similarity(query_vector, point.vector),
                )
            )

        return sorted(hits, key=lambda hit: hit.score, reverse=True)[:top_k]

    def _require_client(self) -> QdrantClient:
        if self._client is None:
            raise RuntimeError("Qdrant client is not available for the memory backend")
        return self._client


async def retrieve_evidence(
    session: AsyncSession,
    *,
    raw_query: str,
    optimized_query_text: str,
    keyword_terms: list[str],
    filters: SearchFilters,
    top_k: int,
    qdrant_url: str,
    qdrant_collection: str,
    embedding: EmbeddingService,
) -> list[EvidenceObject]:
    vector_hits: list[VectorSearchHit] = []
    try:
        vector_hits = QdrantIndexer(
            url=qdrant_url,
            collection=qdrant_collection,
            embedding=embedding,
        ).search_chunks(
            query_text=optimized_query_text,
            filters=_vector_filter_payload(filters),
            top_k=max(top_k * 2, top_k),
        )
    except Exception as exc:  # pragma: no cover - exact Qdrant failures are environment-specific.
        logger.info("Vector search unavailable; continuing with SQL keyword retrieval: %s", exc)

    keyword_hits = await _keyword_search(
        session,
        raw_query=raw_query,
        keyword_terms=keyword_terms,
        filters=filters,
        limit=max(top_k * 10, 50),
    )
    merged_hits = _merge_hits(vector_hits=vector_hits, keyword_hits=keyword_hits)
    return await _hydrate_and_rank_evidence(
        session,
        hits=merged_hits,
        filters=filters,
        top_k=top_k,
    )


async def _keyword_search(
    session: AsyncSession,
    *,
    raw_query: str,
    keyword_terms: list[str],
    filters: SearchFilters,
    limit: int,
) -> list[_SearchHit]:
    terms = _normalized_terms(keyword_terms=keyword_terms, raw_query=raw_query)
    if not terms:
        return []

    term_clauses = []
    for term in terms:
        pattern = _ilike_pattern(term)
        term_clauses.extend(
            [
                ContentItem.title.ilike(pattern, escape="\\"),
                ContentItem.summary_text.ilike(pattern, escape="\\"),
                ContentItem.cleaned_text.ilike(pattern, escape="\\"),
                sql_cast(ContentItem.tags, String).ilike(pattern, escape="\\"),
            ]
        )

    stmt = (
        select(ContentChunk, ContentItem)
        .join(ContentItem, ContentChunk.content_item_id == ContentItem.id)
        .where(*_content_filter_clauses(filters), or_(*term_clauses))
        .order_by(ContentItem.created_at.desc(), ContentChunk.chunk_index.asc())
        .limit(limit)
    )
    rows = (await session.execute(stmt)).all()

    hits: list[_SearchHit] = []
    for content_chunk, content_item in rows:
        score = _keyword_score(content_item, terms)
        if score >= 0.20:
            hits.append(
                _SearchHit(
                    chunk_id=content_chunk.id,
                    content_item_id=content_item.id,
                    score=score,
                    matched_by="keyword",
                )
            )
    return hits


def _merge_hits(
    *,
    vector_hits: list[VectorSearchHit],
    keyword_hits: list[_SearchHit],
) -> list[_SearchHit]:
    merged: dict[tuple[UUID | None, UUID], _SearchHit] = {}

    for vector_hit in vector_hits:
        _merge_hit(
            merged,
            _SearchHit(
                chunk_id=vector_hit.chunk_id,
                content_item_id=vector_hit.content_item_id,
                score=vector_hit.score,
                matched_by="vector",
            ),
        )
    for keyword_hit in keyword_hits:
        _merge_hit(merged, keyword_hit)

    return list(merged.values())


def _merge_hit(merged: dict[tuple[UUID | None, UUID], _SearchHit], incoming: _SearchHit) -> None:
    key = (incoming.content_item_id, incoming.chunk_id)
    existing = merged.get(key)
    if existing is None:
        merged[key] = incoming
        return

    if existing.matched_by == incoming.matched_by:
        merged[key] = _SearchHit(
            chunk_id=incoming.chunk_id,
            content_item_id=incoming.content_item_id,
            score=max(existing.score, incoming.score),
            matched_by=existing.matched_by,
        )
        return

    merged[key] = _SearchHit(
        chunk_id=incoming.chunk_id,
        content_item_id=incoming.content_item_id,
        score=max(existing.score, incoming.score) + 0.1,
        matched_by="hybrid",
    )


async def _hydrate_and_rank_evidence(
    session: AsyncSession,
    *,
    hits: list[_SearchHit],
    filters: SearchFilters,
    top_k: int,
) -> list[EvidenceObject]:
    if not hits:
        return []

    chunk_ids = [hit.chunk_id for hit in hits]
    thread_root = aliased(ContentItem)
    stmt = (
        select(
            ContentChunk,
            ContentItem,
            SourceSite.name,
            Author.display_name,
            thread_root.summary_text,
        )
        .join(ContentItem, ContentChunk.content_item_id == ContentItem.id)
        .join(SourceSite, ContentItem.source_site_id == SourceSite.id)
        .outerjoin(Author, ContentItem.author_id == Author.id)
        .outerjoin(thread_root, ContentItem.thread_root_id == thread_root.id)
        .where(ContentChunk.id.in_(chunk_ids), *_content_filter_clauses(filters))
    )
    rows = (await session.execute(stmt)).all()
    row_by_chunk_id = {row[0].id: row for row in rows}

    evidence: list[EvidenceObject] = []
    for hit in hits:
        row = row_by_chunk_id.get(hit.chunk_id)
        if row is None:
            continue
        content_chunk = typing_cast(ContentChunk, row[0])
        content_item = typing_cast(ContentItem, row[1])
        source_site_name = typing_cast(str, row[2])
        author_name = typing_cast(str | None, row[3])
        thread_summary = typing_cast(str | None, row[4])
        if hit.content_item_id is not None and hit.content_item_id != content_item.id:
            continue
        evidence.append(
            EvidenceObject(
                chunk_id=content_chunk.id,
                content_item_id=content_item.id,
                title=content_item.title,
                snippet=_snippet(content_chunk.display_text),
                canonical_url=content_item.canonical_url,
                source_site_name=source_site_name,
                author_name=author_name,
                published_at=content_item.published_at,
                item_type=content_item.item_type,
                score=hit.score,
                matched_by=hit.matched_by,
                thread_summary=thread_summary,
            )
        )

    evidence.sort(key=lambda item: (-item.score, str(item.chunk_id)))
    return evidence[:top_k]


def _keyword_score(content_item: ContentItem, terms: Sequence[str]) -> float:
    score = 0.0
    tags = [tag.casefold() for tag in content_item.tags]
    for term in terms:
        normalized = term.casefold()
        if content_item.title is not None and normalized in content_item.title.casefold():
            score += 0.50
        if (
            content_item.summary_text is not None
            and normalized in content_item.summary_text.casefold()
        ):
            score += 0.30
        if normalized in content_item.cleaned_text.casefold():
            score += 0.20
        if any(normalized in tag for tag in tags):
            score += 0.10

    if score > 0.0:
        return max(score, 0.20)
    return 0.0


def _content_filter_clauses(filters: SearchFilters) -> list[Any]:
    clauses: list[Any] = []
    if filters.source_site_id is not None:
        clauses.append(ContentItem.source_site_id == filters.source_site_id)
    if filters.item_type is not None:
        clauses.append(ContentItem.item_type == filters.item_type)
    if filters.language is not None:
        clauses.append(ContentItem.language == filters.language)
    if filters.tags:
        clauses.append(ContentItem.tags.contains(filters.tags))
    if filters.published_after is not None:
        clauses.append(ContentItem.published_at >= filters.published_after)
    if filters.published_before is not None:
        clauses.append(ContentItem.published_at <= filters.published_before)
    return clauses


def _vector_filter_payload(filters: SearchFilters) -> dict[str, Any]:
    payload: dict[str, Any] = {}
    if filters.source_site_id is not None:
        payload["source_site_id"] = str(filters.source_site_id)
    if filters.item_type is not None:
        payload["item_type"] = filters.item_type
    if filters.language is not None:
        payload["language"] = filters.language
    if filters.tags:
        payload["tags"] = list(filters.tags)
    return payload


def _qdrant_filter(filters: dict[str, Any]) -> qdrant_models.Filter | None:
    conditions: list[qdrant_models.Condition] = []
    for key in ("source_site_id", "item_type", "language"):
        value = filters.get(key)
        if isinstance(value, str) and value:
            conditions.append(
                qdrant_models.FieldCondition(
                    key=key,
                    match=qdrant_models.MatchValue(value=value),
                )
            )
    tags = filters.get("tags")
    if isinstance(tags, list):
        for tag in tags:
            if isinstance(tag, str) and tag:
                conditions.append(
                    qdrant_models.FieldCondition(
                        key="tags",
                        match=qdrant_models.MatchValue(value=tag),
                    )
                )
    if not conditions:
        return None
    return qdrant_models.Filter(must=conditions)


def _memory_payload_matches(payload: Mapping[str, Any], filters: Mapping[str, Any]) -> bool:
    for key in ("source_site_id", "item_type", "language"):
        expected = filters.get(key)
        if expected is not None and payload.get(key) != expected:
            return False
    expected_tags = filters.get("tags")
    if isinstance(expected_tags, list) and expected_tags:
        payload_tags = payload.get("tags")
        if not isinstance(payload_tags, list):
            return False
        if not all(tag in payload_tags for tag in expected_tags):
            return False
    return True


def _normalized_terms(*, keyword_terms: Sequence[str], raw_query: str) -> list[str]:
    candidates: list[str] = []
    candidates.extend(keyword_terms)
    candidates.extend(_TERM_RE.findall(raw_query))

    terms: list[str] = []
    seen: set[str] = set()
    for candidate in candidates:
        term = candidate.strip().casefold()
        if not term or term in seen:
            continue
        seen.add(term)
        terms.append(term)
    return terms


def _ilike_pattern(term: str) -> str:
    escaped = term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


def _snippet(text: str, max_chars: int = 300) -> str:
    collapsed = " ".join(text.split())
    if len(collapsed) <= max_chars:
        return collapsed
    return f"{collapsed[: max_chars - 1].rstrip()}…"


def _uuid_from_payload(payload: Mapping[str, Any], key: str) -> UUID | None:
    value = payload.get(key)
    if not isinstance(value, str):
        return None
    return _uuid_from_str(value)


def _uuid_from_str(value: str) -> UUID | None:
    try:
        return UUID(value)
    except ValueError:
        return None


def _cosine_similarity(left: Sequence[float], right: Sequence[float]) -> float:
    if len(left) != len(right):
        return 0.0
    left_norm = math.sqrt(sum(value * value for value in left))
    right_norm = math.sqrt(sum(value * value for value in right))
    if left_norm == 0.0 or right_norm == 0.0:
        return 0.0
    dot_product = sum(
        left_value * right_value for left_value, right_value in zip(left, right, strict=False)
    )
    return dot_product / (left_norm * right_norm)


def _is_memory_url(url: str) -> bool:
    return url == ":memory:" or url.startswith("memory://")
