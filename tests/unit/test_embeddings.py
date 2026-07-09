"""Pure synchronous tests for embedding services.

These tests do not depend on PostgreSQL, Qdrant, or any async fixtures.
They are in a ``unit/`` subdirectory so they avoid the async ``conftest.py``
in ``backend/tests/`` and run under synchronous pytest without a database.
"""

import importlib.util

import pytest

from app.core.config import Settings
from app.services.embeddings import (
    _clear_sentence_transformer_model_cache,
    SentenceTransformerEmbeddingService,
    build_embedding_service,
)


requires_sentence_transformers = pytest.mark.skipif(
    importlib.util.find_spec("sentence_transformers") is None,
    reason="sentence-transformers optional dependency is not installed",
)


def test_embedding_settings_default_to_local_bge_small_zh() -> None:
    settings = Settings()

    assert settings.qdrant_collection == "content_chunks_v2"
    assert settings.embedding_provider == "sentence-transformers"
    assert settings.embedding_model == "BAAI/bge-small-zh-v1.5"
    assert settings.embedding_dimension == 512


def test_embedding_factory_raises_on_unsupported_provider() -> None:
    with pytest.raises(ValueError, match="Unsupported EMBEDDING_PROVIDER"):
        build_embedding_service(Settings(EMBEDDING_PROVIDER="openai"))


@pytest.mark.parametrize("provider", ["deterministic", "fake", "siliconflow"])
def test_embedding_factory_rejects_removed_embedding_providers(provider: str) -> None:
    with pytest.raises(ValueError, match="Unsupported EMBEDDING_PROVIDER"):
        build_embedding_service(Settings(EMBEDDING_PROVIDER=provider))


def test_embedding_factory_rejects_non_512_dimension() -> None:
    with pytest.raises(ValueError, match="EMBEDDING_DIMENSION must be 512"):
        build_embedding_service(Settings(EMBEDDING_DIMENSION=1024))


def test_embedding_factory_rejects_other_local_model() -> None:
    with pytest.raises(ValueError, match="EMBEDDING_MODEL must be BAAI/bge-small-zh-v1.5"):
        build_embedding_service(Settings(EMBEDDING_MODEL="BAAI/bge-m3"))


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
        Settings(EMBEDDING_PROVIDER="sentence-transformers")
    )
    assert isinstance(service, SentenceTransformerEmbeddingService)


def test_embedding_model_name_empty_raises() -> None:
    with pytest.raises(ValueError, match="model_name must not be blank"):
        SentenceTransformerEmbeddingService(model_name="")


def test_embedding_model_name_whitespace_raises() -> None:
    with pytest.raises(ValueError, match="model_name must not be blank"):
        SentenceTransformerEmbeddingService(model_name="   ")
