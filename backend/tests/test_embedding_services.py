from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import app.services.embeddings as embeddings
import pytest
from app.core.config import Settings
from app.services.embeddings import (
    SentenceTransformerEmbeddingService,
    build_embedding_service,
)


class FakeVector:
    def __init__(self, values: list[float]) -> None:
        self.values = values

    def tolist(self) -> list[float]:
        return list(self.values)


@pytest.fixture(autouse=True)
def clear_sentence_transformer_cache() -> Iterator[None]:
    cache_clear = getattr(embeddings, "_clear_sentence_transformer_model_cache", None)
    if cache_clear is not None:
        cache_clear()
    yield
    if cache_clear is not None:
        cache_clear()


class FakeSentenceTransformer:
    instances: list[FakeSentenceTransformer] = []

    def __init__(self, model_name: str) -> None:
        self.model_name = model_name
        self.encode_calls: list[tuple[str, bool]] = []
        FakeSentenceTransformer.instances.append(self)

    def get_sentence_embedding_dimension(self) -> int:
        return 3

    def encode(self, text: str, *, normalize_embeddings: bool) -> FakeVector:
        self.encode_calls.append((text, normalize_embeddings))
        return FakeVector([0.1, 0.2, 0.3])


def test_embedding_settings_default_to_local_bge_small_zh() -> None:
    settings = Settings()

    assert settings.embedding_provider == "sentence-transformers"
    assert settings.embedding_model == "BAAI/bge-small-zh-v1.5"
    assert settings.embedding_dimension == 512


def test_build_embedding_service_returns_sentence_transformer_by_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    FakeSentenceTransformer.instances.clear()
    monkeypatch.setattr(
        embeddings,
        "_load_sentence_transformer_class",
        lambda: FakeSentenceTransformer,
    )

    service = build_embedding_service(Settings())

    assert isinstance(service, SentenceTransformerEmbeddingService)
    assert service.model_name == "BAAI/bge-small-zh-v1.5"
    assert service.dimension == 3


def test_build_embedding_service_returns_sentence_transformer_provider(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    FakeSentenceTransformer.instances.clear()
    monkeypatch.setattr(
        embeddings,
        "_load_sentence_transformer_class",
        lambda: FakeSentenceTransformer,
    )
    settings = Settings(
        EMBEDDING_PROVIDER="sentence-transformers",
        EMBEDDING_MODEL="BAAI/bge-small-zh-v1.5",
    )

    service = build_embedding_service(settings)

    assert isinstance(service, SentenceTransformerEmbeddingService)
    assert service.model_name == "BAAI/bge-small-zh-v1.5"
    assert service.dimension == 3
    assert service.embed("遥控器 图传") == [0.1, 0.2, 0.3]
    assert FakeSentenceTransformer.instances[0].encode_calls == [("遥控器 图传", True)]


def test_sentence_transformer_provider_reuses_loaded_model(monkeypatch: pytest.MonkeyPatch) -> None:
    FakeSentenceTransformer.instances.clear()
    monkeypatch.setattr(
        embeddings,
        "_load_sentence_transformer_class",
        lambda: FakeSentenceTransformer,
    )
    service = SentenceTransformerEmbeddingService("BAAI/bge-small-zh-v1.5")

    first_dimension = service.dimension
    second_dimension = service.dimension
    vector = service.embed("固件 升级")

    assert first_dimension == 3
    assert second_dimension == 3
    assert vector == [0.1, 0.2, 0.3]
    assert len(FakeSentenceTransformer.instances) == 1


def test_sentence_transformer_provider_reuses_model_across_service_instances(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    FakeSentenceTransformer.instances.clear()
    monkeypatch.setattr(
        embeddings,
        "_load_sentence_transformer_class",
        lambda: FakeSentenceTransformer,
    )

    first_service = SentenceTransformerEmbeddingService("BAAI/bge-small-zh-v1.5")
    second_service = SentenceTransformerEmbeddingService("BAAI/bge-small-zh-v1.5")

    assert first_service.embed("遥控器 图传") == [0.1, 0.2, 0.3]
    assert second_service.embed("固件 升级") == [0.1, 0.2, 0.3]
    assert len(FakeSentenceTransformer.instances) == 1
    assert FakeSentenceTransformer.instances[0].encode_calls == [
        ("遥控器 图传", True),
        ("固件 升级", True),
    ]


def test_unsupported_embedding_provider_raises_clear_error() -> None:
    settings = Settings(EMBEDDING_PROVIDER="unknown-provider")

    with pytest.raises(ValueError, match="Unsupported EMBEDDING_PROVIDER"):
        build_embedding_service(settings)


@pytest.mark.parametrize("provider", ["deterministic", "fake", "siliconflow"])
def test_removed_embedding_providers_raise_clear_error(provider: str) -> None:
    settings = Settings(EMBEDDING_PROVIDER=provider)

    with pytest.raises(ValueError, match="Unsupported EMBEDDING_PROVIDER"):
        build_embedding_service(settings)


def test_sentence_transformer_import_error_is_clear(monkeypatch: pytest.MonkeyPatch) -> None:
    def raise_import_error() -> Any:
        raise RuntimeError(
            "sentence-transformers is required for EMBEDDING_PROVIDER=sentence-transformers"
        )

    monkeypatch.setattr(embeddings, "_load_sentence_transformer_class", raise_import_error)
    service = SentenceTransformerEmbeddingService("BAAI/bge-small-zh-v1.5")

    with pytest.raises(RuntimeError, match="sentence-transformers is required"):
        service.embed("telemetry")
