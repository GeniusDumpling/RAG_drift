from dataclasses import dataclass, field
from typing import Any, Literal
from uuid import UUID

from qdrant_client import QdrantClient
from qdrant_client.http import models as qdrant_models

from app.services.embeddings import EmbeddingService


@dataclass
class _MemoryPoint:
    vector: list[float]
    payload: dict[str, Any]


@dataclass
class _MemoryCollection:
    dimension: int
    points: dict[str, _MemoryPoint] = field(default_factory=dict)


_MEMORY_COLLECTIONS: dict[tuple[str, str], _MemoryCollection] = {}


class QdrantIndexer:
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

    def _require_client(self) -> QdrantClient:
        if self._client is None:
            raise RuntimeError("Qdrant client is not available for the memory backend")
        return self._client


def _is_memory_url(url: str) -> bool:
    return url == ":memory:" or url.startswith("memory://")
