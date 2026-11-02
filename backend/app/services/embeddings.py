from __future__ import annotations

from typing import Protocol

import httpx

from app.core.config import Settings

DEFAULT_SILICONFLOW_MODEL = "BAAI/bge-m3"
SILICONFLOW_BGE_M3_DIMENSION = 1024


class EmbeddingService(Protocol):
    @property
    def model_name(self) -> str: ...

    @property
    def dimension(self) -> int: ...

    def embed(self, text: str) -> list[float]: ...


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


def build_embedding_service(settings: Settings) -> EmbeddingService:
    provider = settings.embedding_provider.strip().casefold().replace("_", "-")
    if provider != "siliconflow":
        raise ValueError(
            "Unsupported EMBEDDING_PROVIDER "
            f"{settings.embedding_provider!r}; expected siliconflow"
        )
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
