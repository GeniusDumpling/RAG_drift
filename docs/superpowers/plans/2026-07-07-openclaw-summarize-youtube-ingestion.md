# OpenClaw + Summarize YouTube Ingestion Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a manually triggered pipeline that asks OpenClaw for public YouTube candidates, uses Summarize plus local whisper.cpp and adaptive slides to create Chinese summaries, then persists evidence-backed content in PostgreSQL and vectors in Qdrant.

**Architecture:** Add a focused `app.services.youtube_ingestion` package with pure validation/policy functions, bounded subprocess adapters, a synchronous persistence repository, and one orchestration service. A thin CLI invokes the service; existing content/run models and Qdrant infrastructure remain the source-of-truth and retrieval paths.

**Tech Stack:** Python 3.12, SQLAlchemy 2, PostgreSQL JSONB, Qdrant, OpenClaw CLI, `@steipete/summarize`, yt-dlp, FFmpeg, Tesseract, local whisper.cpp, pytest.

---

## Scope and prerequisite

Implement against the approved design in `docs/superpowers/specs/2026-07-07-openclaw-summarize-youtube-ingestion-design.md`.

The current branch contains duplicate embedding settings and duplicate `build_embedding_service()` implementations. Those defects break vector indexing and must be repaired first. The unrelated README contract and FlyForum default-value failures are not part of this feature; record them separately if they remain after the targeted feature suite passes.

## File map

Create:

- `backend/app/services/youtube_ingestion/__init__.py` — public service exports.
- `backend/app/services/youtube_ingestion/contracts.py` — immutable DTOs and result types.
- `backend/app/services/youtube_ingestion/config.py` — environment parsing and preflight configuration.
- `backend/app/services/youtube_ingestion/policy.py` — YouTube normalization, limits, slide policy, safe paths, redaction.
- `backend/app/services/youtube_ingestion/process.py` — bounded no-shell subprocess runner.
- `backend/app/services/youtube_ingestion/adapters.py` — OpenClaw and Summarize command adapters.
- `backend/app/services/youtube_ingestion/chunks.py` — summary/transcript chunk builder.
- `backend/app/services/youtube_ingestion/repository.py` — synchronous PostgreSQL lineage and persistence operations.
- `backend/app/services/youtube_ingestion/service.py` — end-to-end orchestration and cleanup.
- `scripts/ingest_openclaw_youtube.py` — manual CLI entry point.
- `tests/unit/test_youtube_ingestion_policy.py` — pure policy tests.
- `tests/unit/test_youtube_ingestion_process.py` — subprocess limit tests.
- `tests/unit/test_youtube_ingestion_adapters.py` — external JSON adapter tests.
- `tests/unit/test_youtube_ingestion_chunks.py` — video chunk tests.
- `backend/tests/test_youtube_ingestion_repository.py` — PostgreSQL lineage/idempotency tests.
- `backend/tests/test_youtube_ingestion_service.py` — orchestration and degradation tests.
- `tests/fixtures/openclaw_youtube_search.json` — stable fake OpenClaw output.
- `tests/fixtures/summarize_youtube.json` — stable fake Summarize output.

Modify:

- `backend/app/core/config.py` — remove duplicate embedding declarations and retain deterministic defaults.
- `backend/app/services/embeddings.py` — retain one coherent embedding factory.
- `worker/app/chunk_indexer.py` — include video/chunk timing metadata in Qdrant payload.
- `.env.example` — document new non-secret settings; restore the tracked example if its deletion was accidental and resolve that decision before this task.
- `README.md` — add manual invocation and verification commands.

Do not modify the generic worker state machine or add video asset tables in this release.

### Task 1: Restore the embedding baseline

**Files:**
- Modify: `backend/app/core/config.py`
- Modify: `backend/app/services/embeddings.py`
- Test: `backend/tests/test_embedding_services.py`
- Test: `tests/unit/test_embeddings.py`

- [ ] **Step 1: Run the focused baseline tests and capture the existing failures**

Run:

```bash
pytest -q backend/tests/test_embedding_services.py tests/unit/test_embeddings.py
```

Expected: failures showing `sentence-transformers` or `deterministic` is rejected by the later duplicate factory, and the default provider is not deterministic.

- [ ] **Step 2: Remove duplicate Settings fields**

Keep exactly one embedding block in `Settings`:

```python
embedding_provider: str = Field(default="deterministic", alias="EMBEDDING_PROVIDER")
embedding_model: str = Field(default="deterministic-hash-v1", alias="EMBEDDING_MODEL")
embedding_dimension: int = Field(default=384, alias="EMBEDDING_DIMENSION")
```

Keep the existing VLM fields. Do not define `embedding_provider` or `embedding_model` a second time later in the class.

- [ ] **Step 3: Retain one embedding factory**

Delete the unreachable/mis-indented SiliconFlow fragment and the second `build_embedding_service()`. The only factory must be:

```python
def build_embedding_service(settings: Settings) -> EmbeddingService:
    provider = settings.embedding_provider.strip().casefold().replace("_", "-")
    if provider == "deterministic":
        return DeterministicEmbeddingService(dimension=settings.embedding_dimension)
    if provider == "sentence-transformers":
        model_name = settings.embedding_model.strip() or DEFAULT_SENTENCE_TRANSFORMERS_MODEL
        return SentenceTransformerEmbeddingService(model_name=model_name)
    raise ValueError(
        "Unsupported EMBEDDING_PROVIDER "
        f"{settings.embedding_provider!r}; expected deterministic or sentence-transformers"
    )
```

- [ ] **Step 4: Run focused tests**

Run:

```bash
pytest -q backend/tests/test_embedding_services.py tests/unit/test_embeddings.py
```

Expected: all tests pass when the optional local embedding dependency is installed. If it is intentionally absent, run `pytest -q backend/tests/test_embedding_services.py tests/unit/test_embeddings.py -k 'not sentence_transformer_embedding_shape and not sentence_transformer_is_stable and not build_embedding_service_sentence_transformers'` and require all selected tests to pass.

- [ ] **Step 5: Commit the baseline repair**

```bash
git add backend/app/core/config.py backend/app/services/embeddings.py
git commit -m "fix: restore embedding provider factory"
```

### Task 2: Define ingestion contracts and policy

**Files:**
- Create: `backend/app/services/youtube_ingestion/__init__.py`
- Create: `backend/app/services/youtube_ingestion/contracts.py`
- Create: `backend/app/services/youtube_ingestion/policy.py`
- Create: `tests/unit/test_youtube_ingestion_policy.py`

- [ ] **Step 1: Write failing policy tests**

Create tests covering stable URL normalization, invalid domains, candidate dedupe, the five slide bands, hard limits, safe slide paths, and redaction:

```python
from pathlib import Path

import pytest

from app.services.youtube_ingestion.policy import (
    dedupe_candidates,
    normalize_youtube_url,
    redact_text,
    resolve_slide_path,
    slides_max_for_duration,
)
from app.services.youtube_ingestion.contracts import SearchCandidate


def test_normalizes_watch_short_and_shorts_urls() -> None:
    expected = "https://www.youtube.com/watch?v=fAZZLPwbPyg"
    assert normalize_youtube_url(expected + "&t=30s") == (expected, "fAZZLPwbPyg")
    assert normalize_youtube_url("https://youtu.be/fAZZLPwbPyg?t=30") == (
        expected,
        "fAZZLPwbPyg",
    )
    assert normalize_youtube_url("https://youtube.com/shorts/fAZZLPwbPyg") == (
        expected,
        "fAZZLPwbPyg",
    )


def test_rejects_non_youtube_and_non_http_urls() -> None:
    with pytest.raises(ValueError, match="public YouTube"):
        normalize_youtube_url("https://example.com/watch?v=fAZZLPwbPyg")
    with pytest.raises(ValueError, match="public YouTube"):
        normalize_youtube_url("file:///tmp/video.mp4")


@pytest.mark.parametrize(
    ("seconds", "expected"),
    [(300, 6), (301, 10), (901, 16), (1801, 24), (3601, 32)],
)
def test_adaptive_slide_caps(seconds: int, expected: int) -> None:
    assert slides_max_for_duration(seconds) == expected


def test_dedupes_by_parsed_video_id_and_keeps_rank_order() -> None:
    candidates = [
        SearchCandidate("https://youtu.be/fAZZLPwbPyg", "fAZZLPwbPyg", "A", "", 2, ""),
        SearchCandidate("https://youtube.com/watch?v=fAZZLPwbPyg", "fAZZLPwbPyg", "A", "", 1, ""),
    ]
    assert [item.rank for item in dedupe_candidates(candidates, limit=5)] == [1]


def test_slide_path_cannot_escape_root(tmp_path: Path) -> None:
    root = tmp_path / "slides"
    assert resolve_slide_path(root, "fAZZLPwbPyg", "slide-001.jpg").is_relative_to(root)
    with pytest.raises(ValueError, match="unsafe slide path"):
        resolve_slide_path(root, "fAZZLPwbPyg", "../../secret")


def test_redacts_keys_and_signed_urls() -> None:
    text = "Authorization: Bearer secret https://cdn.test/v.mp4?sig=abc&x=1"
    redacted = redact_text(text)
    assert "secret" not in redacted
    assert "sig=abc" not in redacted
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
pytest -q tests/unit/test_youtube_ingestion_policy.py
```

Expected: import error because the package does not exist.

- [ ] **Step 3: Add immutable contracts**

Define these dataclasses in `contracts.py`:

```python
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

TranscriptSource = Literal["caption", "whisper_cpp"]
SummaryInputMode = Literal["transcript_only", "transcript_ocr", "transcript_vision"]


@dataclass(frozen=True)
class SearchCandidate:
    url: str
    video_id: str
    title: str
    snippet: str
    rank: int
    selection_reason: str


@dataclass(frozen=True)
class SlideRecord:
    timestamp_seconds: float
    relative_path: str
    sha256: str
    ocr_text: str = ""


@dataclass(frozen=True)
class VideoExtraction:
    canonical_url: str
    video_id: str
    title: str
    duration_seconds: int
    transcript: str
    transcript_source: TranscriptSource
    summary: str
    summary_model: str
    summary_input_mode: SummaryInputMode
    slides: tuple[SlideRecord, ...] = ()
    raw_json: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class CommandResult:
    stdout: bytes
    stderr: bytes
    returncode: int
    duration_seconds: float


@dataclass(frozen=True)
class IngestionResult:
    run_id: str
    source_id: str
    status: Literal["success", "partial", "failed"]
    discovered_count: int
    processed_count: int
    deduped_count: int
    succeeded_count: int
    failed_count: int
    chunked_count: int
    embedded_count: int
```

- [ ] **Step 4: Implement pure policy functions**

Implement strict 11-character video ID parsing; allow only `youtube.com`, `www.youtube.com`, `m.youtube.com`, and `youtu.be`; stable-sort by rank; enforce `1 <= limit <= 5`; use `Path.resolve()` plus `is_relative_to()` for file safety. Redact bearer tokens, configured secret values, and sensitive query keys `sig`, `signature`, `token`, `key`, `expire`, and `expires`.

- [ ] **Step 5: Run and commit**

```bash
pytest -q tests/unit/test_youtube_ingestion_policy.py
git add backend/app/services/youtube_ingestion tests/unit/test_youtube_ingestion_policy.py
git commit -m "feat: add YouTube ingestion policy"
```

Expected: all policy tests pass.

### Task 3: Add configuration and bounded subprocess execution

**Files:**
- Create: `backend/app/services/youtube_ingestion/config.py`
- Create: `backend/app/services/youtube_ingestion/process.py`
- Create: `tests/unit/test_youtube_ingestion_process.py`

- [ ] **Step 1: Write failing configuration and process tests**

```python
import os
import sys
from pathlib import Path

import pytest

from app.services.youtube_ingestion.config import YoutubeIngestionConfig
from app.services.youtube_ingestion.process import CommandFailed, run_bounded


def test_config_requires_local_secret_model_and_whisper_file(tmp_path: Path) -> None:
    env = {
        "SILICONFLOW_API_KEY": "secret",
        "SILICONFLOW_SUMMARY_MODEL": "Qwen/test",
        "WHISPER_CPP_BINARY": sys.executable,
        "WHISPER_CPP_MODEL_PATH": str(tmp_path / "missing.bin"),
    }
    with pytest.raises(ValueError, match="WHISPER_CPP_MODEL_PATH"):
        YoutubeIngestionConfig.from_env(env)


def test_run_bounded_rejects_oversized_output() -> None:
    with pytest.raises(CommandFailed, match="output limit"):
        run_bounded(
            [sys.executable, "-c", "print('x' * 5000)"],
            env=os.environ.copy(),
            timeout_seconds=5,
            max_output_bytes=1024,
        )


def test_run_bounded_times_out() -> None:
    with pytest.raises(CommandFailed, match="timed out"):
        run_bounded(
            [sys.executable, "-c", "import time; time.sleep(5)"],
            env=os.environ.copy(),
            timeout_seconds=0.1,
            max_output_bytes=1024,
        )
```

- [ ] **Step 2: Run tests to verify failure**

```bash
pytest -q tests/unit/test_youtube_ingestion_process.py
```

Expected: import error for missing modules.

- [ ] **Step 3: Implement environment parsing**

`YoutubeIngestionConfig.from_env()` must parse and validate:

```python
@dataclass(frozen=True)
class YoutubeIngestionConfig:
    openclaw_path: str
    openclaw_agent: str
    openclaw_timeout_seconds: float
    summarize_path: str
    summarize_timeout_seconds: float
    yt_dlp_path: str
    siliconflow_api_key: str
    siliconflow_base_url: str
    siliconflow_summary_model: str
    whisper_cpp_binary: str
    whisper_cpp_model_path: Path
    slides_dir: Path
    slides_ocr: bool
    command_output_limit_bytes: int
```

Defaults: OpenClaw timeout 120 seconds, Summarize timeout 1200 seconds, `yt-dlp` from PATH, SiliconFlow base URL `https://api.siliconflow.cn/v1`, slides root `data/video_slides`, OCR enabled, combined output limit 16 MiB. Use `shutil.which()` for executable validation and require the whisper model to be a readable file.

- [ ] **Step 4: Implement the no-shell bounded runner**

Use `subprocess.Popen(argv, shell=False, stdout=PIPE, stderr=PIPE, env=env)` and `selectors.DefaultSelector` to read bytes incrementally. Track `time.monotonic()`, kill the process group on timeout or once combined stdout/stderr exceeds the configured limit, drain pipes, and raise `CommandFailed` with redacted/truncated stderr. Never log the child environment or full argv when it contains untrusted text.

- [ ] **Step 5: Run and commit**

```bash
pytest -q tests/unit/test_youtube_ingestion_process.py
git add backend/app/services/youtube_ingestion/config.py \
  backend/app/services/youtube_ingestion/process.py \
  tests/unit/test_youtube_ingestion_process.py
git commit -m "feat: add bounded ingestion subprocess runner"
```

Expected: all process/config tests pass and no child process remains after timeout.

### Task 4: Implement OpenClaw and Summarize adapters

**Files:**
- Create: `backend/app/services/youtube_ingestion/adapters.py`
- Create: `tests/unit/test_youtube_ingestion_adapters.py`
- Create: `tests/fixtures/openclaw_youtube_search.json`
- Create: `tests/fixtures/summarize_youtube.json`

- [ ] **Step 1: Create representative sanitized fixtures**

OpenClaw fixture:

```json
{
  "message": "{\"candidates\":[{\"url\":\"https://youtu.be/fAZZLPwbPyg\",\"video_id\":\"fAZZLPwbPyg\",\"title\":\"DJI test\",\"snippet\":\"firmware\",\"rank\":1,\"selection_reason\":\"matches query\"}]}"
}
```

Summarize fixture:

```json
{
  "summary": "该视频介绍 DJI 飞控固件升级流程。",
  "extracted": {
    "title": "DJI test",
    "content": "[00:00] 开始介绍固件。\n[00:30] 展示升级界面。",
    "durationSeconds": 600,
    "sourceMetrics": {"videoId": "fAZZLPwbPyg"},
    "transcriptSource": "youtube_caption"
  },
  "slides": [
    {"timestamp": 30, "path": "slide-001.jpg", "ocr": "Firmware Update"}
  ],
  "model": "openai/Qwen/test"
}
```

- [ ] **Step 2: Write failing adapter tests**

Test exact argv, `shell=False` runner usage, nested OpenClaw JSON extraction, URL/video ID mismatch rejection, yt-dlp metadata mapping, Summarize field mapping, empty summary rejection, caption/whisper source mapping, vision capability probing, and relative slide copying.

```python
def test_openclaw_adapter_builds_ranked_candidates(fake_runner, fixture_json) -> None:
    adapter = OpenClawSearchAdapter(config(), runner=fake_runner)
    candidates, raw = adapter.search("DJI 固件", limit=5)
    assert candidates[0].video_id == "fAZZLPwbPyg"
    assert fake_runner.argv[:3] == ["openclaw", "agent", "--agent"]
    assert fake_runner.argv[-1] == "--json"
    assert raw["message"]


def test_summarize_adapter_maps_caption_summary_and_slide(fake_runner, tmp_path) -> None:
    extraction = SummarizeYoutubeAdapter(config(tmp_path), runner=fake_runner).extract(
        canonical_url="https://www.youtube.com/watch?v=fAZZLPwbPyg",
        video_id="fAZZLPwbPyg",
        search_title="DJI test",
        slides_max=10,
    )
    assert extraction.transcript_source == "caption"
    assert extraction.summary.startswith("该视频")
    assert extraction.summary_input_mode == "transcript_ocr"
    assert extraction.slides[0].relative_path.startswith("data/video_slides/")


def test_metadata_probe_supplies_duration_before_summarize(fake_runner) -> None:
    metadata = YtDlpMetadataAdapter(config(), runner=fake_runner).probe(
        "https://www.youtube.com/watch?v=fAZZLPwbPyg"
    )
    assert metadata.video_id == "fAZZLPwbPyg"
    assert metadata.duration_seconds == 600


def test_vision_mode_requires_successful_capability_probe(fake_http_client) -> None:
    adapter = SummarizeYoutubeAdapter(config(), http_client=fake_http_client)
    assert adapter.probe_vision_support() is False
```

- [ ] **Step 3: Run tests to verify failure**

```bash
pytest -q tests/unit/test_youtube_ingestion_adapters.py
```

Expected: import error for `adapters`.

- [ ] **Step 4: Implement OpenClaw adapter**

Build argv without a shell:

```python
[
    config.openclaw_path,
    "agent",
    "--agent",
    config.openclaw_agent,
    "--message",
    build_search_prompt(query, limit),
    "--json",
]
```

The prompt must demand one JSON object with `candidates`, public YouTube only, no markdown fence, and at most `limit` entries. Parse the outer CLI JSON, then accept candidate JSON from either top-level `candidates` or a string field named `message`, `content`, or `text`. Validate every candidate through `normalize_youtube_url()` and reject a declared ID mismatch.

- [ ] **Step 5: Implement Summarize adapter**

First implement `YtDlpMetadataAdapter.probe()` with this no-download argv:

```python
[
    config.yt_dlp_path,
    "--dump-single-json",
    "--skip-download",
    "--no-playlist",
    canonical_url,
]
```

Require returned `id` to match the canonical URL and require a positive integer `duration`. Return an immutable `VideoMetadata(video_id, title, duration_seconds)` contract.

Build argv:

```python
[
    config.summarize_path,
    canonical_url,
    "--youtube",
    "auto",
    "--language",
    "zh",
    "--length",
    "medium",
    "--force-summary",
    "--slides",
    "--slides-max",
    str(slides_max),
    "--slides-dir",
    str(staging_slides_dir),
    "--json",
    "--timeout",
    f"{int(config.summarize_timeout_seconds)}s",
]
```

Append `--slides-ocr` only when configured. Child-only environment mapping:

```python
child_env["OPENAI_API_KEY"] = config.siliconflow_api_key
child_env["OPENAI_BASE_URL"] = config.siliconflow_base_url
child_env["SUMMARIZE_MODEL"] = f"openai/{config.siliconflow_summary_model}"
child_env["SUMMARIZE_WHISPER_CPP_BINARY"] = config.whisper_cpp_binary
child_env["SUMMARIZE_WHISPER_CPP_MODEL_PATH"] = str(config.whisper_cpp_model_path)
```

Preserve the complete parsed JSON. Resolve known fields using a small list of explicit paths and fail with `AdapterSchemaError` when title, duration, transcript, video ID, or summary is absent. Do not guess that a description is a transcript. Hash each accepted slide, reject unsafe paths, move it from staging into the final video directory, and set input mode from actual OCR/vision evidence.

Implement `probe_vision_support()` as one cached SiliconFlow Chat Completions request containing a generated 1x1 PNG data URL and a request to answer `OK`. Return true only for a successful response containing non-empty assistant text; return false for unsupported-media 4xx responses. Transport errors fail preflight instead of being interpreted as lack of vision. Never include the key or full response body in errors. The orchestration run records the boolean result once.

- [ ] **Step 6: Run and commit**

```bash
pytest -q tests/unit/test_youtube_ingestion_adapters.py
git add backend/app/services/youtube_ingestion/adapters.py \
  tests/unit/test_youtube_ingestion_adapters.py tests/fixtures
git commit -m "feat: add OpenClaw and Summarize adapters"
```

Expected: all adapter tests pass without invoking real external commands.

### Task 5: Build summary and timestamp-aware transcript chunks

**Files:**
- Create: `backend/app/services/youtube_ingestion/chunks.py`
- Create: `tests/unit/test_youtube_ingestion_chunks.py`
- Modify: `worker/app/chunk_indexer.py`
- Test: `worker/tests/test_worker_runner.py`

- [ ] **Step 1: Write failing chunk tests**

```python
from app.services.youtube_ingestion.chunks import build_video_chunks


def test_builds_summary_then_timestamped_transcript_chunks() -> None:
    chunks = build_video_chunks(
        title="DJI test",
        summary="中文摘要",
        transcript="[00:00] 开始介绍。\n[00:30] 展示升级界面。\n[02:00] 完成。",
        max_chars=40,
    )
    assert chunks[0].chunk_type == "summary"
    assert chunks[0].chunk_metadata_json["chunk_type"] == "summary"
    assert chunks[1].chunk_type == "transcript"
    assert chunks[1].chunk_metadata_json["start_seconds"] == 0
    assert chunks[-1].chunk_metadata_json["end_seconds"] == 120


def test_short_transcript_still_has_summary_and_transcript_chunks() -> None:
    chunks = build_video_chunks(
        title="Short",
        summary="摘要",
        transcript="[00:00] 内容",
    )
    assert [chunk.chunk_type for chunk in chunks] == ["summary", "transcript"]
```

- [ ] **Step 2: Run test to verify failure**

```bash
pytest -q tests/unit/test_youtube_ingestion_chunks.py
```

Expected: import error for `chunks`.

- [ ] **Step 3: Implement the dedicated builder**

Reuse `BuiltChunk` and the existing chunk hash/version conventions. Parse `[HH:MM:SS]` and `[MM:SS]` line prefixes into segments, accumulate adjacent lines up to `max_chars`, and never split a timestamp token from its text. Chunk 0 embeds title plus summary; later chunks embed title plus their transcript window. Store `chunk_type`, `start_seconds`, `end_seconds`, `offset_basis="transcript"`, `chunker_version`, and `embed_text_hash`.

- [ ] **Step 4: Extend Qdrant payload with authoritative video metadata**

In `_build_chunk_payload`, add only sanitized values from PostgreSQL:

```python
metadata = content_item.metadata_json
return {
    # existing payload fields remain
    "youtube_video_id": _metadata_string(metadata, "youtube_video_id"),
    "video_url": content_item.canonical_url if content_item.item_type == "video_description" else None,
    "summary_input_mode": _metadata_string(metadata, "summary_input_mode"),
}
```

Also pass chunk metadata into `_build_chunk_payload` and add `chunk_type`, `start_seconds`, and `end_seconds` from the `ContentChunk`. Update focused worker tests to assert these fields and no signed media URL.

- [ ] **Step 5: Run and commit**

```bash
pytest -q tests/unit/test_youtube_ingestion_chunks.py \
  worker/tests/test_worker_runner.py -k 'payload or chunk'
git add backend/app/services/youtube_ingestion/chunks.py \
  tests/unit/test_youtube_ingestion_chunks.py \
  worker/app/chunk_indexer.py worker/tests/test_worker_runner.py
git commit -m "feat: add timestamped YouTube chunks"
```

Expected: selected tests pass.

### Task 6: Add PostgreSQL lineage and idempotency repository

**Files:**
- Create: `backend/app/services/youtube_ingestion/repository.py`
- Create: `backend/tests/test_youtube_ingestion_repository.py`

- [ ] **Step 1: Write failing PostgreSQL tests**

Use the existing `db_session` fixture and test:

```python
async def test_pending_raw_page_commits_before_external_extraction(sync_repository) -> None:
    context = sync_repository.start_run(query="DJI 固件", candidate_limit=5, video_limit=3)
    raw_page_id = sync_repository.create_pending_video_raw_page(
        context=context,
        canonical_url="https://www.youtube.com/watch?v=fAZZLPwbPyg",
    )
    assert sync_repository.read_raw_page(raw_page_id).parse_status == "pending"


async def test_successful_video_id_is_deduped_across_runs(
    sync_repository, context, raw_page_id, candidate, extraction
) -> None:
    first = sync_repository.persist_success(
        context=context,
        raw_page_id=raw_page_id,
        candidate=candidate,
        extraction=extraction,
        query="DJI 固件",
    )
    assert sync_repository.find_successful_video("fAZZLPwbPyg") == first.content_item_id
```

The test module may use a synchronous engine pointed at `TEST_DATABASE_URL`; do not share one Session across threads or subprocess calls.

- [ ] **Step 2: Run test to verify failure**

```bash
pytest -q backend/tests/test_youtube_ingestion_repository.py
```

Expected: import error for `repository`.

- [ ] **Step 3: Implement Source, Job, Run, and search snapshot methods**

Use one stable source (`site_type="youtube"`, `base_url="https://www.youtube.com"`, allowed domains only) and one manual job (`trigger_mode="manual"`, `parser_profile="openclaw-youtube-search-v1"`). `start_run()` stores a sanitized config snapshot and a URL-encoded YouTube search URL. `persist_search_snapshot()` writes the OpenClaw JSON before any candidate processing and records `search_completed`.

- [ ] **Step 4: Implement pending RawPage and failure transitions**

`create_pending_video_raw_page()` must open and commit a short transaction before returning its UUID. `mark_video_raw_page_success()` writes transcript, sanitized raw JSON, extraction method, confidence, and body hash. `mark_video_raw_page_failed()` commits `parse_status="failed"`, a redacted error, and no secrets.

- [ ] **Step 5: Implement ContentItem/chunk persistence**

Use the approved mapping:

```python
content = ContentItem(
    source_site_id=context.source_id,
    raw_page_id=raw_page_id,
    crawl_run_id=context.run_id,
    item_type="video_description",
    title=extraction.title,
    canonical_url=extraction.canonical_url,
    source_url=extraction.canonical_url,
    language="zh",
    raw_text=extraction.transcript,
    cleaned_text=f"摘要\n\n{extraction.summary}\n\n字幕\n\n{extraction.transcript}",
    summary_text=extraction.summary,
    structured_by="summarize-youtube-v1",
    tags=["youtube", "video", "openclaw-search"],
    metadata_json=build_video_metadata(extraction, candidate, query),
    content_hash=sha256(cleaned_text.encode()).hexdigest(),
    dedup_key=f"{context.source_id}:youtube:{extraction.video_id}:video_description",
)
```

Create `ContentChunk` rows from `build_video_chunks()`, commit them with the ContentItem, and return IDs for vector indexing. A unique-key race reloads the existing successful item and reports dedupe rather than failing the Run.

- [ ] **Step 6: Implement counters and final status**

Repository methods update all generic counters from accumulated facts. Final status rules: no tool error and zero candidates is success; all attempted videos fail is failed; mixed success/failure or vector failure is partial; otherwise success.

- [ ] **Step 7: Run and commit**

```bash
pytest -q backend/tests/test_youtube_ingestion_repository.py
git add backend/app/services/youtube_ingestion/repository.py \
  backend/tests/test_youtube_ingestion_repository.py
git commit -m "feat: persist YouTube ingestion lineage"
```

Expected: all repository tests pass against `intelligence_rag_test`.

### Task 7: Orchestrate search, extraction, persistence, cleanup, and indexing

**Files:**
- Create: `backend/app/services/youtube_ingestion/service.py`
- Create: `backend/tests/test_youtube_ingestion_service.py`
- Modify: `backend/app/services/youtube_ingestion/__init__.py`

- [ ] **Step 1: Write failing orchestration tests with fakes**

Cover all-success, all-deduped, no candidates, malformed search, one-video partial failure, caption path, whisper fallback, slide failure degradation, summary failure, Qdrant failure, and cleanup.

```python
def test_processes_only_top_three_new_candidates(service_factory) -> None:
    service, search, summarize, repo, indexer = service_factory(candidate_count=5)
    result = service.run(query="DJI 固件", candidate_limit=5, video_limit=3)
    assert len(summarize.calls) == 3
    assert result.succeeded_count == 3
    assert result.status == "success"


def test_one_video_failure_yields_partial_and_continues(service_factory) -> None:
    service, search, summarize, repo, indexer = service_factory(
        candidate_count=3,
        summarize_failures={"video-id-2"},
    )
    result = service.run(query="DJI 固件", candidate_limit=5, video_limit=3)
    assert result.succeeded_count == 2
    assert result.failed_count == 1
    assert result.status == "partial"
    assert repo.failed_raw_pages == 1


def test_existing_video_skips_summarize_but_records_dedupe(service_factory) -> None:
    service, search, summarize, repo, indexer = service_factory(existing={"video-id-1"})
    result = service.run(query="DJI 固件", candidate_limit=5, video_limit=3)
    assert "video-id-1" not in summarize.video_ids
    assert result.deduped_count == 1
```

- [ ] **Step 2: Run tests to verify failure**

```bash
pytest -q backend/tests/test_youtube_ingestion_service.py
```

Expected: import error for `service`.

- [ ] **Step 3: Implement orchestration in explicit stages**

`YoutubeIngestionService.run()` must:

1. validate `1..5` candidate and `1..3` video limits;
2. start Run;
3. search and persist raw search output;
4. normalize, rank, and dedupe;
5. check successful DB identities;
6. create and commit pending RawPage before each Summarize call;
7. call `YtDlpMetadataAdapter.probe()`, derive the slides cap from its duration, and reject a metadata/video-ID mismatch before Summarize;
8. extract, persist raw result, ContentItem, and chunks;
9. use the preflight vision probe to select `transcript_vision` only when supported; otherwise use OCR/text modes;
10. index committed chunks with a focused index function;
11. mark vector failures without deleting PostgreSQL facts;
12. remove staging media in `finally`;
13. finalize counters/status and return `IngestionResult`.

Keep each video in its own `try/except/finally`; catch expected adapter/database/vector exceptions, but allow `KeyboardInterrupt` and `SystemExit` to propagate after cleanup.

- [ ] **Step 4: Add a focused index function**

Do not call the generic chunk builder again because video chunks are already persisted. Add a helper in `service.py` (or a narrowly exported helper in `worker/app/chunk_indexer.py`) that loads pending chunks, ensures the collection, upserts each stored `embed_text`, and commits `success`/`failed` vector status. Payload must be built from authoritative PostgreSQL ContentItem/ContentChunk fields.

- [ ] **Step 5: Run and commit**

```bash
pytest -q backend/tests/test_youtube_ingestion_service.py \
  backend/tests/test_youtube_ingestion_repository.py \
  tests/unit/test_youtube_ingestion_policy.py \
  tests/unit/test_youtube_ingestion_adapters.py \
  tests/unit/test_youtube_ingestion_chunks.py
git add backend/app/services/youtube_ingestion backend/tests/test_youtube_ingestion_service.py
git commit -m "feat: orchestrate YouTube search ingestion"
```

Expected: all selected tests pass.

### Task 8: Add the manual CLI and JSON contract

**Files:**
- Create: `scripts/ingest_openclaw_youtube.py`
- Create: `tests/unit/test_ingest_openclaw_youtube_cli.py`

- [ ] **Step 1: Write failing CLI tests**

```python
def test_parse_args_defaults() -> None:
    module = load_script()
    args = module.parse_args(["--query", "DJI 固件"])
    assert args.candidate_limit == 5
    assert args.video_limit == 3
    assert args.json is False


def test_parse_args_rejects_limits_above_hard_caps() -> None:
    module = load_script()
    with pytest.raises(SystemExit):
        module.parse_args(["--query", "DJI", "--candidate-limit", "6"])


def test_main_prints_machine_result(monkeypatch, capsys) -> None:
    monkeypatch.setattr(module, "build_service", lambda: FakeService())
    assert module.main(["--query", "DJI", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "success"
    assert payload["run_id"]
```

- [ ] **Step 2: Run test to verify failure**

```bash
pytest -q tests/unit/test_ingest_openclaw_youtube_cli.py
```

Expected: script does not exist.

- [ ] **Step 3: Implement thin CLI**

Expose `parse_args(argv)`, `build_service()`, and `main(argv)`. Use `argparse` choices/types to enforce caps, call `YoutubeIngestionConfig.from_env(os.environ)`, construct adapters/repository/service, and return exit code 0 for success, 2 for partial, 1 for failed/preflight error. `--json` writes exactly one JSON object to stdout; logs go to stderr. Never print environment variables.

- [ ] **Step 4: Run and commit**

```bash
pytest -q tests/unit/test_ingest_openclaw_youtube_cli.py
git add scripts/ingest_openclaw_youtube.py tests/unit/test_ingest_openclaw_youtube_cli.py
git commit -m "feat: add manual YouTube ingestion CLI"
```

Expected: all CLI tests pass.

### Task 9: Document configuration and operational verification

**Files:**
- Modify: `.env.example`
- Modify: `README.md`
- Create: `tests/unit/test_openclaw_youtube_docs.py`

- [ ] **Step 1: Resolve `.env.example` ownership before editing**

Run:

```bash
git status --short .env.example
git diff -- .env.example
git diff --cached -- .env.example
```

Expected: determine whether its current deletion is intentional user work. If intentional, do not restore it; put the non-secret configuration block in README only. If accidental and approved for restoration, restore the tracked file before editing it. Never overwrite an intentional deletion.

- [ ] **Step 2: Write a docs contract test**

Assert README contains the exact manual command, hard limits, public-only restriction, no-cookie restriction, PostgreSQL verification, and a search request using space-separated Chinese keywords. If `.env.example` exists, assert it contains variable names but no non-empty secret.

- [ ] **Step 3: Add operator documentation**

Document:

```bash
python scripts/ingest_openclaw_youtube.py \
  --query "DJI 飞控 固件" \
  --candidate-limit 5 \
  --video-limit 3 \
  --json
```

Include installation checks for Node 24+, Summarize, OpenClaw, yt-dlp, FFmpeg, Tesseract, whisper-cli/model, Docker PostgreSQL/Qdrant, and SiliconFlow variables. State that browser cookies, authenticated videos, bypasses, and signed media URL persistence are forbidden.

- [ ] **Step 4: Add verification commands**

Use the returned IDs:

```bash
curl -fsS "http://127.0.0.1:8000/runs/$RUN_ID" | python -m json.tool
curl -fsS "http://127.0.0.1:8000/contents?source_site_id=$SOURCE_ID&limit=10" | python -m json.tool
curl -fsS -X POST http://127.0.0.1:8000/search \
  -H 'Content-Type: application/json' \
  -d "{\"query\":\"DJI 飞控 固件\",\"mode\":\"search\",\"filters\":{\"source_site_id\":\"$SOURCE_ID\",\"item_type\":\"video_description\"},\"top_k\":3}" \
  | python -m json.tool
```

- [ ] **Step 5: Run and commit**

```bash
pytest -q tests/unit/test_openclaw_youtube_docs.py
git add README.md tests/unit/test_openclaw_youtube_docs.py
test ! -f .env.example || git add .env.example
git commit -m "docs: add YouTube ingestion operations guide"
```

Expected: docs contract passes and no secret value is staged.

### Task 10: Final verification and one bounded live smoke

**Files:**
- Test only; no production changes unless a failing test identifies a defect.

- [ ] **Step 1: Run formatting, lint, and type checks for changed code**

```bash
ruff check backend/app/services/youtube_ingestion scripts/ingest_openclaw_youtube.py \
  tests/unit/test_youtube_ingestion_*.py backend/tests/test_youtube_ingestion_*.py
mypy backend/app/services/youtube_ingestion
```

Expected: both commands exit 0.

- [ ] **Step 2: Run the complete feature test set**

```bash
pytest -q \
  tests/unit/test_youtube_ingestion_policy.py \
  tests/unit/test_youtube_ingestion_process.py \
  tests/unit/test_youtube_ingestion_adapters.py \
  tests/unit/test_youtube_ingestion_chunks.py \
  tests/unit/test_ingest_openclaw_youtube_cli.py \
  tests/unit/test_openclaw_youtube_docs.py \
  backend/tests/test_youtube_ingestion_repository.py \
  backend/tests/test_youtube_ingestion_service.py \
  backend/tests/test_embedding_services.py
```

Expected: all selected tests pass.

- [ ] **Step 3: Run the broader regression suite**

```bash
pytest -q
```

Expected: no failures introduced by this feature. If pre-existing README/FlyForum failures remain, compare against the recorded baseline, report them explicitly, and do not claim the full suite passes.

- [ ] **Step 4: Run a one-video public live smoke**

With local secrets configured and infrastructure running:

```bash
docker compose up -d postgres qdrant
alembic upgrade head
python scripts/ingest_openclaw_youtube.py \
  --query "DJI 飞控 固件" \
  --candidate-limit 1 \
  --video-limit 1 \
  --json | tee /tmp/openclaw-youtube-smoke.json
```

Expected: one JSON result, no credential output, status `success` or a clearly attributed public-source/tool failure. Do not add cookies or bypasses to force success.

- [ ] **Step 5: Verify database and retrieval evidence**

Extract IDs with `jq`, run the documented `/runs`, `/contents`, and `/search` requests, and verify:

- stable `youtube.com/watch?v=` URL;
- non-empty transcript and Chinese `summary_text`;
- `summary_input_mode` matches actual inputs;
- adaptive `slides_max` and safe relative slide paths;
- at least one summary chunk and, when transcript content exists, one transcript chunk;
- Qdrant success or explicit failed chunk status;
- no API key, Cookie, browser profile, signed CDN URL, or local absolute path in JSON/logs.

- [ ] **Step 6: Commit only if verification required a code correction**

```bash
git status --short
git diff --check
git add backend/app/services/youtube_ingestion \
  scripts/ingest_openclaw_youtube.py \
  tests/unit/test_youtube_ingestion_*.py \
  tests/unit/test_ingest_openclaw_youtube_cli.py \
  backend/tests/test_youtube_ingestion_*.py
git commit -m "fix: harden YouTube ingestion verification"
```

Expected: no unrelated user changes are staged or committed.
