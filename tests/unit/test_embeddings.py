"""Pure synchronous tests for embedding services.

These tests do not depend on PostgreSQL, Qdrant, or any async fixtures.
They are in a ``unit/`` subdirectory so they avoid the async ``conftest.py``
in ``backend/tests/`` and run under synchronous pytest without a database.
"""

import json

import httpx
import pytest

from app.core.config import Settings
from app.services.embeddings import SiliconFlowEmbeddingService, build_embedding_service


def test_siliconflow_embedding_uses_bge_m3_contract() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v1/embeddings"
        assert request.headers["Authorization"] == "Bearer test-key"
        assert json.loads(request.content) == {
            "model": "BAAI/bge-m3",
            "input": "无人机低空飞行",
        }
        return httpx.Response(200, json={"data": [{"embedding": [0.1, 0.2, 0.3]}]})

    service = SiliconFlowEmbeddingService(
        api_key="test-key",
        base_url="https://api.siliconflow.cn/v1",
        model="BAAI/bge-m3",
        dimension=3,
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    assert service.embed("无人机低空飞行") == [0.1, 0.2, 0.3]


def test_embedding_factory_keeps_fake_mode_for_tests() -> None:
    service = build_embedding_service(Settings(EMBEDDING_PROVIDER="fake"))
    assert service.model_name == "deterministic-hash-v1"


def test_embedding_factory_raises_on_missing_siliconflow_key() -> None:
    with pytest.raises(RuntimeError, match="SILICONFLOW_API_KEY is required"):
        build_embedding_service(
            Settings(
                EMBEDDING_PROVIDER="siliconflow",
                SILICONFLOW_API_KEY=None,
            )
        )


def test_embedding_factory_raises_on_unsupported_provider() -> None:
    with pytest.raises(ValueError, match="unsupported embedding provider"):
        build_embedding_service(Settings(EMBEDDING_PROVIDER="openai"))


def test_siliconflow_dimension_mismatch_raises() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"data": [{"embedding": [0.1, 0.2, 0.3, 0.4]}]})

    service = SiliconFlowEmbeddingService(
        api_key="test-key",
        base_url="https://api.siliconflow.cn/v1",
        model="BAAI/bge-m3",
        dimension=1024,
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    with pytest.raises(ValueError, match="embedding dimension mismatch"):
        service.embed("test text")
