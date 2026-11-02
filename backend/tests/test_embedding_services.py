from __future__ import annotations

import pytest
from app.core.config import Settings
from app.services.embeddings import SiliconFlowEmbeddingService, build_embedding_service

pytestmark = pytest.mark.no_db


def test_embedding_settings_default_to_siliconflow_bge_m3() -> None:
    settings = Settings()

    assert settings.embedding_provider == "siliconflow"
    assert settings.embedding_model == "BAAI/bge-m3"
    assert settings.embedding_dimension == 1024


def test_build_embedding_service_returns_siliconflow_bge_m3_by_default() -> None:
    service = build_embedding_service(Settings(VLM_API_KEY="test-key"))

    assert isinstance(service, SiliconFlowEmbeddingService)
    assert service.model_name == "BAAI/bge-m3"
    assert service.dimension == 1024


def test_unsupported_embedding_provider_raises_clear_error() -> None:
    settings = Settings(EMBEDDING_PROVIDER="unknown-provider")

    with pytest.raises(ValueError, match="Unsupported EMBEDDING_PROVIDER"):
        build_embedding_service(settings)


@pytest.mark.parametrize("provider", ["deterministic", "fake", "sentence-transformers"])
def test_removed_embedding_providers_raise_clear_error(provider: str) -> None:
    settings = Settings(EMBEDDING_PROVIDER=provider)

    with pytest.raises(ValueError, match="Unsupported EMBEDDING_PROVIDER"):
        build_embedding_service(settings)
