"""Pure synchronous tests for embedding services.

These tests do not depend on PostgreSQL, Qdrant, or any async fixtures.
They are in a ``unit/`` subdirectory so they avoid the async ``conftest.py``
in ``backend/tests/`` and run under synchronous pytest without a database.
"""

import pytest

from app.core.config import Settings
from app.services.embeddings import (
    DeterministicEmbeddingService,
    SentenceTransformerEmbeddingService,
    build_embedding_service,
    _clear_sentence_transformer_model_cache,
)


def test_deterministic_embedding_is_stable() -> None:
    service = DeterministicEmbeddingService(dimension=384)
    v1 = service.embed("Hello world")
    v2 = service.embed("Hello world")
    assert v1 == v2
    assert len(v1) == 384


def test_deterministic_embedding_differs_for_diff_text() -> None:
    service = DeterministicEmbeddingService(dimension=384)
    v1 = service.embed("Hello world")
    v2 = service.embed("Goodbye world")
    assert v1 != v2


def test_embedding_factory_keeps_deterministic_for_tests() -> None:
    service = build_embedding_service(Settings(EMBEDDING_PROVIDER="deterministic"))
    assert service.model_name == "deterministic-hash-v1"


def test_embedding_factory_raises_on_unsupported_provider() -> None:
    with pytest.raises(ValueError, match="Unsupported EMBEDDING_PROVIDER"):
        build_embedding_service(Settings(EMBEDDING_PROVIDER="openai"))


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
