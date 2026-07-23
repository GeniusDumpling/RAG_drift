from __future__ import annotations

from functools import lru_cache
from typing import Any, Protocol, cast

from app.core.config import Settings

DEFAULT_SENTENCE_TRANSFORMERS_MODEL = "BAAI/bge-small-zh-v1.5"


class EmbeddingService(Protocol):
    @property
    def model_name(self) -> str: ...

    @property
    def dimension(self) -> int: ...

    def embed(self, text: str) -> list[float]: ...


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


def build_embedding_service(settings: Settings) -> EmbeddingService:
    provider = settings.embedding_provider.strip().casefold().replace("_", "-")
    if provider == "sentence-transformers":
        if settings.embedding_dimension != 512:
            raise ValueError("EMBEDDING_DIMENSION must be 512 for BAAI/bge-small-zh-v1.5")
        model_name = settings.embedding_model.strip() or DEFAULT_SENTENCE_TRANSFORMERS_MODEL
        if model_name != DEFAULT_SENTENCE_TRANSFORMERS_MODEL:
            raise ValueError(
                "EMBEDDING_MODEL must be BAAI/bge-small-zh-v1.5 for local "
                "512-dimensional embeddings"
            )
        return SentenceTransformerEmbeddingService(model_name=model_name)
    raise ValueError(
        "Unsupported EMBEDDING_PROVIDER "
        f"{settings.embedding_provider!r}; expected sentence-transformers"
    )

