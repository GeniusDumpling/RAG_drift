"""Pure synchronous tests for embedding services.

These tests do not depend on PostgreSQL, Qdrant, or any async fixtures.
They are in a ``unit/`` subdirectory so they avoid the async ``conftest.py``
in ``backend/tests/`` and run under synchronous pytest without a database.
"""

import importlib.util
import json

import httpx
import pytest
from app.core.config import Settings
from app.services.embeddings import (
    SentenceTransformerEmbeddingService,
    SiliconFlowEmbeddingService,
    _clear_sentence_transformer_model_cache,
    build_embedding_service,
)

requires_sentence_transformers = pytest.mark.skipif(
    importlib.util.find_spec("sentence_transformers") is None,
    reason="sentence-transformers optional dependency is not installed",
)


def test_embedding_settings_default_to_siliconflow_bge_m3() -> None:
    settings = Settings()

    assert settings.qdrant_collection == "content_chunks_bge_m3_v1"
    assert settings.embedding_provider == "siliconflow"
    assert settings.embedding_model == "BAAI/bge-m3"
    assert settings.embedding_dimension == 1024


def test_embedding_factory_raises_on_unsupported_provider() -> None:
    with pytest.raises(ValueError, match="Unsupported EMBEDDING_PROVIDER"):
        build_embedding_service(Settings(EMBEDDING_PROVIDER="openai"))


@pytest.mark.parametrize("provider", ["deterministic", "fake"])
def test_embedding_factory_rejects_removed_embedding_providers(provider: str) -> None:
    with pytest.raises(ValueError, match="Unsupported EMBEDDING_PROVIDER"):
        build_embedding_service(Settings(EMBEDDING_PROVIDER=provider))


def test_siliconflow_embedding_factory_rejects_non_1024_dimension() -> None:
    with pytest.raises(ValueError, match="EMBEDDING_DIMENSION must be 1024"):
        build_embedding_service(Settings(EMBEDDING_DIMENSION=512))


def test_embedding_factory_rejects_other_local_model() -> None:
    with pytest.raises(ValueError, match="EMBEDDING_MODEL must be BAAI/bge-small-zh-v1.5"):
        build_embedding_service(
            Settings(
                EMBEDDING_PROVIDER="sentence-transformers",
                EMBEDDING_MODEL="BAAI/bge-m3",
                EMBEDDING_DIMENSION=512,
            )
        )


def test_siliconflow_embedding_service_posts_openai_compatible_embedding_request() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json={"data": [{"embedding": [0.1] * 1024}]})

    service = SiliconFlowEmbeddingService(
        model_name="BAAI/bge-m3",
        api_key="test-api-key",
        base_url="https://api.siliconflow.cn/v1",
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )

    assert service.embed("English video subtitle") == [0.1] * 1024
    assert requests[0].url == "https://api.siliconflow.cn/v1/embeddings"
    assert requests[0].headers["authorization"] == "Bearer test-api-key"
    assert json.loads(requests[0].content) == {
        "model": "BAAI/bge-m3",
        "input": "English video subtitle",
    }


def test_embedding_factory_builds_siliconflow_bge_m3_from_existing_vlm_key() -> None:
    service = build_embedding_service(Settings(VLM_API_KEY="existing-siliconflow-key"))

    assert isinstance(service, SiliconFlowEmbeddingService)
    assert service.model_name == "BAAI/bge-m3"
    assert service.dimension == 1024


@requires_sentence_transformers
def test_sentence_transformer_embedding_shape() -> None:
    """Integration-light: verify SentenceTransformer yields the expected dimension."""
    _clear_sentence_transformer_model_cache()
    service = SentenceTransformerEmbeddingService(
        model_name="BAAI/bge-small-zh-v1.5"
    )
    result = service.embed("无人机低空飞行")
    assert isinstance(result, list)
    assert all(isinstance(v, float) for v in result)
    assert len(result) == service.dimension
    # bge-small-zh-v1.5 has 512 dimensions
    assert service.dimension == 512


@requires_sentence_transformers
def test_sentence_transformer_is_stable() -> None:
    _clear_sentence_transformer_model_cache()
    service = SentenceTransformerEmbeddingService(
        model_name="BAAI/bge-small-zh-v1.5"
    )
    v1 = service.embed("测试稳定性")
    v2 = service.embed("测试稳定性")
    assert v1 == v2


def test_build_embedding_service_sentence_transformers() -> None:
    _clear_sentence_transformer_model_cache()
    service = build_embedding_service(
        Settings(
            EMBEDDING_PROVIDER="sentence-transformers",
            EMBEDDING_MODEL="BAAI/bge-small-zh-v1.5",
            EMBEDDING_DIMENSION=512,
        )
    )
    assert isinstance(service, SentenceTransformerEmbeddingService)


def test_embedding_model_name_empty_raises() -> None:
    with pytest.raises(ValueError, match="model_name must not be blank"):
        SentenceTransformerEmbeddingService(model_name="")


def test_embedding_model_name_whitespace_raises() -> None:
    with pytest.raises(ValueError, match="model_name must not be blank"):
        SentenceTransformerEmbeddingService(model_name="   ")
