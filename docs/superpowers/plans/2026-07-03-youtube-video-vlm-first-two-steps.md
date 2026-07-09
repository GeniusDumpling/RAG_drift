# YouTube Video VLM First Two Steps Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Reliably resolve one public YouTube page into a SiliconFlow-readable media URL and obtain a non-empty Chinese Qwen3-Omni description without touching PostgreSQL or Qdrant.

**Architecture:** Keep the existing CLI entry point but isolate metadata resolution and VLM analysis behind typed results. Use `yt-dlp` metadata extraction first, immediately submit its selected HTTPS media URL to SiliconFlow, and report only sanitized diagnostics. This first slice deliberately stops before persistence and does not expose the local file server.

**Tech Stack:** Python 3.11, yt-dlp, httpx, pytest, SiliconFlow OpenAI-compatible chat completions API

---

## File Structure

- Modify `skills/video-crawler/scripts/video_fetch.py`: add typed YouTube metadata resolution, URL validation, a no-persistence analyze command, and sanitized result output.
- Create `tests/unit/test_video_fetch.py`: cover extraction options, metadata normalization, VLM payload, redaction, and the no-persistence analysis flow with fakes.
- Modify `skills/video-crawler/SKILL.md`: document the minimal analysis-only command and its required environment variables.

### Task 1: Resolve YouTube metadata into a VLM input

**Files:**
- Modify: `skills/video-crawler/scripts/video_fetch.py`
- Create: `tests/unit/test_video_fetch.py`

- [ ] **Step 1: Write failing resolver tests**

Add tests that load the hyphenated script with `importlib.util`, replace `yt_dlp.YoutubeDL` with a fake context manager, and assert that `resolve_video_input()` returns a typed result containing the stable page URL, video ID, title, selected HTTPS media URL, duration, and extractor. Assert that unsupported schemes and missing media URLs raise `VideoResolutionError`. Assert extractor options use `best[height<=480]`, `skip_download=True`, and do not print signed URLs.

- [ ] **Step 2: Run resolver tests and verify failure**

Run:

```powershell
.venv\Scripts\python.exe -m pytest tests/unit/test_video_fetch.py -q
```

Expected: FAIL because `VideoInput`, `VideoResolutionError`, and `resolve_video_input` do not exist.

- [ ] **Step 3: Implement the typed resolver**

Add immutable `VideoInput` with `page_url`, `video_id`, `title`, `media_url`, `duration_seconds`, and `extractor`. Add `VideoResolutionError`. Validate input and output schemes as HTTP(S), call `yt_dlp.extract_info(download=False)` with a 480p progressive-first format selector, reject playlist results, normalize the stable YouTube watch URL from the extracted ID, and never log the complete media URL.

- [ ] **Step 4: Run resolver tests**

Run the Task 1 test command. Expected: PASS.

- [ ] **Step 5: Commit resolver changes**

```powershell
git add -- skills/video-crawler/scripts/video_fetch.py tests/unit/test_video_fetch.py
git commit -m "feat: resolve YouTube videos for VLM analysis"
```

### Task 2: Call SiliconFlow without persistence

**Files:**
- Modify: `skills/video-crawler/scripts/video_fetch.py`
- Modify: `tests/unit/test_video_fetch.py`
- Modify: `skills/video-crawler/SKILL.md`

- [ ] **Step 1: Write failing analysis-only tests**

Add a fake `httpx.Client` that captures `/chat/completions`. Assert `analyze_video_only()` passes the resolved `media_url` as a `video_url` content part, returns the stable page URL/title/description/model, rejects empty descriptions, and does not create a database engine. Add CLI parsing coverage for `--analyze-only --video-url ... --json`.

- [ ] **Step 2: Run analysis tests and verify failure**

Run:

```powershell
.venv\Scripts\python.exe -m pytest tests/unit/test_video_fetch.py -q
```

Expected: FAIL because the analysis-only function and CLI mode are absent.

- [ ] **Step 3: Implement analysis-only mode**

Add `analyze_video_only()` that resolves metadata, calls the existing VLM request function, and returns only stable/safe fields. Extend CLI arguments with `--analyze-only` and `--json`; in this mode validate `VLM_API_KEY`, avoid all database and Qdrant setup, print a JSON result containing `status`, `video_id`, `video_page_url`, `title`, `duration_seconds`, `model`, and `description`, and return a nonzero exit code with a sanitized error on failure.

- [ ] **Step 4: Document and run focused tests**

Document:

```powershell
.venv\Scripts\python.exe skills/video-crawler/scripts/video_fetch.py `
  --analyze-only `
  --video-url "https://www.youtube.com/watch?v=fAZZLPwbPyg" `
  --source youtube `
  --json
```

Run the Task 2 test command. Expected: PASS.

- [ ] **Step 5: Perform the live acceptance check**

First confirm only whether `VLM_API_KEY` is present, without printing its value. Then run the documented command. Expected: yt-dlp resolves video metadata and SiliconFlow returns a non-empty Chinese `description`. If the key is absent or the provider cannot fetch YouTube's signed CDN URL, report that exact sanitized blocker; do not fall through to database ingestion.

- [ ] **Step 6: Run regression tests**

```powershell
.venv\Scripts\python.exe -m pytest tests/unit/test_video_fetch.py tests/unit/test_flyforum_video_demo.py backend/tests/test_search_pipeline.py -q
```

Expected: PASS.

- [ ] **Step 7: Commit analysis-only changes**

```powershell
git add -- skills/video-crawler/scripts/video_fetch.py tests/unit/test_video_fetch.py skills/video-crawler/SKILL.md
git commit -m "feat: add SiliconFlow video analysis-only flow"
```

