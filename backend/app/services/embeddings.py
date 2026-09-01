from __future__ import annotations

from functools import lru_cache
from typing import Any, Protocol, cast

import httpx

from app.core.config import Settings

DEFAULT_SENTENCE_TRANSFORMERS_MODEL = "BAAI/bge-small-zh-v1.5"
DEFAULT_SILICONFLOW_MODEL = "BAAI/bge-m3"
SILICONFLOW_BGE_M3_DIMENSION = 1024


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


class SiliconFlowEmbeddingService:
    def __init__(
        self,
        *,
        model_name: str,
        api_key: str,
        base_url: str,
        client: httpx.Client | None = None,
    ) -> None:
        if model_name.strip() != DEFAULT_SILICONFLOW_MODEL:
            raise ValueError(f"model_name must be {DEFAULT_SILICONFLOW_MODEL}")
        if not api_key.strip():
            raise ValueError("SiliconFlow embedding API key must not be blank")
        if not base_url.strip():
            raise ValueError("SiliconFlow embedding base URL must not be blank")
        self.model_name = model_name.strip()
        self._api_key = api_key.strip()
        self._base_url = base_url.rstrip("/")
        self._client = client or httpx.Client(timeout=30.0)

    @property
    def dimension(self) -> int:
        return SILICONFLOW_BGE_M3_DIMENSION

    def embed(self, text: str) -> list[float]:
        response = self._client.post(
            f"{self._base_url}/embeddings",
            headers={"Authorization": f"Bearer {self._api_key}"},
            json={"model": self.model_name, "input": text},
        )
        response.raise_for_status()
        try:
            vector = response.json()["data"][0]["embedding"]
        except (IndexError, KeyError, TypeError, ValueError) as exc:
            raise RuntimeError(
                "SiliconFlow embedding response did not contain data[0].embedding"
            ) from exc
        if not isinstance(vector, list) or len(vector) != self.dimension:
            raise RuntimeError(
                f"Invalid embedding dimension for {self.model_name}: expected {self.dimension}, "
                f"got {len(vector) if isinstance(vector, list) else 'non-list'}"
            )
        return [float(value) for value in vector]


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
    if provider == "siliconflow":
        if settings.embedding_dimension != SILICONFLOW_BGE_M3_DIMENSION:
            raise ValueError(
                f"EMBEDDING_DIMENSION must be {SILICONFLOW_BGE_M3_DIMENSION} for "
                f"{DEFAULT_SILICONFLOW_MODEL}"
            )
        model_name = settings.embedding_model.strip() or DEFAULT_SILICONFLOW_MODEL
        if model_name != DEFAULT_SILICONFLOW_MODEL:
            raise ValueError(f"EMBEDDING_MODEL must be {DEFAULT_SILICONFLOW_MODEL} for siliconflow")
        api_key = settings.embedding_api_key or settings.vlm_api_key
        if api_key is None or not api_key.strip():
            raise ValueError(
                "EMBEDDING_API_KEY or VLM_API_KEY is required for siliconflow embeddings"
            )
        return SiliconFlowEmbeddingService(
            model_name=model_name,
            api_key=api_key,
            base_url=settings.embedding_base_url or settings.vlm_base_url,
        )
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
        f"{settings.embedding_provider!r}; expected siliconflow or sentence-transformers"
    )

