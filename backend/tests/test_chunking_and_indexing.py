import uuid

from app.services.chunking import build_chunks
from app.services.embeddings import DeterministicEmbeddingService
from app.services.retrieval import QdrantIndexer


def test_article_chunking_produces_stable_embed_text() -> None:
    chunks = build_chunks(
        item_type="article",
        title="Release notes",
        cleaned_text="Telemetry was changed. Operators can disable it in settings.",
        summary_text="Telemetry settings changed.",
        tags=["telemetry", "settings"],
    )
    assert chunks[0].chunk_index == 0
    assert chunks[0].chunk_type == "title_lead"
    assert "Release notes" in chunks[0].embed_text
    assert "telemetry" in chunks[0].embed_text.lower()


def test_comment_chunking_adds_context() -> None:
    chunks = build_chunks(
        item_type="comment",
        title=None,
        cleaned_text="I reproduced the issue on v1.2.",
        summary_text=None,
        tags=["bug"],
        thread_title="Telemetry issue thread",
    )
    assert chunks[0].chunk_type == "comment_contextual"
    assert "Telemetry issue thread" in chunks[0].embed_text


def test_deterministic_embedding_has_fixed_dimension_and_repeatability() -> None:
    service = DeterministicEmbeddingService(dimension=32)
    first = service.embed("telemetry settings")
    second = service.embed("telemetry settings")
    assert len(first) == 32
    assert first == second


def test_memory_indexer_returns_deterministic_point_id() -> None:
    chunk_id = uuid.uuid4()
    indexer = QdrantIndexer(
        url="memory://unit-test",
        collection="content_chunks_test",
        embedding=DeterministicEmbeddingService(dimension=16),
    )

    indexer.ensure_collection()
    point_id = indexer.upsert_chunk(
        chunk_id=chunk_id,
        embed_text="telemetry settings",
        payload={"content_item_id": str(uuid.uuid4())},
    )

    assert indexer.backend_name == "memory"
    assert point_id == str(chunk_id)
