from __future__ import annotations

from typing import Any

import app.services.search as search_module
from app.core.config import Settings
from app.services.search import SearchService

import worker.app.chunk_indexer as chunk_indexer


class FakeEmbedding:
    model_name = "fake-semantic-model"
    dimension = 7

    def embed(self, text: str) -> list[float]:
        return [0.0 for _ in range(self.dimension)]


class FakeAgentClient:
    pass


def test_search_service_uses_embedding_factory_when_embedding_not_injected(
    monkeypatch: Any,
) -> None:
    fake_embedding = FakeEmbedding()
    captured_settings: list[Settings] = []

    def fake_build_embedding_service(settings: Settings) -> FakeEmbedding:
        captured_settings.append(settings)
        return fake_embedding

    settings = Settings(EMBEDDING_PROVIDER="sentence-transformers", EMBEDDING_MODEL="fake-model")
    monkeypatch.setattr(search_module, "build_embedding_service", fake_build_embedding_service)

    service = SearchService(
        session=object(),  # type: ignore[arg-type]
        settings=settings,
        agent_client=FakeAgentClient(),  # type: ignore[arg-type]
    )

    assert service.embedding is fake_embedding
    assert captured_settings == [settings]


def test_worker_current_vector_target_uses_embedding_factory(monkeypatch: Any) -> None:
    settings = Settings(
        QDRANT_URL="memory://factory-wiring",
        QDRANT_COLLECTION="semantic_collection",
        EMBEDDING_PROVIDER="sentence-transformers",
        EMBEDDING_MODEL="fake-model",
    )
    fake_embedding = FakeEmbedding()

    monkeypatch.setattr(chunk_indexer, "get_settings", lambda: settings)
    monkeypatch.setattr(chunk_indexer, "build_embedding_service", lambda received: fake_embedding)

    target = chunk_indexer._current_vector_index_target()  # noqa: SLF001

    assert target.backend_name == "memory"
    assert target.metadata["vector_collection"] == "semantic_collection"
    assert target.metadata["embedding_model"] == "fake-semantic-model"
    assert target.metadata["embedding_dimension"] == 7
