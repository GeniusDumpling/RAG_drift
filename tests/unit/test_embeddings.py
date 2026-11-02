"""Pure synchronous tests for embedding services.

These tests do not depend on PostgreSQL, Qdrant, or any async fixtures.
They are in a ``unit/`` subdirectory so they avoid the async ``conftest.py``
in ``backend/tests/`` and run under synchronous pytest without a database.
"""

import json

import httpx
import pytest
from app.core.config import Settings
from app.services.embeddings import (
    SiliconFlowEmbeddingService,
    build_embedding_service,
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


@pytest.mark.parametrize("provider", ["deterministic", "fake", "sentence-transformers"])
def test_embedding_factory_rejects_removed_embedding_providers(provider: str) -> None:
    with pytest.raises(ValueError, match="Unsupported EMBEDDING_PROVIDER"):
        build_embedding_service(Settings(EMBEDDING_PROVIDER=provider))


def test_siliconflow_embedding_factory_rejects_non_1024_dimension() -> None:
    with pytest.raises(ValueError, match="EMBEDDING_DIMENSION must be 1024"):
        build_embedding_service(Settings(EMBEDDING_DIMENSION=512))


def test_siliconflow_embedding_factory_rejects_other_models() -> None:
    with pytest.raises(ValueError, match="EMBEDDING_MODEL must be BAAI/bge-m3"):
        build_embedding_service(
            Settings(
                EMBEDDING_MODEL="BAAI/bge-small-zh-v1.5",
                VLM_API_KEY="test-api-key",
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


def test_siliconflow_embedding_service_rejects_blank_inputs() -> None:
    with pytest.raises(ValueError, match="model_name must be BAAI/bge-m3"):
        SiliconFlowEmbeddingService(
            model_name="",
            api_key="test-api-key",
            base_url="https://api.siliconflow.cn/v1",
        )
    with pytest.raises(ValueError, match="API key must not be blank"):
        SiliconFlowEmbeddingService(
            model_name="BAAI/bge-m3",
            api_key="   ",
            base_url="https://api.siliconflow.cn/v1",
        )
