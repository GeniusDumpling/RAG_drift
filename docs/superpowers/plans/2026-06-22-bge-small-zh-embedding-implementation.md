# BGE Small Chinese Embedding Integration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a configurable local semantic embedding provider using `BAAI/bge-small-zh-v1.5`, reindex existing chunks into a fresh Qdrant collection, and run the demo with real semantic vector retrieval.

**Architecture:** Keep the deterministic hash embedding provider for tests and fallback, add a lazy `sentence-transformers` provider behind a settings-driven factory, and route both search and worker indexing through that factory. Add a focused reindex service plus CLI script that rebuilds Qdrant vectors from PostgreSQL `content_chunks` and updates chunk vector metadata.

**Tech Stack:** FastAPI, SQLAlchemy 2 async sessions, Pydantic settings, Qdrant client, sentence-transformers, PostgreSQL, pytest, Python CLI scripts.

---

## File Structure

Create or modify these files:

- Modify `pyproject.toml`: add optional local embedding dependency group.
- Modify `.env.example`: document `EMBEDDING_PROVIDER` and `EMBEDDING_MODEL`.
- Modify `README.md`: document BGE local embedding and reindex operation.
- Modify `backend/app/core/config.py`: add embedding settings.
- Modify `backend/app/services/embeddings.py`: add `SentenceTransformerEmbeddingService` and `build_embedding_service`.
- Modify `backend/app/services/search.py`: use `build_embedding_service(settings)` instead of hard-coded deterministic embedding.
- Modify `worker/app/chunk_indexer.py`: use the same embedding factory for worker indexing target metadata.
- Modify `backend/app/services/retrieval.py`: add a Qdrant collection reset helper used by reindexing.
- Create `backend/app/services/embedding_reindex.py`: reindex service that scans chunks, upserts vectors, and updates chunk state.
- Create `scripts/reindex_embeddings.py`: CLI wrapper around the reindex service.
- Create `backend/tests/test_embedding_services.py`: unit tests for embedding settings/factory/provider.
- Create `backend/tests/test_embedding_reindex.py`: reindex tests using memory Qdrant and fake embedding.
- Modify or create focused tests for search/worker factory wiring.

---

## Task 1: Embedding Settings and Service Tests

**Files:**
- Create: `backend/tests/test_embedding_services.py`
- Modify: `backend/app/core/config.py`
- Modify: `backend/app/services/embeddings.py`
- Modify: `pyproject.toml`
- Modify: `.env.example`

- [ ] **Step 1: Write failing embedding settings and provider tests**

Create `backend/tests/test_embedding_services.py` with this content:

```python
from __future__ import annotations

from typing import Any

import pytest

import app.services.embeddings as embeddings
from app.core.config import Settings
from app.services.embeddings import (
    DeterministicEmbeddingService,
    SentenceTransformerEmbeddingService,
    build_embedding_service,
)


class FakeVector:
    def __init__(self, values: list[float]) -> None:
        self.values = values

    def tolist(self) -> list[float]:
        return list(self.values)


class FakeSentenceTransformer:
    instances: list["FakeSentenceTransformer"] = []

    def __init__(self, model_name: str) -> None:
        self.model_name = model_name
        self.encode_calls: list[tuple[str, bool]] = []
        FakeSentenceTransformer.instances.append(self)

    def get_sentence_embedding_dimension(self) -> int:
        return 3

    def encode(self, text: str, *, normalize_embeddings: bool) -> FakeVector:
        self.encode_calls.append((text, normalize_embeddings))
        return FakeVector([0.1, 0.2, 0.3])


def test_embedding_settings_default_to_deterministic() -> None:
    settings = Settings()

    assert settings.embedding_provider == "deterministic"
    assert settings.embedding_model == "deterministic-hash-v1"


def test_build_embedding_service_returns_deterministic_by_default() -> None:
    service = build_embedding_service(Settings())

    assert isinstance(service, DeterministicEmbeddingService)
    assert service.model_name == "deterministic-hash-v1"
    assert service.dimension == 384


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


def test_unsupported_embedding_provider_raises_clear_error() -> None:
    settings = Settings(EMBEDDING_PROVIDER="unknown-provider")

    with pytest.raises(ValueError, match="Unsupported EMBEDDING_PROVIDER"):
        build_embedding_service(settings)


def test_sentence_transformer_import_error_is_clear(monkeypatch: pytest.MonkeyPatch) -> None:
    def raise_import_error() -> Any:
        raise RuntimeError("sentence-transformers is required for EMBEDDING_PROVIDER=sentence-transformers")

    monkeypatch.setattr(embeddings, "_load_sentence_transformer_class", raise_import_error)
    service = SentenceTransformerEmbeddingService("BAAI/bge-small-zh-v1.5")

    with pytest.raises(RuntimeError, match="sentence-transformers is required"):
        service.embed("telemetry")
```

- [ ] **Step 2: Run the new tests to verify they fail**

Run:

```bash
cd /root/intelligence-rag
.venv/bin/python -m pytest backend/tests/test_embedding_services.py -q
```

Expected: FAIL because `Settings.embedding_provider`, `SentenceTransformerEmbeddingService`, and `build_embedding_service` do not exist.

- [ ] **Step 3: Add embedding settings**

Modify `backend/app/core/config.py` by adding these fields after `qdrant_collection`:

```python
    embedding_provider: str = Field(default="deterministic", alias="EMBEDDING_PROVIDER")
    embedding_model: str = Field(default="deterministic-hash-v1", alias="EMBEDDING_MODEL")
```

- [ ] **Step 4: Add sentence-transformers provider and factory**

Replace `backend/app/services/embeddings.py` with this content:

```python
from __future__ import annotations

import hashlib
import math
from typing import Any, Protocol, cast

from app.core.config import Settings

DEFAULT_DETERMINISTIC_MODEL = "deterministic-hash-v1"
DEFAULT_SENTENCE_TRANSFORMERS_MODEL = "BAAI/bge-small-zh-v1.5"


class EmbeddingService(Protocol):
    model_name: str
    dimension: int

    def embed(self, text: str) -> list[float]: ...


class DeterministicEmbeddingService:
    model_name: str = DEFAULT_DETERMINISTIC_MODEL

    def __init__(self, dimension: int = 384) -> None:
        if dimension < 1:
            raise ValueError("dimension must be at least 1")
        self.dimension = dimension

    def embed(self, text: str) -> list[float]:
        values: list[float] = []
        salt_index = 0
        encoded_text = text.encode("utf-8")
        while len(values) < self.dimension:
            digest = hashlib.sha256(salt_index.to_bytes(4, "big") + b"\0" + encoded_text).digest()
            values.extend((byte / 127.5) - 1.0 for byte in digest)
            salt_index += 1

        vector = values[: self.dimension]
        norm = math.sqrt(sum(value * value for value in vector))
        if norm == 0.0:
            return [0.0 for _ in vector]
        return [value / norm for value in vector]


class SentenceTransformerEmbeddingService:
    def __init__(self, model_name: str = DEFAULT_SENTENCE_TRANSFORMERS_MODEL) -> None:
        if not model_name.strip():
            raise ValueError("model_name must not be blank")
        self.model_name = model_name.strip()
        self._model: Any | None = None
        self._dimension: int | None = None

    @property
    def dimension(self) -> int:
        if self._dimension is None:
            raw_dimension = self._loaded_model.get_sentence_embedding_dimension()
            if not isinstance(raw_dimension, int) or raw_dimension < 1:
                raise RuntimeError(f"Invalid embedding dimension for {self.model_name}: {raw_dimension}")
            self._dimension = raw_dimension
        return self._dimension

    def embed(self, text: str) -> list[float]:
        vector = self._loaded_model.encode(text, normalize_embeddings=True)
        if hasattr(vector, "tolist"):
            vector = vector.tolist()
        values = cast(list[float], list(vector))
        if len(values) != self.dimension:
            raise RuntimeError(
                f"Embedding dimension mismatch for {self.model_name}: "
                f"expected {self.dimension}, got {len(values)}"
            )
        return [float(value) for value in values]

    @property
    def _loaded_model(self) -> Any:
        if self._model is None:
            sentence_transformer_class = _load_sentence_transformer_class()
            self._model = sentence_transformer_class(self.model_name)
        return self._model


def build_embedding_service(settings: Settings) -> EmbeddingService:
    provider = settings.embedding_provider.strip().casefold().replace("_", "-")
    if provider == "deterministic":
        return DeterministicEmbeddingService()
    if provider == "sentence-transformers":
        model_name = settings.embedding_model.strip() or DEFAULT_SENTENCE_TRANSFORMERS_MODEL
        return SentenceTransformerEmbeddingService(model_name=model_name)
    raise ValueError(
        "Unsupported EMBEDDING_PROVIDER "
        f"{settings.embedding_provider!r}; expected deterministic or sentence-transformers"
    )


def _load_sentence_transformer_class() -> Any:
    try:
        from sentence_transformers import SentenceTransformer
    except ImportError as exc:
        raise RuntimeError(
            "sentence-transformers is required for EMBEDDING_PROVIDER=sentence-transformers. "
            "Install it with: python -m pip install -e '.[local-embeddings]'"
        ) from exc
    return SentenceTransformer
```

- [ ] **Step 5: Add optional dependency group**

Modify `pyproject.toml` by adding this optional dependency group after the `dev` group:

```toml
local-embeddings = [
  "sentence-transformers==3.3.1"
]
```

Keep the existing `dev` group unchanged.

- [ ] **Step 6: Document embedding env defaults**

Modify `.env.example` by adding these lines after `QDRANT_COLLECTION=content_chunks_v1`:

```env
EMBEDDING_PROVIDER=deterministic
EMBEDDING_MODEL=deterministic-hash-v1
```

- [ ] **Step 7: Run embedding service tests**

Run:

```bash
cd /root/intelligence-rag
.venv/bin/python -m pytest backend/tests/test_embedding_services.py -q
```

Expected: PASS.

- [ ] **Step 8: Commit embedding service foundation**

Run:

```bash
cd /root/intelligence-rag
git add pyproject.toml .env.example backend/app/core/config.py backend/app/services/embeddings.py backend/tests/test_embedding_services.py
git commit -m "feat: add configurable embedding providers"
```

---

## Task 2: Wire Search and Worker Indexing to the Embedding Factory

**Files:**
- Modify: `backend/app/services/search.py`
- Modify: `worker/app/chunk_indexer.py`
- Create: `backend/tests/test_embedding_factory_wiring.py`

- [ ] **Step 1: Write failing factory wiring tests**

Create `backend/tests/test_embedding_factory_wiring.py` with this content:

```python
from __future__ import annotations

from typing import Any

import app.services.search as search_module
import worker.app.chunk_indexer as chunk_indexer
from app.core.config import Settings
from app.services.search import SearchService


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
```

- [ ] **Step 2: Run wiring tests to verify they fail**

Run:

```bash
cd /root/intelligence-rag
.venv/bin/python -m pytest backend/tests/test_embedding_factory_wiring.py -q
```

Expected: FAIL because search and worker modules do not import or use `build_embedding_service` yet.

- [ ] **Step 3: Wire SearchService to the factory**

In `backend/app/services/search.py`, change the import:

```python
from app.services.embeddings import DeterministicEmbeddingService, EmbeddingService
```

to:

```python
from app.services.embeddings import EmbeddingService, build_embedding_service
```

Then change this line inside `SearchService.__init__`:

```python
        self.embedding = embedding or DeterministicEmbeddingService()
```

to:

```python
        self.embedding = embedding or build_embedding_service(self.settings)
```

- [ ] **Step 4: Wire worker indexing to the factory**

In `worker/app/chunk_indexer.py`, change the embedding import from deterministic-only to factory import.

Replace:

```python
from app.services.embeddings import DeterministicEmbeddingService
```

with:

```python
from app.services.embeddings import build_embedding_service
```

Replace `_build_qdrant_indexer` with:

```python
def _build_qdrant_indexer() -> QdrantIndexer:
    settings = get_settings()
    return QdrantIndexer(
        url=settings.qdrant_url,
        collection=settings.qdrant_collection,
        embedding=build_embedding_service(settings),
    )
```

Replace `_current_vector_index_target` with:

```python
def _current_vector_index_target() -> VectorIndexTarget:
    settings = get_settings()
    embedding = build_embedding_service(settings)
    return VectorIndexTarget(
        backend_name=_vector_backend_name(settings.qdrant_url),
        metadata={
            "vector_collection": settings.qdrant_collection,
            "vector_store_id": _vector_store_id(settings.qdrant_url),
            "embedding_model": embedding.model_name,
            "embedding_dimension": embedding.dimension,
        },
    )
```

- [ ] **Step 5: Run factory wiring tests**

Run:

```bash
cd /root/intelligence-rag
.venv/bin/python -m pytest backend/tests/test_embedding_factory_wiring.py -q
```

Expected: PASS.

- [ ] **Step 6: Run focused search and worker regressions**

Run:

```bash
cd /root/intelligence-rag
.venv/bin/python -m pytest backend/tests/test_search_pipeline.py worker/tests/test_worker_runner.py -q
```

Expected: PASS. These tests continue to use deterministic defaults unless they inject custom indexers.

- [ ] **Step 7: Commit factory wiring**

Run:

```bash
cd /root/intelligence-rag
git add backend/app/services/search.py worker/app/chunk_indexer.py backend/tests/test_embedding_factory_wiring.py
git commit -m "feat: use configured embedding service for search and indexing"
```

---

## Task 3: Add Qdrant Collection Reset Support

**Files:**
- Modify: `backend/app/services/retrieval.py`
- Modify: `backend/tests/test_chunking_and_indexing.py`

- [ ] **Step 1: Write failing reset-collection tests**

Append these tests to `backend/tests/test_chunking_and_indexing.py`:

```python

def test_memory_indexer_recreate_collection_clears_existing_points() -> None:
    _MEMORY_COLLECTIONS.clear()
    chunk_id = uuid.uuid4()
    indexer = QdrantIndexer(
        url="memory://unit-test-reset",
        collection="content_chunks_reset_test",
        embedding=DeterministicEmbeddingService(dimension=16),
    )
    indexer.ensure_collection()
    indexer.upsert_chunk(
        chunk_id=chunk_id,
        embed_text="telemetry settings",
        payload={"content_item_id": str(uuid.uuid4())},
    )

    indexer.recreate_collection()

    collection = _MEMORY_COLLECTIONS[(indexer.url, indexer.collection)]
    assert collection.dimension == 16
    assert collection.points == {}


def test_qdrant_indexer_recreate_collection_deletes_then_creates_remote_collection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, str]] = []

    class FakeQdrantClient:
        def __init__(self, url: str) -> None:
            self.url = url

        def collection_exists(self, *, collection_name: str) -> bool:
            calls.append(("exists", collection_name))
            return True

        def delete_collection(self, *, collection_name: str) -> None:
            calls.append(("delete", collection_name))

        def create_collection(self, *, collection_name: str, vectors_config: object) -> None:
            calls.append(("create", collection_name))

    monkeypatch.setattr("app.services.retrieval.QdrantClient", FakeQdrantClient)
    indexer = QdrantIndexer(
        url="http://qdrant.example.test:6333",
        collection="content_chunks_reset_remote_test",
        embedding=DeterministicEmbeddingService(dimension=16),
    )

    indexer.recreate_collection()

    assert calls == [
        ("exists", "content_chunks_reset_remote_test"),
        ("delete", "content_chunks_reset_remote_test"),
        ("create", "content_chunks_reset_remote_test"),
    ]
```

- [ ] **Step 2: Run reset tests to verify they fail**

Run:

```bash
cd /root/intelligence-rag
.venv/bin/python -m pytest \
  backend/tests/test_chunking_and_indexing.py::test_memory_indexer_recreate_collection_clears_existing_points \
  backend/tests/test_chunking_and_indexing.py::test_qdrant_indexer_recreate_collection_deletes_then_creates_remote_collection -q
```

Expected: FAIL because `QdrantIndexer.recreate_collection` does not exist.

- [ ] **Step 3: Implement `QdrantIndexer.recreate_collection`**

In `backend/app/services/retrieval.py`, add this method inside `class QdrantIndexer` immediately after `ensure_collection`:

```python
    def recreate_collection(self) -> None:
        if self.backend_name == "memory":
            _MEMORY_COLLECTIONS[(self.url, self.collection)] = _MemoryCollection(
                dimension=self.embedding.dimension
            )
            return

        client = self._require_client()
        if client.collection_exists(collection_name=self.collection):
            client.delete_collection(collection_name=self.collection)
        client.create_collection(
            collection_name=self.collection,
            vectors_config=qdrant_models.VectorParams(
                size=self.embedding.dimension,
                distance=qdrant_models.Distance.COSINE,
            ),
        )
```

- [ ] **Step 4: Run reset tests**

Run:

```bash
cd /root/intelligence-rag
.venv/bin/python -m pytest \
  backend/tests/test_chunking_and_indexing.py::test_memory_indexer_recreate_collection_clears_existing_points \
  backend/tests/test_chunking_and_indexing.py::test_qdrant_indexer_recreate_collection_deletes_then_creates_remote_collection -q
```

Expected: PASS.

- [ ] **Step 5: Run chunking/indexing regression tests**

Run:

```bash
cd /root/intelligence-rag
.venv/bin/python -m pytest backend/tests/test_chunking_and_indexing.py -q
```

Expected: PASS.

- [ ] **Step 6: Commit Qdrant reset helper**

Run:

```bash
cd /root/intelligence-rag
git add backend/app/services/retrieval.py backend/tests/test_chunking_and_indexing.py
git commit -m "feat: support vector collection reset"
```

---

## Task 4: Add Embedding Reindex Service and CLI

**Files:**
- Create: `backend/app/services/embedding_reindex.py`
- Create: `scripts/reindex_embeddings.py`
- Create: `backend/tests/test_embedding_reindex.py`

- [ ] **Step 1: Write failing reindex tests**

Create `backend/tests/test_embedding_reindex.py` with this content:

```python
from __future__ import annotations

from datetime import UTC, datetime

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.content import ContentChunk, ContentItem, RawPage
from app.models.control import CrawlJob, CrawlRun, SourceSite
from app.services.embedding_reindex import reindex_embeddings
from app.services.retrieval import _MEMORY_COLLECTIONS


class FakeEmbedding:
    model_name = "fake-bge-small-zh"
    dimension = 4

    def embed(self, text: str) -> list[float]:
        if "fail-me" in text:
            raise RuntimeError("fake embedding failure")
        return [1.0, 0.0, 0.0, 0.0]


async def _seed_chunk(session: AsyncSession, *, embed_text: str = "遥控器 图传 设置") -> ContentChunk:
    now = datetime(2026, 6, 22, 12, 0, tzinfo=UTC)
    source = SourceSite(
        name="BGE Test Source",
        site_type="forum",
        base_url="https://example.test",
        allowed_domains=["example.test"],
        fetch_mode="http",
        default_language="zh",
        active=True,
        config_json={},
    )
    session.add(source)
    await session.flush()

    job = CrawlJob(
        source_site_id=source.id,
        name="BGE Test Job",
        trigger_mode="manual",
        cron_expr=None,
        seed_config_json={},
        parser_profile="test",
        max_pages=1,
        enabled=True,
        agent_policy_json={},
    )
    session.add(job)
    await session.flush()

    run = CrawlRun(
        source_site_id=source.id,
        crawl_job_id=job.id,
        trigger_type="manual",
        execution_mode="agent_assisted",
        seed_url="https://example.test/thread/1",
        status="success",
        started_at=now,
        finished_at=now,
        discovered_count=1,
        fetched_count=1,
        parsed_count=1,
        extracted_count=1,
        deduped_count=0,
        chunked_count=1,
        embedded_count=0,
        error_count=0,
        config_snapshot_json={},
        error_message=None,
        created_at=now,
    )
    session.add(run)
    await session.flush()

    raw_page = RawPage(
        source_site_id=source.id,
        crawl_run_id=run.id,
        requested_url="https://example.test/thread/1",
        final_url="https://example.test/thread/1",
        http_status=200,
        content_type="text/html",
        response_headers_json={},
        raw_html="<p>遥控器 图传 设置</p>",
        raw_text="遥控器 图传 设置",
        raw_json={},
        fetched_at=now,
        fetch_error=None,
        parser_profile="test",
        extraction_method="test",
        extraction_confidence=0.9,
        parse_status="success",
        parse_error=None,
        body_hash="raw-hash",
        created_at=now,
    )
    session.add(raw_page)
    await session.flush()

    item = ContentItem(
        source_site_id=source.id,
        raw_page_id=raw_page.id,
        crawl_run_id=run.id,
        author_id=None,
        parent_item_id=None,
        thread_root_id=None,
        item_type="thread",
        title="遥控器图传设置",
        canonical_url="https://example.test/thread/1",
        source_url="https://example.test/thread/1",
        published_at=now,
        language="zh",
        raw_text="遥控器 图传 设置",
        cleaned_text="遥控器 图传 设置",
        summary_text="图传设置摘要",
        structured_by="test",
        extraction_confidence=0.9,
        tags=["dji", "图传"],
        metadata_json={},
        content_hash="content-hash",
        dedup_key=f"dedup-{embed_text}",
        search_tsv=None,
    )
    session.add(item)
    await session.flush()

    chunk = ContentChunk(
        content_item_id=item.id,
        chunk_index=0,
        char_start=0,
        char_end=8,
        display_text="遥控器 图传 设置",
        embed_text=embed_text,
        token_count=4,
        chunk_metadata_json={"chunker_version": "test"},
        qdrant_point_id=None,
        vector_backend=None,
        vector_point_id=None,
        embedded_at=None,
        embed_status="pending",
        embed_error=None,
    )
    session.add(chunk)
    await session.commit()
    await session.refresh(chunk)
    return chunk


@pytest.mark.asyncio
async def test_reindex_embeddings_indexes_pending_chunk_and_updates_metadata(
    db_session: AsyncSession,
) -> None:
    _MEMORY_COLLECTIONS.clear()
    chunk = await _seed_chunk(db_session)

    summary = await reindex_embeddings(
        db_session,
        qdrant_url="memory://bge-reindex-test",
        collection="content_chunks_bge_small_zh_v1_test",
        embedding=FakeEmbedding(),
        reset_collection=True,
    )

    refreshed = await db_session.scalar(select(ContentChunk).where(ContentChunk.id == chunk.id))
    assert refreshed is not None
    assert summary.total == 1
    assert summary.succeeded == 1
    assert summary.failed == 0
    assert summary.collection == "content_chunks_bge_small_zh_v1_test"
    assert summary.model == "fake-bge-small-zh"
    assert summary.dimension == 4
    assert refreshed.embed_status == "success"
    assert refreshed.embed_error is None
    assert refreshed.vector_backend == "memory"
    assert refreshed.vector_point_id == str(chunk.id)
    assert refreshed.qdrant_point_id is None
    assert refreshed.embedded_at is not None
    assert refreshed.chunk_metadata_json["embedding_model"] == "fake-bge-small-zh"
    assert refreshed.chunk_metadata_json["embedding_dimension"] == 4
    assert refreshed.chunk_metadata_json["vector_collection"] == "content_chunks_bge_small_zh_v1_test"
    memory_collection = _MEMORY_COLLECTIONS[("memory://bge-reindex-test", "content_chunks_bge_small_zh_v1_test")]
    assert str(chunk.id) in memory_collection.points
    assert memory_collection.points[str(chunk.id)].payload["source_site_id"]
    assert memory_collection.points[str(chunk.id)].payload["tags"] == ["dji", "图传"]


@pytest.mark.asyncio
async def test_reindex_embeddings_marks_failed_chunk_and_continues(db_session: AsyncSession) -> None:
    _MEMORY_COLLECTIONS.clear()
    failing_chunk = await _seed_chunk(db_session, embed_text="fail-me")

    summary = await reindex_embeddings(
        db_session,
        qdrant_url="memory://bge-reindex-failure-test",
        collection="content_chunks_bge_failure_test",
        embedding=FakeEmbedding(),
        reset_collection=True,
    )

    refreshed = await db_session.scalar(select(ContentChunk).where(ContentChunk.id == failing_chunk.id))
    assert refreshed is not None
    assert summary.total == 1
    assert summary.succeeded == 0
    assert summary.failed == 1
    assert refreshed.embed_status == "failed"
    assert refreshed.vector_backend == "memory"
    assert refreshed.vector_point_id is None
    assert refreshed.qdrant_point_id is None
    assert refreshed.embedded_at is None
    assert refreshed.embed_error is not None
    assert "fake embedding failure" in refreshed.embed_error
```

- [ ] **Step 2: Run reindex tests to verify they fail**

Run:

```bash
cd /root/intelligence-rag
.venv/bin/python -m pytest backend/tests/test_embedding_reindex.py -q
```

Expected: FAIL because `app.services.embedding_reindex` does not exist.

- [ ] **Step 3: Implement reindex service**

Create `backend/app/services/embedding_reindex.py` with this content:

```python
from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.base import utcnow
from app.models.content import ContentChunk, ContentItem
from app.services.embeddings import EmbeddingService
from app.services.retrieval import QdrantIndexer

MAX_EMBED_ERROR_CHARS = 1000


@dataclass(frozen=True)
class ReindexSummary:
    total: int
    succeeded: int
    failed: int
    provider: str
    model: str
    dimension: int
    collection: str
    backend_name: str
    reset_collection: bool

    def to_json_dict(self) -> dict[str, Any]:
        return asdict(self)


async def reindex_embeddings(
    session: AsyncSession,
    *,
    qdrant_url: str,
    collection: str,
    embedding: EmbeddingService,
    reset_collection: bool,
) -> ReindexSummary:
    indexer = QdrantIndexer(url=qdrant_url, collection=collection, embedding=embedding)
    if reset_collection:
        indexer.recreate_collection()
    else:
        indexer.ensure_collection()

    rows = (
        await session.execute(
            select(ContentChunk, ContentItem)
            .join(ContentItem, ContentChunk.content_item_id == ContentItem.id)
            .where(ContentChunk.embed_status != "obsolete")
            .order_by(ContentChunk.id.asc())
        )
    ).all()

    succeeded = 0
    failed = 0
    for content_chunk, content_item in rows:
        try:
            point_id = indexer.upsert_chunk(
                chunk_id=content_chunk.id,
                embed_text=content_chunk.embed_text,
                payload=_build_payload(content_chunk=content_chunk, content_item=content_item),
            )
            _mark_success(
                content_chunk=content_chunk,
                point_id=point_id,
                backend_name=indexer.backend_name,
                embedding=embedding,
                collection=collection,
            )
            succeeded += 1
        except Exception as exc:  # noqa: BLE001 - per-chunk failure must be recorded.
            _mark_failed(
                content_chunk=content_chunk,
                backend_name=indexer.backend_name,
                error_message=str(exc),
            )
            failed += 1

    await session.commit()
    return ReindexSummary(
        total=len(rows),
        succeeded=succeeded,
        failed=failed,
        provider=_provider_name(embedding),
        model=embedding.model_name,
        dimension=embedding.dimension,
        collection=collection,
        backend_name=indexer.backend_name,
        reset_collection=reset_collection,
    )


def _build_payload(*, content_chunk: ContentChunk, content_item: ContentItem) -> dict[str, Any]:
    return {
        "chunk_id": str(content_chunk.id),
        "content_item_id": str(content_item.id),
        "source_site_id": str(content_item.source_site_id),
        "item_type": content_item.item_type,
        "canonical_url": content_item.canonical_url,
        "title": content_item.title,
        "published_at": content_item.published_at.isoformat() if content_item.published_at else None,
        "language": content_item.language,
        "tags": list(content_item.tags),
    }


def _mark_success(
    *,
    content_chunk: ContentChunk,
    point_id: str,
    backend_name: str,
    embedding: EmbeddingService,
    collection: str,
) -> None:
    content_chunk.embed_status = "success"
    content_chunk.embed_error = None
    content_chunk.vector_backend = backend_name
    content_chunk.vector_point_id = point_id
    content_chunk.qdrant_point_id = point_id if backend_name == "qdrant" else None
    content_chunk.embedded_at = utcnow()
    content_chunk.chunk_metadata_json = {
        **content_chunk.chunk_metadata_json,
        "vector_collection": collection,
        "embedding_model": embedding.model_name,
        "embedding_dimension": embedding.dimension,
    }


def _mark_failed(*, content_chunk: ContentChunk, backend_name: str, error_message: str) -> None:
    content_chunk.embed_status = "failed"
    content_chunk.embed_error = error_message[:MAX_EMBED_ERROR_CHARS]
    content_chunk.vector_backend = backend_name
    content_chunk.vector_point_id = None
    content_chunk.qdrant_point_id = None
    content_chunk.embedded_at = None


def _provider_name(embedding: EmbeddingService) -> str:
    class_name = embedding.__class__.__name__
    if class_name == "SentenceTransformerEmbeddingService":
        return "sentence-transformers"
    if class_name == "DeterministicEmbeddingService":
        return "deterministic"
    return class_name
```

- [ ] **Step 4: Implement CLI script**

Create `scripts/reindex_embeddings.py` with this content:

```python
#!/usr/bin/env python3
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
BACKEND_DIR = PROJECT_ROOT / "backend"

for import_path in (PROJECT_ROOT, BACKEND_DIR):
    import_path_str = str(import_path)
    if import_path_str not in sys.path:
        sys.path.insert(0, import_path_str)

from app.core.config import get_settings  # noqa: E402
from app.db.session import AsyncSessionLocal  # noqa: E402
from app.services.embedding_reindex import reindex_embeddings  # noqa: E402
from app.services.embeddings import build_embedding_service  # noqa: E402


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Rebuild Qdrant vectors for existing content chunks.")
    parser.add_argument(
        "--collection",
        default=None,
        help="Target Qdrant collection. Defaults to QDRANT_COLLECTION from settings.",
    )
    parser.add_argument(
        "--reset-collection",
        action="store_true",
        help="Delete and recreate only the target collection before indexing.",
    )
    return parser.parse_args()


async def run() -> int:
    args = parse_args()
    settings = get_settings()
    embedding = build_embedding_service(settings)
    collection = args.collection or settings.qdrant_collection
    async with AsyncSessionLocal() as session:
        summary = await reindex_embeddings(
            session,
            qdrant_url=settings.qdrant_url,
            collection=collection,
            embedding=embedding,
            reset_collection=args.reset_collection,
        )
    print(json.dumps(summary.to_json_dict(), sort_keys=True, ensure_ascii=False))
    return 1 if summary.failed else 0


def main() -> int:
    return asyncio.run(run())


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 5: Run reindex tests**

Run:

```bash
cd /root/intelligence-rag
.venv/bin/python -m pytest backend/tests/test_embedding_reindex.py -q
```

Expected: PASS.

- [ ] **Step 6: Run script help**

Run:

```bash
cd /root/intelligence-rag
.venv/bin/python scripts/reindex_embeddings.py --help
```

Expected: command prints usage text containing `--collection` and `--reset-collection`.

- [ ] **Step 7: Commit reindex service and CLI**

Run:

```bash
cd /root/intelligence-rag
git add backend/app/services/embedding_reindex.py scripts/reindex_embeddings.py backend/tests/test_embedding_reindex.py
git commit -m "feat: add embedding reindex command"
```

---

## Task 5: Documentation and Regression Verification

**Files:**
- Modify: `README.md`
- Test: backend and worker suites

- [ ] **Step 1: Document BGE local embedding mode**

Add this section to `README.md` after the existing vector backend configuration section:

```markdown
### 本地语义 Embedding 配置

默认配置使用 `EMBEDDING_PROVIDER=deterministic`，这是可复现的 hash 向量，只用于测试和演示链路。若要启用真实语义向量检索，可以安装本地 embedding 依赖并使用 BGE small zh：

```bash
source .venv/bin/activate
python -m pip install -e ".[dev,local-embeddings]"
```

然后设置：

```bash
export EMBEDDING_PROVIDER=sentence-transformers
export EMBEDDING_MODEL=BAAI/bge-small-zh-v1.5
export QDRANT_COLLECTION=content_chunks_bge_small_zh_v1
```

第一次运行会下载模型文件。切换 embedding 模型后必须重建 Qdrant 向量索引，因为旧 collection 中的向量维度和语义空间不同：

```bash
python3 scripts/reindex_embeddings.py --reset-collection
```

回滚到 deterministic demo 模式：

```bash
export EMBEDDING_PROVIDER=deterministic
export EMBEDDING_MODEL=deterministic-hash-v1
export QDRANT_COLLECTION=content_chunks_v1
```
```

- [ ] **Step 2: Run focused backend tests**

Run:

```bash
cd /root/intelligence-rag
.venv/bin/python -m pytest \
  backend/tests/test_embedding_services.py \
  backend/tests/test_embedding_factory_wiring.py \
  backend/tests/test_embedding_reindex.py \
  backend/tests/test_chunking_and_indexing.py \
  backend/tests/test_search_pipeline.py \
  backend/tests/test_database_api.py -q
```

Expected: PASS.

- [ ] **Step 3: Run worker regression tests**

Run:

```bash
cd /root/intelligence-rag
.venv/bin/python -m pytest worker/tests/test_worker_runner.py -q
```

Expected: PASS.

- [ ] **Step 4: Commit docs and verification updates**

Run:

```bash
cd /root/intelligence-rag
git add README.md
git commit -m "docs: document local bge embedding mode"
```

If `README.md` is the only changed file. If source files changed during verification, inspect `git diff` and commit only intentional changes.

---

## Task 6: Install BGE Dependencies and Reindex the Live Demo

**Files:**
- Runtime dependency installation in `/root/intelligence-rag/.venv`
- Runtime services/logs only

- [ ] **Step 1: Install local embedding dependencies**

Run:

```bash
cd /root/intelligence-rag
.venv/bin/python -m pip install -e ".[dev,local-embeddings]"
```

Expected: install succeeds and `sentence_transformers` imports.

Verify:

```bash
cd /root/intelligence-rag
.venv/bin/python - <<'PY'
from sentence_transformers import SentenceTransformer
print(SentenceTransformer)
PY
```

Expected: prints the class path without ImportError.

- [ ] **Step 2: Stop the API before reindexing live collection**

Stop only the process listening on port `18000`:

```bash
port18000_pids=$(ss -ltnp 'sport = :18000' 2>/dev/null | grep -oE 'pid=[0-9]+' | cut -d= -f2 | sort -u || true)
if [ -n "$port18000_pids" ]; then kill $port18000_pids; fi
```

Expected: no listener remains on `127.0.0.1:18000`.

- [ ] **Step 3: Reindex with BGE small zh into a fresh collection**

Run:

```bash
cd /root/intelligence-rag
EMBEDDING_PROVIDER=sentence-transformers \
EMBEDDING_MODEL=BAAI/bge-small-zh-v1.5 \
QDRANT_COLLECTION=content_chunks_bge_small_zh_v1 \
.venv/bin/python scripts/reindex_embeddings.py --reset-collection | tee /tmp/intel-rag-bge-reindex.json
```

Expected JSON summary:

- `failed` is `0`.
- `succeeded` equals the number of non-obsolete chunks.
- `model` is `BAAI/bge-small-zh-v1.5`.
- `dimension` is `512`.
- `collection` is `content_chunks_bge_small_zh_v1`.

- [ ] **Step 4: Restart API with BGE settings**

Run:

```bash
cd /root/intelligence-rag
mkdir -p /root/logs
nohup /root/intelligence-rag/.venv/bin/uvicorn app.main:app \
  --app-dir /root/intelligence-rag/backend \
  --host 127.0.0.1 --port 18000 \
  > /root/logs/intel-rag-api-18000.log 2>&1 &
```

Use these environment variables when starting the command:

```bash
EMBEDDING_PROVIDER=sentence-transformers
EMBEDDING_MODEL=BAAI/bge-small-zh-v1.5
QDRANT_COLLECTION=content_chunks_bge_small_zh_v1
```

The final command should be:

```bash
cd /root/intelligence-rag
EMBEDDING_PROVIDER=sentence-transformers \
EMBEDDING_MODEL=BAAI/bge-small-zh-v1.5 \
QDRANT_COLLECTION=content_chunks_bge_small_zh_v1 \
nohup /root/intelligence-rag/.venv/bin/uvicorn app.main:app \
  --app-dir /root/intelligence-rag/backend \
  --host 127.0.0.1 --port 18000 \
  > /root/logs/intel-rag-api-18000.log 2>&1 &
```

Then wait until:

```bash
curl -fsS http://127.0.0.1:18000/health
```

Expected: `{"status":"ok","service":"intelligence-rag-api"}`.

- [ ] **Step 5: Restart frontend proxy if needed**

If port 80 proxy is not running, start it with:

```bash
cd /root/intelligence-rag
nohup python3 /root/intelligence-rag/skills/dji-lito-demo-ingestion/scripts/demo_proxy.py \
  --host 0.0.0.0 --port 80 \
  --dist /root/intelligence-rag/frontend/dist \
  --api http://127.0.0.1:18000 \
  > /root/logs/intel-rag-frontend-demo-80.log 2>&1 &
```

Expected: `curl -fsS http://127.0.0.1/health` returns API health through the proxy.

- [ ] **Step 6: Verify database overview and public demo**

Run:

```bash
curl -fsS http://127.0.0.1:18000/database/overview | python3 -c "import json,sys; p=json.load(sys.stdin); print(p['qdrant']['collection'], p['qdrant']['vector_size'], p['reconciliation']['status'])"
curl -fsS http://62.234.15.249/database/overview | python3 -c "import json,sys; p=json.load(sys.stdin); print(p['qdrant']['collection'], p['qdrant']['vector_size'], p['reconciliation']['status'])"
```

Expected:

```text
content_chunks_bge_small_zh_v1 512 matched
content_chunks_bge_small_zh_v1 512 matched
```

- [ ] **Step 7: Verify vector search trace**

Run:

```bash
curl -fsS -X POST http://127.0.0.1:18000/search \
  -H 'Content-Type: application/json' \
  -d '{"query":"遥控器 图传 设置","mode":"search","filters":{},"top_k":5}' \
  | python3 -c "import json,sys; p=json.load(sys.stdin); print(p['query']['query_trace_json']['retrieval']['vector']); print([(e['matched_by'], e['score']) for e in p['evidence']])"
```

Expected:

- Vector trace has `attempted: True`.
- Vector trace has `failed: False`.
- Vector `hit_count` is greater than or equal to `1` if indexed corpus has semantically related chunks.
- Evidence list is non-empty.

- [ ] **Step 8: Commit any final intentional source changes**

Run:

```bash
cd /root/intelligence-rag
git status --short
git diff --stat
```

Expected: no unexpected tracked source changes. Runtime logs, model cache files, and frontend `dist/` should not be committed.

---

## Self-Review Checklist

- Spec coverage:
  - Configurable deterministic/sentence-transformers provider: Tasks 1 and 2.
  - BGE model selection: Tasks 1 and 6.
  - New Qdrant collection and reset: Tasks 3 and 6.
  - Reindex existing chunks and update metadata: Task 4.
  - Search and worker use the same configured embedding: Task 2.
  - Runtime rollout and verification: Task 6.
  - Rollback documentation: Task 5.
- Placeholder scan: this plan contains no incomplete placeholders or unspecified implementation step.
- Type consistency:
  - `EmbeddingService` exposes `model_name`, `dimension`, and `embed(text)`.
  - `ReindexSummary` fields match CLI JSON verification steps.
  - `EMBEDDING_PROVIDER` and `EMBEDDING_MODEL` names match `Settings` aliases and runtime commands.
