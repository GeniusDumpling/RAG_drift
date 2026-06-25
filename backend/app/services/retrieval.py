import inspect
import logging
import math
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Literal
from typing import cast as typing_cast
from uuid import UUID

from qdrant_client import QdrantClient
from qdrant_client.http import models as qdrant_models
from sqlalchemy import String, case, func, literal, or_, select
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
class RetrievalResult:
    evidence: list[EvidenceObject]
    trace: dict[str, Any]


@dataclass(frozen=True)
class _SearchHit:
    chunk_id: UUID
    content_item_id: UUID | None
    score: float
    matched_by: Literal["vector", "keyword", "hybrid"]
    vector_score: float | None = None
    keyword_score: float | None = None


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
_OBSOLETE_EMBED_STATUS = "obsolete"
DEFAULT_VECTOR_SCORE_THRESHOLD = 0.20
_KEYWORD_MINIMUM_MATCH_SCORE = 0.20


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

    def recreate_collection(self) -> None:
        if self.backend_name == "memory":
            _MEMORY_COLLECTIONS[(self.url, self.collection)] = _MemoryCollection(
                dimension=self.embedding.dimension
            )
            return

        client = self._require_client()
        if client.collection_exists(collection_name=self.collection):
            client.delete_collection(collection_name=self.collection)
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
        score_threshold: float = DEFAULT_VECTOR_SCORE_THRESHOLD,
    ) -> list[VectorSearchHit]:
        if top_k < 1 or not query_text.strip():
            return []

        query_vector = self.embedding.embed(query_text)
        if self.backend_name == "memory":
            return self._search_memory(
                query_vector=query_vector,
                filters=filters,
                top_k=top_k,
                score_threshold=score_threshold,
            )

        client = self._require_client()
        search_kwargs: dict[str, Any] = {
            "collection_name": self.collection,
            "query_vector": query_vector,
            "query_filter": _qdrant_filter(filters),
            "limit": top_k,
            "with_payload": True,
            "with_vectors": False,
            "timeout": 1,
        }
        if _qdrant_search_supports_score_threshold(client.search):
            search_kwargs["score_threshold"] = score_threshold
        scored_points = client.search(**search_kwargs)
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
        score_threshold: float,
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
            score = _cosine_similarity(query_vector, point.vector)
            if not _vector_score_meets_threshold(score, score_threshold):
                continue
            hits.append(
                VectorSearchHit(
                    chunk_id=chunk_id,
                    content_item_id=_uuid_from_payload(point.payload, "content_item_id"),
                    score=score,
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
) -> RetrievalResult:
    vector_hits: list[VectorSearchHit] = []
    vector_trace: dict[str, Any] = {
        "attempted": False,
        "failed": False,
        "error": None,
        "hit_count": 0,
    }
    try:
        vector_trace["attempted"] = True
        vector_hits = QdrantIndexer(
            url=qdrant_url,
            collection=qdrant_collection,
            embedding=embedding,
        ).search_chunks(
            query_text=optimized_query_text,
            filters=_vector_filter_payload(filters),
            top_k=max(top_k * 2, top_k),
        )
        vector_trace["hit_count"] = len(vector_hits)
    except Exception as exc:  # pragma: no cover - exact Qdrant failures are environment-specific.
        vector_trace["failed"] = True
        vector_trace["error"] = str(exc)
        logger.info("Vector search unavailable; continuing with SQL keyword retrieval: %s", exc)

    snippet_terms = _normalized_terms(keyword_terms=keyword_terms, raw_query=raw_query)
    keyword_hits = await _keyword_search(
        session,
        raw_query=raw_query,
        keyword_terms=keyword_terms,
        filters=filters,
        limit=max(top_k * 10, 50),
    )
    merged_hits = _sort_hits_by_score(
        _merge_hits(vector_hits=vector_hits, keyword_hits=keyword_hits)
    )
    evidence = await _hydrate_and_rank_evidence(
        session,
        hits=merged_hits,
        filters=filters,
        top_k=top_k,
        snippet_terms=snippet_terms,
    )
    if filters.item_type == "video_description":
        evidence = _deduplicate_video_evidence(evidence, top_k)
    return RetrievalResult(
        evidence=evidence,
        trace={
            "vector": vector_trace,
            "keyword": {"hit_count": len(keyword_hits)},
            "merged_hit_count": len(merged_hits),
            "hydrated_evidence_count": len(evidence),
            "component_scores": [
                _hit_trace_summary(hit) for hit in merged_hits[: max(top_k * 2, top_k)]
            ],
        },
    )


def _deduplicate_video_evidence(
    evidence: list[EvidenceObject], top_k: int
) -> list[EvidenceObject]:
    """Deduplicate video evidence by content_item_id, keeping the best-scoring chunk.

    For video_description results, multiple chunks from the same video can match.
    This collapses them into a single evidence entry per video, preserving the
    highest-scoring chunk's score and showing the full video_url and description.

    Also filters out videos whose description indicates they are unrelated to
    the intended analysis topic (e.g. VLM noted "视频内容与无人机无关"), so they
    won't pollute retrieval results with high-scoring but irrelevant matches.
    """
    seen: set[UUID] = set()
    deduped: list[EvidenceObject] = []
    for item in evidence:
        if item.content_item_id not in seen:
            # Skip videos VLM explicitly flagged as unrelated
            if item.description_text and item.description_text.startswith(
                "视频内容与无人机无关"
            ):
                continue
            seen.add(item.content_item_id)
            deduped.append(item)
    return deduped[:top_k]


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
                ContentChunk.display_text.ilike(pattern, escape="\\"),
                ContentChunk.embed_text.ilike(pattern, escape="\\"),
                sql_cast(ContentItem.tags, String).ilike(pattern, escape="\\"),
            ]
        )

    candidate_limit = _keyword_candidate_limit(limit)
    keyword_score_expr = _keyword_score_sql_expression(terms)
    stmt = (
        select(ContentChunk, ContentItem, keyword_score_expr.label("keyword_score"))
        .join(ContentItem, ContentChunk.content_item_id == ContentItem.id)
        .where(
            _searchable_chunk_predicate(),
            *_content_filter_clauses(filters),
            or_(*term_clauses),
        )
        .order_by(
            keyword_score_expr.desc(),
            ContentItem.created_at.desc(),
            ContentChunk.chunk_index.asc(),
            ContentChunk.id.asc(),
        )
        .limit(candidate_limit)
    )
    rows = (await session.execute(stmt)).all()

    scored_hits: list[tuple[_SearchHit, float, int, str]] = []
    for content_chunk, content_item, _sql_keyword_score in rows:
        score = _normalize_keyword_score(_keyword_score(content_item, content_chunk, terms))
        if score >= _KEYWORD_MINIMUM_MATCH_SCORE:
            scored_hits.append(
                (
                    _SearchHit(
                        chunk_id=content_chunk.id,
                        content_item_id=content_item.id,
                        score=score,
                        matched_by="keyword",
                        keyword_score=score,
                    ),
                    _datetime_sort_value(content_item.created_at),
                    content_chunk.chunk_index,
                    str(content_chunk.id),
                )
            )
    scored_hits.sort(key=lambda item: (-item[0].score, -item[1], item[2], item[3]))
    return [hit for hit, _, _, _ in scored_hits[:limit]]


def _merge_hits(
    *,
    vector_hits: list[VectorSearchHit],
    keyword_hits: list[_SearchHit],
) -> list[_SearchHit]:
    merged: dict[UUID, _SearchHit] = {}

    for vector_hit in vector_hits:
        if not _vector_score_meets_threshold(vector_hit.score):
            continue
        score = _normalize_vector_score(vector_hit.score)
        _merge_hit(
            merged,
            _SearchHit(
                chunk_id=vector_hit.chunk_id,
                content_item_id=vector_hit.content_item_id,
                score=score,
                matched_by="vector",
                vector_score=score,
            ),
        )
    for keyword_hit in keyword_hits:
        _merge_hit(merged, keyword_hit)

    return list(merged.values())


def _sort_hits_by_score(hits: list[_SearchHit]) -> list[_SearchHit]:
    return sorted(hits, key=lambda hit: hit.score, reverse=True)


def _merge_hit(merged: dict[UUID, _SearchHit], incoming: _SearchHit) -> None:
    incoming = _normalize_search_hit(incoming)
    key = incoming.chunk_id
    existing = merged.get(key)
    if existing is None:
        merged[key] = incoming
        return

    content_item_id = _preferred_content_item_id(existing, incoming)
    vector_score = _max_optional(existing.vector_score, incoming.vector_score)
    keyword_score = _max_optional(existing.keyword_score, incoming.keyword_score)
    if existing.matched_by == incoming.matched_by:
        merged[key] = _SearchHit(
            chunk_id=incoming.chunk_id,
            content_item_id=content_item_id,
            score=max(existing.score, incoming.score),
            matched_by=existing.matched_by,
            vector_score=vector_score,
            keyword_score=keyword_score,
        )
        return

    merged[key] = _SearchHit(
        chunk_id=incoming.chunk_id,
        content_item_id=content_item_id,
        score=_hybrid_score(vector_score=vector_score, keyword_score=keyword_score),
        matched_by="hybrid",
        vector_score=vector_score,
        keyword_score=keyword_score,
    )


def _preferred_content_item_id(existing: _SearchHit, incoming: _SearchHit) -> UUID | None:
    if existing.matched_by == "keyword" and existing.content_item_id is not None:
        return existing.content_item_id
    if incoming.matched_by == "keyword" and incoming.content_item_id is not None:
        return incoming.content_item_id
    return existing.content_item_id or incoming.content_item_id


def _normalize_search_hit(hit: _SearchHit) -> _SearchHit:
    if hit.matched_by == "vector":
        vector_score = _normalize_vector_score(
            hit.vector_score if hit.vector_score is not None else hit.score
        )
        return _SearchHit(
            chunk_id=hit.chunk_id,
            content_item_id=hit.content_item_id,
            score=vector_score,
            matched_by="vector",
            vector_score=vector_score,
            keyword_score=hit.keyword_score,
        )
    if hit.matched_by == "keyword":
        keyword_score = _normalize_keyword_score(
            hit.keyword_score if hit.keyword_score is not None else hit.score
        )
        return _SearchHit(
            chunk_id=hit.chunk_id,
            content_item_id=hit.content_item_id,
            score=keyword_score,
            matched_by="keyword",
            vector_score=hit.vector_score,
            keyword_score=keyword_score,
        )

    hybrid_vector_score: float | None = (
        _normalize_vector_score(hit.vector_score) if hit.vector_score is not None else None
    )
    hybrid_keyword_score: float | None = (
        _normalize_keyword_score(hit.keyword_score) if hit.keyword_score is not None else None
    )
    score = (
        _hybrid_score(vector_score=hybrid_vector_score, keyword_score=hybrid_keyword_score)
        if hybrid_vector_score is not None or hybrid_keyword_score is not None
        else _clamp_unit_score(hit.score)
    )
    return _SearchHit(
        chunk_id=hit.chunk_id,
        content_item_id=hit.content_item_id,
        score=score,
        matched_by="hybrid",
        vector_score=hybrid_vector_score,
        keyword_score=hybrid_keyword_score,
    )


def _normalize_vector_score(score: float) -> float:
    return _clamp_unit_score(score)


def _vector_score_meets_threshold(
    score: float | None,
    threshold: float = DEFAULT_VECTOR_SCORE_THRESHOLD,
) -> bool:
    return score is not None and not math.isnan(score) and score >= threshold


def _normalize_keyword_score(score: float) -> float:
    return _clamp_unit_score(score)


def _clamp_unit_score(score: float) -> float:
    if math.isnan(score):
        return 0.0
    return min(max(score, 0.0), 1.0)


def _hybrid_score(*, vector_score: float | None, keyword_score: float | None) -> float:
    component_scores = [score for score in (vector_score, keyword_score) if score is not None]
    if not component_scores:
        return 0.0
    return min(max(component_scores) + 0.1, 1.0)


def _max_optional(left: float | None, right: float | None) -> float | None:
    if left is None:
        return right
    if right is None:
        return left
    return max(left, right)


def _keyword_candidate_limit(limit: int) -> int:
    return min(max(limit * 20, 500), 1000)


def _keyword_score_sql_expression(terms: Sequence[str]) -> Any:
    score_expr: Any = literal(0.0)
    for term in terms:
        pattern = _ilike_pattern(term)
        score_expr = (
            score_expr
            + case(
                (ContentItem.title.ilike(pattern, escape="\\"), 0.50),
                else_=0.0,
            )
            + case(
                (ContentItem.summary_text.ilike(pattern, escape="\\"), 0.30),
                else_=0.0,
            )
            + case(
                (
                    or_(
                        ContentChunk.display_text.ilike(pattern, escape="\\"),
                        ContentChunk.embed_text.ilike(pattern, escape="\\"),
                    ),
                    0.20,
                ),
                else_=0.0,
            )
            + case(
                (sql_cast(ContentItem.tags, String).ilike(pattern, escape="\\"), 0.10),
                else_=0.0,
            )
        )

    return case(
        (
            score_expr > 0.0,
            func.least(func.greatest(score_expr, _KEYWORD_MINIMUM_MATCH_SCORE), 1.0),
        ),
        else_=0.0,
    )


def _datetime_sort_value(value: datetime) -> float:
    return _as_utc(value).timestamp()


def _hit_trace_summary(hit: _SearchHit) -> dict[str, Any]:
    return {
        "chunk_id": str(hit.chunk_id),
        "matched_by": hit.matched_by,
        "score": hit.score,
        "vector_score": hit.vector_score,
        "keyword_score": hit.keyword_score,
    }


async def _hydrate_and_rank_evidence(
    session: AsyncSession,
    *,
    hits: list[_SearchHit],
    filters: SearchFilters,
    top_k: int,
    snippet_terms: Sequence[str],
) -> list[EvidenceObject]:
    if not hits:
        return []

    hits = [hit for hit in hits if _hit_has_acceptable_evidence_score(hit)]
    if not hits:
        return []

    chunk_ids = [hit.chunk_id for hit in hits]
    hit_rank_by_chunk_id = {hit.chunk_id: index for index, hit in enumerate(hits)}
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
        .where(
            ContentChunk.id.in_(chunk_ids),
            _searchable_chunk_predicate(),
            *_content_filter_clauses(filters),
        )
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
        is_video = content_item.item_type == "video_description"
        evidence.append(
            EvidenceObject(
                chunk_id=content_chunk.id,
                content_item_id=content_item.id,
                raw_page_id=content_item.raw_page_id,
                source_site_id=content_item.source_site_id,
                title=content_item.title,
                snippet=_snippet(content_chunk.display_text, terms=snippet_terms),
                canonical_url=content_item.canonical_url,
                source_site_name=source_site_name,
                author_name=author_name,
                published_at=content_item.published_at,
                item_type=content_item.item_type,
                score=hit.score,
                vector_score=hit.vector_score,
                keyword_score=hit.keyword_score,
                matched_by=hit.matched_by,
                thread_summary=thread_summary,
                video_url=content_item.canonical_url if is_video else None,
                description_text=content_item.cleaned_text if is_video else None,
            )
        )

    evidence.sort(
        key=lambda item: (-item.score, hit_rank_by_chunk_id.get(item.chunk_id, len(hits)))
    )
    return evidence[:top_k]


def _keyword_score(
    content_item: ContentItem, content_chunk: ContentChunk, terms: Sequence[str]
) -> float:
    score = 0.0
    tags = [tag.casefold() for tag in content_item.tags]
    chunk_text = f"{content_chunk.display_text}\n{content_chunk.embed_text}".casefold()
    for term in terms:
        normalized = term.casefold()
        if content_item.title is not None and normalized in content_item.title.casefold():
            score += 0.50
        if (
            content_item.summary_text is not None
            and normalized in content_item.summary_text.casefold()
        ):
            score += 0.30
        if normalized in chunk_text:
            score += 0.20
        if any(normalized in tag for tag in tags):
            score += 0.10

    if score > 0.0:
        return max(score, _KEYWORD_MINIMUM_MATCH_SCORE)
    return 0.0


def _hit_has_acceptable_evidence_score(hit: _SearchHit) -> bool:
    if hit.matched_by == "keyword":
        return True
    if hit.matched_by == "hybrid" and hit.keyword_score is not None:
        return True
    return _vector_score_meets_threshold(hit.vector_score)


def _searchable_chunk_predicate() -> Any:
    """Return the shared chunk-level predicate for retrieval candidates.

    Pending, successful, and failed chunks remain searchable for keyword fallback and stale
    vector hydration. Only chunks explicitly marked obsolete are hidden from evidence.
    """
    return ContentChunk.embed_status != _OBSOLETE_EMBED_STATUS


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
    if filters.published_after is not None:
        payload["published_after"] = filters.published_after
    if filters.published_before is not None:
        payload["published_before"] = filters.published_before
    return payload


def _qdrant_search_supports_score_threshold(search_method: Any) -> bool:
    try:
        return "score_threshold" in inspect.signature(search_method).parameters
    except (TypeError, ValueError):
        return False


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
    published_after = _coerce_datetime(filters.get("published_after"))
    published_before = _coerce_datetime(filters.get("published_before"))
    if published_after is not None or published_before is not None:
        conditions.append(
            qdrant_models.FieldCondition(
                key="published_at",
                range=qdrant_models.DatetimeRange(
                    gte=published_after,
                    lte=published_before,
                ),
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

    published_after = _coerce_datetime(filters.get("published_after"))
    published_before = _coerce_datetime(filters.get("published_before"))
    if published_after is not None or published_before is not None:
        published_at = _coerce_datetime(payload.get("published_at"))
        if published_at is None:
            return False
        if published_after is not None and published_at < published_after:
            return False
        if published_before is not None and published_at > published_before:
            return False
    return True


def _coerce_datetime(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return _as_utc(value)
    if isinstance(value, str) and value.strip():
        try:
            return _as_utc(datetime.fromisoformat(value.replace("Z", "+00:00")))
        except ValueError:
            return None
    return None


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


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


def _snippet(text: str, *, terms: Sequence[str] = (), max_chars: int = 300) -> str:
    collapsed = " ".join(text.split())
    if len(collapsed) <= max_chars:
        return collapsed

    folded = collapsed.casefold()
    match_index = min(
        (
            index
            for term in terms
            if term and (index := folded.find(term.casefold())) >= 0
        ),
        default=-1,
    )
    if match_index >= 0:
        start = max(0, match_index - max_chars // 2)
        end = min(len(collapsed), start + max_chars)
        start = max(0, end - max_chars)
        snippet = collapsed[start:end].strip()
        if start > 0:
            snippet = f"…{snippet.lstrip()}"
        if end < len(collapsed):
            snippet = f"{snippet.rstrip()}…"
        return snippet

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
