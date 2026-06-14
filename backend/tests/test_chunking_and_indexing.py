import uuid

import app.services.chunking as chunking_service
import pytest
from app.services.chunking import build_chunks
from app.services.embeddings import DeterministicEmbeddingService
from app.services.retrieval import (
    _MEMORY_COLLECTIONS,
    DEFAULT_VECTOR_SCORE_THRESHOLD,
    QdrantIndexer,
)


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


def test_article_chunking_with_long_normalized_text_caps_lead_and_skips_duplicate_body() -> None:
    normalized_text = "abcdefghijklmnopqrstuvwxyz0123456789"

    chunks = build_chunks(
        item_type="article",
        title=None,
        cleaned_text=normalized_text,
        summary_text=None,
        tags=[],
        max_chars=10,
    )

    assert [chunk.chunk_type for chunk in chunks] == [
        "title_lead",
        "body_section",
        "body_section",
        "body_section",
    ]
    assert chunks[0].display_text == "abcdefghij"
    assert chunks[0].start_char == 0
    assert chunks[0].end_char == 10
    assert all(len(chunk.display_text) <= 10 for chunk in chunks)
    assert "".join(chunk.display_text for chunk in chunks) == normalized_text
    assert [(chunk.start_char, chunk.end_char) for chunk in chunks] == [
        (0, 10),
        (10, 20),
        (20, 30),
        (30, 36),
    ]


def test_article_chunking_short_normalized_text_uses_single_title_lead() -> None:
    chunks = build_chunks(
        item_type="doc_page",
        title=None,
        cleaned_text="short normalized body",
        summary_text=None,
        tags=[],
        max_chars=32,
    )

    assert [chunk.chunk_type for chunk in chunks] == ["title_lead"]
    assert chunks[0].display_text == "short normalized body"
    assert chunks[0].start_char == 0
    assert chunks[0].end_char == len("short normalized body")


def test_article_chunking_offsets_are_relative_to_unstripped_input() -> None:
    cleaned_text = "\n  Alpha beta  \n"

    chunks = build_chunks(
        item_type="article",
        title="Operator guide",
        cleaned_text=cleaned_text,
        summary_text="Short summary",
        tags=["telemetry"],
        max_chars=50,
    )

    assert [chunk.chunk_type for chunk in chunks] == ["title_lead"]
    assert chunks[0].display_text == "Operator guide\n\nShort summary\n\nAlpha beta"
    assert chunks[0].start_char == cleaned_text.index("Alpha")
    assert chunks[0].end_char == cleaned_text.index("Alpha") + len("Alpha beta")
    assert cleaned_text[chunks[0].start_char : chunks[0].end_char] == "Alpha beta"
    assert chunks[0].chunk_metadata_json["offset_basis"] == "cleaned_text"
    assert chunks[0].chunk_metadata_json["offset_text"] == "body_span"
    assert chunks[0].chunk_metadata_json["contextual_display_fields"] == [
        "title",
        "summary",
    ]
    assert chunks[0].chunk_metadata_json["contextual_embed_fields"] == [
        "title",
        "summary",
        "tags",
    ]


def test_long_article_chunking_starts_body_after_bounded_lead_span() -> None:
    cleaned_text = "  abcdefghij klmnop  "

    chunks = build_chunks(
        item_type="article",
        title=None,
        cleaned_text=cleaned_text,
        summary_text=None,
        tags=[],
        max_chars=10,
    )

    assert [chunk.chunk_type for chunk in chunks] == ["title_lead", "body_section"]
    assert chunks[0].display_text == "abcdefghij"
    assert chunks[0].start_char == 2
    assert chunks[0].end_char == 12
    assert chunks[1].display_text == " klmnop"
    assert chunks[1].start_char == chunks[0].end_char
    assert chunks[1].end_char == len(cleaned_text.rstrip())
    assert cleaned_text[chunks[1].start_char : chunks[1].end_char] == chunks[1].display_text
    assert "".join(chunk.display_text for chunk in chunks) == cleaned_text.strip()
    assert chunks[1].chunk_metadata_json["contextual_display_fields"] == []


def test_chunks_include_chunker_version_and_embed_text_hash_metadata() -> None:
    chunks = build_chunks(
        item_type="comment",
        title=None,
        cleaned_text="I reproduced the issue on v1.2.",
        summary_text=None,
        tags=["bug"],
        thread_title="Telemetry issue thread",
    )

    assert hasattr(chunking_service, "CHUNKER_VERSION")
    assert chunks[0].chunk_metadata_json["chunker_version"] == chunking_service.CHUNKER_VERSION
    assert isinstance(chunks[0].chunk_metadata_json["embed_text_hash"], str)
    assert len(chunks[0].chunk_metadata_json["embed_text_hash"]) == 64


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


def test_long_comment_chunking_splits_comment_text_by_max_chars() -> None:
    comment_text = "abcdefghijklmnopqrstuvwxyz0123456789"

    chunks = build_chunks(
        item_type="comment",
        title=None,
        cleaned_text=comment_text,
        summary_text=None,
        tags=["bug", "regression"],
        thread_title="Telemetry issue thread",
        max_chars=10,
    )

    assert [chunk.chunk_index for chunk in chunks] == [0, 1, 2, 3]
    assert chunks[0].chunk_type == "comment_contextual"
    assert {chunk.chunk_type for chunk in chunks[1:]} == {"comment_contextual_continued"}
    assert "Telemetry issue thread" in chunks[0].embed_text
    assert "bug, regression" in chunks[0].embed_text
    assert "Comment: abcdefghij" in chunks[0].embed_text
    assert all(len(chunk.display_text) <= 10 for chunk in chunks)
    assert "".join(chunk.display_text for chunk in chunks) == comment_text
    assert [(chunk.start_char, chunk.end_char) for chunk in chunks] == [
        (0, 10),
        (10, 20),
        (20, 30),
        (30, 36),
    ]


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


def test_qdrant_indexer_passes_score_threshold_to_supported_client(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured_search_kwargs: dict[str, object] = {}

    class FakeQdrantClient:
        def __init__(self, url: str) -> None:
            self.url = url

        def search(
            self,
            *,
            collection_name: str,
            query_vector: list[float],
            query_filter: object,
            limit: int,
            with_payload: bool,
            with_vectors: bool,
            timeout: int,
            score_threshold: float | None = None,
        ) -> list[object]:
            captured_search_kwargs.update(
                {
                    "collection_name": collection_name,
                    "query_vector": query_vector,
                    "query_filter": query_filter,
                    "limit": limit,
                    "with_payload": with_payload,
                    "with_vectors": with_vectors,
                    "timeout": timeout,
                    "score_threshold": score_threshold,
                }
            )
            return []

    monkeypatch.setattr("app.services.retrieval.QdrantClient", FakeQdrantClient)
    indexer = QdrantIndexer(
        url="http://qdrant.example.test:6333",
        collection="content_chunks_threshold_test",
        embedding=DeterministicEmbeddingService(dimension=16),
    )

    hits = indexer.search_chunks(query_text="telemetry settings", filters={}, top_k=3)

    assert hits == []
    assert captured_search_kwargs["limit"] == 3
    assert captured_search_kwargs["score_threshold"] == DEFAULT_VECTOR_SCORE_THRESHOLD


def test_memory_indexer_delete_chunk_removes_existing_point_and_ignores_missing() -> None:
    _MEMORY_COLLECTIONS.clear()
    chunk_id = uuid.uuid4()
    indexer = QdrantIndexer(
        url="memory://unit-test-delete",
        collection="content_chunks_delete_test",
        embedding=DeterministicEmbeddingService(dimension=16),
    )

    indexer.ensure_collection()
    point_id = indexer.upsert_chunk(
        chunk_id=chunk_id,
        embed_text="obsolete telemetry settings",
        payload={"content_item_id": str(uuid.uuid4())},
    )
    memory_collection = _MEMORY_COLLECTIONS[(indexer.url, indexer.collection)]
    assert point_id in memory_collection.points

    indexer.delete_chunk(point_id=point_id)
    indexer.delete_chunk(point_id=point_id)

    assert point_id not in memory_collection.points
