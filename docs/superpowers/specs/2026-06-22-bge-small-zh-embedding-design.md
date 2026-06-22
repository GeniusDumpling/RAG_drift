# BGE Small Chinese Embedding Integration Design

## Goal

Replace the demo-only deterministic hash embedding path with a configurable local semantic embedding provider for the intelligence RAG prototype, and run the public demo on `BAAI/bge-small-zh-v1.5` without requiring paid embedding APIs.

The existing deterministic embedding remains available for tests, memory-vector smoke paths, and fast local fallback. The deployed demo will use BGE embeddings and a fresh Qdrant collection so vector search behaves like real semantic retrieval.

## Non-goals

- Do not add a paid embedding API provider in this change.
- Do not add a complex embedding job queue or distributed reindex workflow.
- Do not change crawler or extraction behavior.
- Do not introduce frontend model selection controls.
- Do not delete the old Qdrant collection as part of normal migration.

## Current state

The project currently uses `DeterministicEmbeddingService`, a stable hash-based vector generator with 384 dimensions. It is useful for repeatable tests and demo plumbing, but it does not encode natural-language semantics.

Both indexing and search use the same embedding abstraction. Search currently constructs `DeterministicEmbeddingService()` directly in `SearchService`, while ingestion/indexing code passes an embedding service into `QdrantIndexer`. Qdrant currently stores vectors in `content_chunks_v1`, which is sized for the deterministic 384-dimensional vectors.

## Proposed approach

Use a configurable embedding provider factory:

```text
EMBEDDING_PROVIDER=deterministic | sentence-transformers
EMBEDDING_MODEL=deterministic-hash-v1 | BAAI/bge-small-zh-v1.5
```

For the public demo, use:

```text
EMBEDDING_PROVIDER=sentence-transformers
EMBEDDING_MODEL=BAAI/bge-small-zh-v1.5
QDRANT_COLLECTION=content_chunks_bge_small_zh_v1
```

This keeps the old deterministic path intact while allowing the live service to use real semantic vectors.

## Components

### Embedding services

`backend/app/services/embeddings.py` will provide:

- `EmbeddingService` protocol, unchanged.
- `DeterministicEmbeddingService`, unchanged for deterministic tests.
- `SentenceTransformerEmbeddingService`, new lazy local provider backed by `sentence-transformers`.
- `build_embedding_service(settings)`, new factory that chooses the provider from settings.

`SentenceTransformerEmbeddingService` will:

- Load `BAAI/bge-small-zh-v1.5` through `sentence_transformers.SentenceTransformer`.
- Expose the model name through `model_name`.
- Expose the model dimension, expected to be 512 for BGE small zh.
- Return normalized vectors using `encode(..., normalize_embeddings=True)`.
- Keep imports lazy so environments using deterministic mode do not need to import the heavy dependency on every test path.

### Settings

`backend/app/core/config.py` will add:

```python
embedding_provider: str = Field(default="deterministic", alias="EMBEDDING_PROVIDER")
embedding_model: str = Field(default="deterministic-hash-v1", alias="EMBEDDING_MODEL")
```

`.env.example` and documentation will describe deterministic and BGE modes.

### Search path

`SearchService` will stop hard-coding `DeterministicEmbeddingService()` and will call `build_embedding_service(settings)` when no explicit embedding service is injected.

Tests can still inject fake or deterministic embedding services directly.

### Reindex script

Add `scripts/reindex_embeddings.py` as a single-process operational script for the prototype.

Required behavior:

1. Load settings.
2. Build the configured embedding service.
3. Use the configured or CLI-provided Qdrant collection.
4. Optionally reset the target collection with `--reset-collection`.
5. Iterate PostgreSQL `content_chunks`, using `content_chunks.embed_text` as embedding input.
6. Upsert each vector into Qdrant with payload fields used by search filters:
   - `chunk_id`
   - `content_item_id`
   - `source_site_id`
   - `item_type`
   - `language`
   - `tags`
   - `published_at`
7. Update each chunk in PostgreSQL:
   - `embed_status = "success"` on success.
   - `vector_backend = "qdrant"` for real Qdrant, or `"memory"` for explicit memory backends.
   - `qdrant_point_id = str(chunk.id)`.
   - `vector_point_id = str(chunk.id)`.
   - `embedded_at = now`.
   - `embed_error = None` on success.
   - `chunk_metadata_json.embedding_model = embedding.model_name`.
   - `chunk_metadata_json.embedding_dimension = embedding.dimension`.
   - `chunk_metadata_json.vector_collection = target collection`.
8. On per-chunk failure, mark only that chunk failed with `embed_status = "failed"` and a truncated `embed_error`, then continue.
9. Print JSON summary with total, success, failed, provider, model, dimension, collection, and reset flag.

### Qdrant collection strategy

Use a new collection instead of overwriting the existing deterministic collection:

```text
content_chunks_bge_small_zh_v1
```

Rationale:

- Avoids dimension mismatch with existing 384-dimensional vectors.
- Preserves old collection for rollback.
- Makes database/Qdrant reconciliation explicit in the Database page.

The reindex script may delete only the target collection when `--reset-collection` is passed.

### Dependencies

Add a local embedding optional path to project dependencies. For this prototype deployment, installing the dependency into the existing virtual environment is acceptable.

Expected Python dependencies:

- `sentence-transformers`
- its transitive PyTorch dependency

The implementation should not require these dependencies for deterministic-mode tests that mock the sentence-transformer provider.

## Operational rollout

Rollout sequence for the current server:

1. Install updated Python dependencies in `/root/intelligence-rag/.venv`.
2. Set runtime environment for API and reindex:
   - `EMBEDDING_PROVIDER=sentence-transformers`
   - `EMBEDDING_MODEL=BAAI/bge-small-zh-v1.5`
   - `QDRANT_COLLECTION=content_chunks_bge_small_zh_v1`
3. Run:

```bash
python3 scripts/reindex_embeddings.py --reset-collection
```

4. Restart API with the same embedding and Qdrant collection environment.
5. Keep frontend proxy serving existing static assets unless source changes require rebuild.
6. Verify:
   - `/health` returns ok.
   - `/database/overview` shows `qdrant.collection = content_chunks_bge_small_zh_v1`.
   - `/database/overview` reconciliation status is `matched`.
   - Search query trace shows vector retrieval attempted and not failed.
   - Evidence includes `matched_by` values of `vector` or `hybrid` for semantic matches when available.

## Error handling

- Unsupported `EMBEDDING_PROVIDER` raises a clear `ValueError`.
- Missing `sentence-transformers` dependency raises a clear runtime error only when the provider is selected.
- Qdrant failures during reindex are recorded per chunk and summarized; the script exits non-zero if any chunk fails.
- Search keeps existing behavior: vector retrieval errors are traced and SQL keyword retrieval continues.
- The reindex script redacts connection details in user-facing summaries and prints no secrets.

## Testing plan

Backend tests:

- Factory returns deterministic provider by default.
- Factory returns a sentence-transformers provider when configured, with `SentenceTransformer` mocked.
- Sentence-transformers vectors are converted to Python lists and normalized by the model call contract.
- `SearchService` uses the embedding factory rather than hard-coded deterministic service.
- Reindex script updates chunk vector status and metadata using fake embeddings and a memory vector backend.
- Reindex script supports `--reset-collection` without affecting unrelated collections.

Regression tests:

- Existing chunking/indexing tests pass.
- Existing search pipeline tests pass.
- Existing database overview tests pass.

Manual verification:

- Install dependencies.
- Download/load `BAAI/bge-small-zh-v1.5`.
- Reindex current chunks.
- Restart demo API.
- Verify public frontend search and Database page.

## Security and privacy

The selected BGE model runs locally. No content is sent to a third-party embedding API. The model download itself contacts the model hosting service, but only to download model artifacts. The implementation must not print database credentials, API tokens, or raw secrets.

## Rollback

To roll back, restart the API with:

```text
EMBEDDING_PROVIDER=deterministic
EMBEDDING_MODEL=deterministic-hash-v1
QDRANT_COLLECTION=content_chunks_v1
```

The previous collection remains available unless explicitly deleted outside this plan.

## Open decisions resolved

- Use `BAAI/bge-small-zh-v1.5` first, not `bge-m3`.
- Use local model inference, not a paid embedding API.
- Use a new Qdrant collection, not in-place overwrite.
- Keep deterministic embeddings for tests and fallback.
