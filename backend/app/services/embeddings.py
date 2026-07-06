from __future__ import annotations

import hashlib
import math
from functools import lru_cache
from typing import Any, Protocol, cast

from app.core.config import Settings

DEFAULT_DETERMINISTIC_MODEL = "deterministic-hash-v1"
DEFAULT_SENTENCE_TRANSFORMERS_MODEL = "BAAI/bge-small-zh-v1.5"

import httpx

from app.core.config import Settings


class EmbeddingService(Protocol):
    @property
    def model_name(self) -> str: ...

    @property
    def dimension(self) -> int: ...

    def embed(self, text: str) -> list[float]: ...


class DeterministicEmbeddingService:
    model_name: str = DEFAULT_DETERMINISTIC_MODEL

    def __init__(self, dimension: int = 384) -> None:
        if dimension < 1:
            raise ValueError("dimension must be at least 1")
        self.dimension = dimension

    def embed(self, text: str) -> list[float]:
        values: list[float] = []
        salt_index = 0
        encoded_text = text.encode("utf-8")
        while len(values) < self.dimension:
            digest = hashlib.sha256(salt_index.to_bytes(4, "big") + b"\0" + encoded_text).digest()
            values.extend((byte / 127.5) - 1.0 for byte in digest)
            salt_index += 1

        vector = values[: self.dimension]
        norm = math.sqrt(sum(value * value for value in vector))
        if norm == 0.0:
            return [0.0 for _ in vector]
        return [value / norm for value in vector]


class SentenceTransformerEmbeddingService:
    def __init__(self, model_name: str = DEFAULT_SENTENCE_TRANSFORMERS_MODEL) -> None:
        if not model_name.strip():
            raise ValueError("model_name must not be blank")
        self.model_name = model_name.strip()
        self._model: Any | None = None
        self._dimension: int | None = None

    @property
    def dimension(self) -> int:
        if self._dimension is None:
            raw_dimension = self._loaded_model.get_sentence_embedding_dimension()
            if not isinstance(raw_dimension, int) or raw_dimension < 1:
                raise RuntimeError(
                    f"Invalid embedding dimension for {self.model_name}: {raw_dimension}"
                )
            self._dimension = raw_dimension
        return self._dimension

    def embed(self, text: str) -> list[float]:
        vector = self._loaded_model.encode(text, normalize_embeddings=True)
        if hasattr(vector, "tolist"):
            vector = vector.tolist()
        values = cast(list[float], list(vector))
        if len(values) != self.dimension:
            raise RuntimeError(
                f"Embedding dimension mismatch for {self.model_name}: "
                f"expected {self.dimension}, got {len(values)}"
            )
        return [float(value) for value in values]

    @property
    def _loaded_model(self) -> Any:
        if self._model is None:
            self._model = _load_sentence_transformer_model(self.model_name)
        return self._model


class SiliconFlowEmbeddingService:
    """SiliconFlow OpenAI-compatible embeddings (BAAI/bge-m3)."""

    def __init__(
        self,
        *,
        api_key: str,
        base_url: str,
        model: str,
        dimension: int,
        client: httpx.Client | None = None,
    ) -> None:
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.model_name = model
        self.dimension = dimension
        self.client = client or httpx.Client(timeout=60)

    def embed(self, text: str) -> list[float]:
        response = self.client.post(
            f"{self.base_url}/embeddings",
            headers={"Authorization": f"Bearer {self.api_key}"},
            json={"model": self.model_name, "input": text},
        )
        response.raise_for_status()
        vector = response.json()["data"][0]["embedding"]
        if len(vector) != self.dimension:
            raise ValueError(
                f"embedding dimension mismatch: expected {self.dimension}, got {len(vector)}"
            )
        return [float(value) for value in vector]


def build_embedding_service(settings: Settings) -> EmbeddingService:
    provider = settings.embedding_provider.strip().casefold().replace("_", "-")
    if provider == "deterministic":
        return DeterministicEmbeddingService(dimension=settings.embedding_dimension)
    if provider == "sentence-transformers":
        model_name = settings.embedding_model.strip() or DEFAULT_SENTENCE_TRANSFORMERS_MODEL
        return SentenceTransformerEmbeddingService(model_name=model_name)
    if provider == "siliconflow":
        if not settings.siliconflow_api_key:
            raise RuntimeError(
                "SILICONFLOW_API_KEY is required when EMBEDDING_PROVIDER=siliconflow"
            )
        return SiliconFlowEmbeddingService(
            api_key=settings.siliconflow_api_key,
            base_url=settings.siliconflow_base_url,
            model=settings.embedding_model,
            dimension=settings.embedding_dimension,
        )
    raise ValueError(
        "Unsupported EMBEDDING_PROVIDER "
        f"{settings.embedding_provider!r}; expected deterministic, sentence-transformers, or siliconflow"
    )


@lru_cache(maxsize=4)
def _load_sentence_transformer_model(model_name: str) -> Any:
    sentence_transformer_class = _load_sentence_transformer_class()
    return sentence_transformer_class(model_name)


def _clear_sentence_transformer_model_cache() -> None:
    _load_sentence_transformer_model.cache_clear()


def _load_sentence_transformer_class() -> Any:
    try:
        from sentence_transformers import SentenceTransformer
    except ImportError as exc:
        raise RuntimeError(
            "sentence-transformers is required for EMBEDDING_PROVIDER=sentence-transformers. "
            "Install it with: python -m pip install -e '.[local-embeddings]'"
        ) from exc
    return SentenceTransformer



