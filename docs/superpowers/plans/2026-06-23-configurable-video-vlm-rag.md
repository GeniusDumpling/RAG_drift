# Configurable Video VLM RAG Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Extend the existing Intelligence RAG application so configurable public websites can be crawled for direct MP4/WebM/M3U8 URLs, analyzed by SiliconFlow Qwen3-Omni under per-job description rules, stored with full PostgreSQL/Qdrant lineage, and returned through both search and answer UIs.

**Architecture:** Keep the current FastAPI/worker/React modular monolith. Add a typed video asset model, bounded configurable crawler, safe media validator, pluggable page/VLM/embedding providers, and a video orchestration path that persists PostgreSQL state before indexing Qdrant. Reuse `ContentItem`, `ContentChunk`, run events, retrieval merging, and frontend navigation.

**Tech Stack:** Python 3.11, FastAPI, Pydantic 2, SQLAlchemy 2 async/sync, Alembic, PostgreSQL 16, Qdrant, httpx, BeautifulSoup, croniter, FFmpeg/FFprobe, React 18, TypeScript, Vite, Vitest, pytest.

---

## Scope and execution notes

- Implement against the approved design in `docs/superpowers/specs/2026-06-23-configurable-video-vlm-rag-design.md`.
- Use public unauthenticated pages only. Never bypass login, CAPTCHA, robots rules, rate limits, or protected players.
- The repository already has Git metadata. Before each task, inspect `git status --short`, preserve unrelated and pre-existing changes, and stage only the files changed by that task before running its listed commit command.
- The current deterministic embedding remains available only for tests and explicit development mode. Production video retrieval uses SiliconFlow's OpenAI-compatible embeddings endpoint with `EMBEDDING_MODEL=BAAI/bge-m3` unless deployment overrides it.
- Use the exact `python -m pytest` and `npm test` commands listed under each task so execution works in PowerShell and POSIX shells when the correct environment is active.

## File map

### Backend and database

- Create `backend/app/models/video.py`: stable `VideoAsset`, append-only `VideoAssetDiscovery`, and statuses.
- Create `backend/alembic/versions/0002_video_assets.py`: asset/discovery tables, six video run counters, indexes, and constraints.
- Create `backend/app/schemas/video_config.py`: crawl and description-policy schemas shared by API and worker.
- Create `backend/app/schemas/video.py`: asset list/detail and media filter schemas.
- Create `backend/app/repositories/videos.py`: asset/discovery CRUD, locking, list/detail, idempotent content update, and transactional chunk reconciliation.
- Create `backend/app/api/videos.py`: `/videos` and `/videos/{id}`.
- Modify `backend/app/models/__init__.py`, `backend/app/api/router.py`, `backend/app/schemas/sources.py`, and `backend/app/models/control.py` to expose the new model/config/counters.

### Crawling, VLM, scheduling, and indexing

- Create `worker/app/url_safety.py`: URL normalization and page/media-domain policy.
- Create `worker/app/safe_http.py`: DNS/IP validation bound to the actual connection, redirect validation, and bounded streaming.
- Create `worker/app/robots.py`: bounded robots.txt loading, caching, and fail-closed page authorization.
- Create `worker/app/page_fetchers.py`: native HTTP and Firecrawl fetchers built on the safe transport.
- Create `worker/app/site_profiles.py`: generic and Discuz discovery rules.
- Create `worker/app/video_discovery.py`: HTML media extraction and candidate deduplication.
- Create `worker/app/hls_downloader.py`: controlled nested-playlist/key/segment download with aggregate limits.
- Create `worker/app/media_probe.py`: bounded download metadata checks, local-only FFprobe/FFmpeg remux, and cleanup.
- Create `worker/app/video_vlm.py`: SiliconFlow request, prompt building, validation, and retry.
- Create `worker/app/video_pipeline.py`: per-run video state machine and transaction boundaries.
- Create `worker/app/scheduler.py` and `scripts/run_scheduler.py`: due-Cron job creation.
- Modify `worker/app/runner.py`, `worker/app/adapters.py`, `worker/app/chunk_indexer.py`, `backend/app/core/config.py`, `backend/app/services/embeddings.py`, and `backend/app/services/retrieval.py`.

### Frontend

- Create `frontend/src/pages/VideosPage.tsx`: asset list/detail view.
- Create `frontend/src/components/VideoEvidenceCard.tsx`: grouped video evidence.
- Create `frontend/src/components/VideoJobConfig.tsx`: typed site/job/policy editor.
- Modify `frontend/src/App.tsx`, `frontend/src/api/types.ts`, `frontend/src/api/client.ts`, `frontend/src/pages/SourcesPage.tsx`, `frontend/src/pages/RunDetailPage.tsx`, `frontend/src/pages/SearchPage.tsx`, and `frontend/src/styles.css`.

---

### Task 1: Runtime configuration and dependencies

**Files:**
- Modify: `pyproject.toml`
- Modify: `.env.example`
- Modify: `backend/app/core/config.py`
- Test: `backend/tests/test_scaffold.py`

- [ ] **Step 1: Add a failing settings test**

```python
def test_video_runtime_settings_have_safe_defaults(monkeypatch):
    from app.core.config import Settings

    monkeypatch.delenv("VLM_API_KEY", raising=False)
    settings = Settings(_env_file=None)
    assert settings.vlm_base_url == "https://api.siliconflow.cn/v1"
    assert settings.vlm_model == "Qwen/Qwen3-Omni-30B-A3B-Instruct"
    assert settings.embedding_provider == "deterministic"
    assert settings.embedding_model == "BAAI/bge-m3"
    assert settings.video_max_bytes == 104_857_600
    assert settings.video_max_duration_seconds == 900
    assert settings.video_max_hls_segments == 512
```

- [ ] **Step 2: Run the test and verify the missing fields fail**

Run: `python -m pytest backend/tests/test_scaffold.py::test_video_runtime_settings_have_safe_defaults -q`

Expected: FAIL with an `AttributeError` for `vlm_base_url`.

- [ ] **Step 3: Add dependencies and typed settings**

Add these locked dependencies to `pyproject.toml`:

```toml
"beautifulsoup4==4.12.3",
"croniter==6.0.0",
```

Add these fields to `Settings`:

```python
vlm_api_key: str | None = Field(default=None, alias="VLM_API_KEY")
vlm_base_url: str = Field(default="https://api.siliconflow.cn/v1", alias="VLM_BASE_URL")
vlm_model: str = Field(
    default="Qwen/Qwen3-Omni-30B-A3B-Instruct", alias="VLM_MODEL"
)
firecrawl_api_key: str | None = Field(default=None, alias="FIRECRAWL_API_KEY")
embedding_provider: str = Field(default="deterministic", alias="EMBEDDING_PROVIDER")
embedding_model: str = Field(default="BAAI/bge-m3", alias="EMBEDDING_MODEL")
video_max_bytes: int = Field(default=104_857_600, alias="VIDEO_MAX_BYTES", gt=0)
video_max_duration_seconds: int = Field(
    default=900, alias="VIDEO_MAX_DURATION_SECONDS", gt=0
)
video_max_hls_segments: int = Field(default=512, alias="VIDEO_MAX_HLS_SEGMENTS", gt=0, le=4096)
video_vlm_timeout_seconds: int = Field(
    default=180, alias="VIDEO_VLM_TIMEOUT_SECONDS", gt=0
)
video_temp_dir: str | None = Field(default=None, alias="VIDEO_TEMP_DIR")
```

Mirror the variable names and non-secret defaults in `.env.example`; leave API keys blank.

- [ ] **Step 4: Install and verify**

Run: `python -m pip install -e ".[dev]" && python -m pytest backend/tests/test_scaffold.py -q`

Expected: PASS.

- [ ] **Step 5: Commit when Git metadata is available**

```bash
git add pyproject.toml .env.example backend/app/core/config.py backend/tests/test_scaffold.py
git commit -m "chore: add video pipeline runtime settings"
```

### Task 2: Stable video assets, append-only discovery lineage, migration, and run counters

**Files:**
- Create: `backend/app/models/video.py`
- Create: `backend/alembic/versions/0002_video_assets.py`
- Modify: `backend/app/models/__init__.py`
- Modify: `backend/app/models/control.py`
- Modify: `backend/app/schemas/sources.py`
- Test: `backend/tests/test_schema_metadata.py`
- Test: `backend/tests/test_control_api.py`

- [ ] **Step 1: Write metadata tests for the table and counters**

```python
def test_video_assets_and_discoveries_separate_identity_from_run_lineage():
    from app.models.video import VideoAsset, VideoAssetDiscovery

    assert VideoAsset.__tablename__ == "video_assets"
    columns = VideoAsset.__table__.columns
    assert columns["normalized_video_url"].nullable is False
    unique_sets = {
        tuple(constraint.columns.keys())
        for constraint in VideoAsset.__table__.constraints
        if constraint.__class__.__name__ == "UniqueConstraint"
    }
    assert ("source_site_id", "normalized_video_url") in unique_sets
    assert "crawl_run_id" not in columns
    assert "raw_page_id" not in columns

    discovery_columns = VideoAssetDiscovery.__table__.columns
    assert {"video_asset_id", "crawl_run_id", "raw_page_id", "source_page_url"} <= set(
        discovery_columns.keys()
    )
    discovery_unique_sets = {
        tuple(constraint.columns.keys())
        for constraint in VideoAssetDiscovery.__table__.constraints
        if constraint.__class__.__name__ == "UniqueConstraint"
    }
    assert ("crawl_run_id", "raw_page_id", "video_asset_id") in discovery_unique_sets


def test_run_schema_exposes_video_counters():
    from app.schemas.sources import CrawlRunRead

    assert {
        "video_discovered_count",
        "video_analyzed_count",
        "video_skipped_count",
        "video_failed_count",
        "video_chunked_count",
        "video_indexed_count",
    } <= CrawlRunRead.model_fields.keys()
```

- [ ] **Step 2: Run tests and verify import/field failures**

Run: `python -m pytest backend/tests/test_schema_metadata.py backend/tests/test_control_api.py -q`

Expected: FAIL because `app.models.video`, the discovery model, and the counters do not exist.

- [ ] **Step 3: Implement the ORM model**

Create `VideoAsset` with UUID/timestamp mixins, typed foreign keys, JSONB snapshots, indexes on source/content/status, and this constraint set:

```python
VIDEO_STATUSES = ("discovered", "analyzing", "success", "failed", "skipped")

__table_args__ = (
    UniqueConstraint("source_site_id", "normalized_video_url"),
    CheckConstraint(
        "status IN ('discovered','analyzing','success','failed','skipped')",
        name="video_asset_status_valid",
    ),
    CheckConstraint("size_bytes IS NULL OR size_bytes >= 0", name="video_size_non_negative"),
    CheckConstraint(
        "duration_seconds IS NULL OR duration_seconds >= 0",
        name="video_duration_non_negative",
    ),
)
```

Use `ondelete="SET NULL"` for `content_item_id`. Map nullable `analysis_discovery_id` with a named `ForeignKey("video_asset_discoveries.id", ondelete="SET NULL", use_alter=True)` so SQLAlchemy metadata understands the relationship without emitting the circular constraint before both tables exist. The explicit migration creates the column with the asset table and adds the named foreign key after the discovery table exists.

Create `VideoAssetDiscovery` with foreign keys to asset, source, run, and raw page; source page URL; observed video URL; source element; declared content type; metadata JSON; and discovery timestamp. Add `UniqueConstraint("crawl_run_id", "raw_page_id", "video_asset_id")`. Export both models from `backend/app/models/__init__.py` so Alembic metadata sees them.

- [ ] **Step 4: Add non-negative run counters and response fields**

Add six integer columns to `CrawlRun`, each defaulting to zero and guarded by check constraints: `video_discovered_count`, `video_analyzed_count`, `video_skipped_count`, `video_failed_count`, `video_chunked_count`, and `video_indexed_count`. Add the same names to `CrawlRunRead`. Tests must establish that Qdrant failures affect chunk state, generic `error_count`, and run outcome but do not increment `video_failed_count` for an asset whose description was persisted successfully.

- [ ] **Step 5: Write migration `0002_video_assets.py`**

Use `down_revision = "0001_intel_rag_schema"`. Create `video_assets` with nullable `analysis_discovery_id` but without its foreign-key constraint, create `video_asset_discoveries`, then add the named foreign key so the circular relationship and migration ordering are explicit. Add all six run-counter columns with server default `0` and their check constraints. `downgrade()` drops the circular foreign key, discovery table, asset table, constraints, and counters in reverse order.

- [ ] **Step 6: Run metadata and migration tests**

Run: `python -m pytest backend/tests/test_schema_metadata.py backend/tests/test_control_api.py -q`

Expected: PASS.

Run against the local test database: `python -m alembic upgrade head`

Expected: migration reaches `0002_video_assets` without error.

- [ ] **Step 7: Commit**

```bash
git add backend/app/models backend/app/schemas/sources.py backend/alembic/versions/0002_video_assets.py backend/tests/test_schema_metadata.py backend/tests/test_control_api.py
git commit -m "feat: add video asset persistence model"
```

### Task 3: Strict crawl and description-policy configuration

**Files:**
- Create: `backend/app/schemas/video_config.py`
- Modify: `backend/app/schemas/sources.py`
- Modify: `backend/app/api/sources.py`
- Test: `backend/tests/test_video_config.py`
- Test: `backend/tests/test_control_api.py`

- [ ] **Step 1: Write failing configuration tests**

```python
import pytest
from pydantic import ValidationError

from app.schemas.video_config import VideoCrawlConfig, VideoDescriptionPolicy


def test_video_crawl_config_accepts_bounded_flyforum_profile():
    config = VideoCrawlConfig.model_validate({
        "start_urls": ["https://www.flyforum.cn/forum.php"],
        "allowed_domains": ["www.flyforum.cn"],
        "allowed_media_domains": ["www.flyforum.cn"],
        "follow_patterns": ["/forum-*.html", "/thread-*.html"],
        "exclude_patterns": ["/member.php*", "*mod=post*"],
        "media_selectors": ["video source[src]", "a[href$='.mp4']"],
        "max_depth": 2,
        "max_pages": 50,
        "max_videos": 10,
        "request_interval_seconds": 1.5,
        "respect_robots_txt": True,
    })
    assert config.max_videos == 10
    assert config.media_selectors == ["video source[src]", "a[href$='.mp4']"]


def test_description_policy_rejects_inverted_character_bounds():
    with pytest.raises(ValidationError):
        VideoDescriptionPolicy(min_chars=3000, max_chars=500)
```

- [ ] **Step 2: Verify failure**

Run: `python -m pytest backend/tests/test_video_config.py -q`

Expected: FAIL because the module is missing.

- [ ] **Step 3: Implement typed models**

Implement `VideoCrawlConfig` with `extra="forbid"`, non-empty HTTP(S) start URLs, domain normalization, `max_depth` 0–5, `max_pages` 1–500, `max_videos` 1–100, and request interval 0.5–60 seconds. Add `media_selectors: list[str]` with at most 20 selectors, 200 characters per selector, no empty entries, and validation through `soupsieve.compile`; reject invalid or unsupported selectors at the API boundary rather than during a run. Implement `VideoDescriptionPolicy` with language, focus areas, required sections, excluded topics, 100–10,000 character bounds, and a 2,000-character custom-instruction limit. Add an after-validator requiring `min_chars <= max_chars`.

Expose a helper:

```python
def parse_video_job_config(
    seed_config_json: dict[str, object], agent_policy_json: dict[str, object]
) -> tuple[VideoCrawlConfig, VideoDescriptionPolicy]:
    crawl = VideoCrawlConfig.model_validate(seed_config_json)
    policy = VideoDescriptionPolicy.model_validate(
        agent_policy_json.get("description_policy", {})
    )
    return crawl, policy
```

Add `validate_job_allowlists(source, crawl)` that requires every job `allowed_domains` entry to be within `SourceSite.allowed_domains` and every job `allowed_media_domains` entry to be within `SourceSite.config_json["allowed_media_domains"]`. Domain comparison uses the same normalized exact-or-subdomain semantics as runtime URL checks. Missing source media bounds reject video-job creation.

- [ ] **Step 4: Validate video jobs at API boundaries**

In create/update job routes, when `parser_profile == "configurable_video"`, call `parse_video_job_config` and `validate_job_allowlists` against the owning source; convert validation failures into HTTP 422 with sanitized field errors. Keep existing non-video jobs unchanged. Add API tests proving a job cannot expand page or media domains beyond its source.

- [ ] **Step 5: Run tests**

Run: `python -m pytest backend/tests/test_video_config.py backend/tests/test_control_api.py -q`

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add backend/app/schemas/video_config.py backend/tests/test_video_config.py backend/app/schemas/sources.py backend/app/api/sources.py backend/tests/test_control_api.py
git commit -m "feat: validate configurable video crawl jobs"
```

### Task 4: URL normalization, robots, SSRF, and redirect safety

**Files:**
- Create: `worker/app/url_safety.py`
- Create: `worker/app/safe_http.py`
- Create: `worker/app/robots.py`
- Test: `worker/tests/test_url_safety.py`
- Test: `worker/tests/test_safe_http.py`
- Test: `worker/tests/test_robots.py`

- [ ] **Step 1: Write failing security tests**

```python
import ipaddress
from dataclasses import dataclass
from unittest.mock import Mock
import pytest

from worker.app.robots import RobotsPolicy
from worker.app.safe_http import SafeHttpClient
from worker.app.url_safety import UnsafeUrlError, normalize_url, validate_resolved_ips


@dataclass
class FakeResponse:
    status_code: int
    peer_ip: str
    headers: dict[str, str]
    body: bytes


class FakeResolver:
    def __init__(self, values):
        self.values = values
        self.calls = []

    def resolve(self, hostname, port):
        self.calls.append(hostname)
        return [ipaddress.ip_address(value) for value in self.values[hostname]]


class RecordingConnector:
    def __init__(self, peer_ip):
        self.peer_ip = peer_ip
        self.calls = []

    def request(self, *, connect_ip, server_hostname, host_header, **kwargs):
        self.calls.append({
            "connect_ip": connect_ip,
            "server_hostname": server_hostname,
            "host_header": host_header,
        })
        return FakeResponse(status_code=200, peer_ip=self.peer_ip, headers={}, body=b"ok")


def test_normalize_url_removes_fragment_and_default_port():
    assert normalize_url("HTTPS://Example.COM:443/a.mp4#t=1") == "https://example.com/a.mp4"


@pytest.mark.parametrize("value", ["127.0.0.1", "10.0.0.2", "169.254.1.1", "::1"])
def test_private_or_local_addresses_are_rejected(value):
    with pytest.raises(UnsafeUrlError):
        validate_resolved_ips([ipaddress.ip_address(value)])


def test_safe_client_connects_to_validated_ip_without_second_dns_lookup():
    resolver = FakeResolver({"media.example": ["93.184.216.34"]})
    connector = RecordingConnector(peer_ip="93.184.216.34")
    client = SafeHttpClient(resolver=resolver, connector=connector)
    client.get("https://media.example/demo.mp4", allowed_domains=["media.example"])
    assert connector.calls == [{
        "connect_ip": "93.184.216.34",
        "server_hostname": "media.example",
        "host_header": "media.example",
    }]
    assert resolver.calls == ["media.example"]


def test_robots_network_failure_is_fail_closed():
    client = Mock()
    client.get.side_effect = TimeoutError("network unavailable")
    policy = RobotsPolicy(client=client, user_agent="intelligence-rag-video/1.0")
    assert policy.can_fetch("https://www.flyforum.cn/thread-1-1-1.html") is False
```

- [ ] **Step 2: Verify failure**

Run: `python -m pytest worker/tests/test_url_safety.py worker/tests/test_safe_http.py worker/tests/test_robots.py -q`

Expected: FAIL because the URL, safe HTTP, and robots modules are missing.

- [ ] **Step 3: Implement the safety module**

Provide these complete core helpers; the implementation may add typed internal helpers for DNS socket calls:

```python
import ipaddress
import socket
from urllib.parse import urljoin, urlsplit, urlunsplit


class UnsafeUrlError(ValueError):
    pass


def normalize_url(value: str, *, base_url: str | None = None) -> str:
    resolved = urljoin(base_url, value) if base_url else value
    parsed = urlsplit(resolved.strip())
    scheme = parsed.scheme.lower()
    host = (parsed.hostname or "").lower().rstrip(".")
    if scheme not in {"http", "https"} or not host:
        raise UnsafeUrlError("Only absolute HTTP(S) URLs are allowed")
    if parsed.username is not None or parsed.password is not None:
        raise UnsafeUrlError("Embedded URL credentials are not allowed")
    port = parsed.port
    default_port = (scheme == "http" and port == 80) or (scheme == "https" and port == 443)
    netloc = host if port is None or default_port else f"{host}:{port}"
    return urlunsplit((scheme, netloc, parsed.path or "/", parsed.query, ""))


def host_is_allowed(host: str, allowed_domains: list[str]) -> bool:
    normalized = host.lower().rstrip(".")
    return any(
        normalized == domain.lower().rstrip(".")
        or normalized.endswith(f".{domain.lower().rstrip('.')}")
        for domain in allowed_domains
    )


def resolve_public_ips(
    host: str, port: int
) -> list[ipaddress.IPv4Address | ipaddress.IPv6Address]:
    values = {
        ipaddress.ip_address(item[4][0])
        for item in socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    }
    return sorted(values, key=str)


def validate_resolved_ips(
    addresses: list[ipaddress.IPv4Address | ipaddress.IPv6Address],
) -> None:
    if not addresses or any(not address.is_global for address in addresses):
        raise UnsafeUrlError("URL host must resolve only to public addresses")


def validate_public_url(value: str, *, allowed_domains: list[str]) -> str:
    normalized = normalize_url(value)
    parsed = urlsplit(normalized)
    host = parsed.hostname or ""
    if not host_is_allowed(host, allowed_domains):
        raise UnsafeUrlError("URL host is not in the configured allowlist")
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    validate_resolved_ips(resolve_public_ips(host, port))
    return normalized
```

`validate_public_url` must reject credentials, non-HTTP(S) schemes, empty hosts, disallowed domains, and any resolved address that is not globally routable.

Implement `SafeHttpClient` around a small connector interface. Resolution returns a `ValidatedTarget(url, hostname, port, addresses)`; successful requests return `SafeHttpResponse(status_code, headers, body, final_url, peer_ip)`. The connector receives one selected validated IP as the connection destination while retaining the original hostname for the HTTP `Host` header and TLS SNI/certificate verification. The connector verifies that the connected peer IP belongs to the validated address set. It must not perform a second hostname lookup. The client disables ambient proxy, cookie, and credential use, validates every redirect as a new target, caps redirects at five, and provides bounded streaming that stops before exceeding the caller's byte limit. Tests inject a fake resolver and connector to prove the connection is pinned and to simulate peer mismatch and redirect-to-private-host rejection.

Implement `RobotsPolicy` using `SafeHttpClient` and `urllib.robotparser`. Fetch at most 512 KiB from `/robots.txt` per origin and cache the parsed result for the run. HTTP 404 means no published restrictions; 401/403 means disallow all; timeout, DNS, 429, and 5xx failures retry within the bounded network policy and then fail closed for that origin. Check `can_fetch` before enqueueing and immediately before fetching every page URL. Media-resource fetching is governed by the separate explicit media allowlist rather than page robots rules. Add tests for disallow, 404, failure, cache reuse, and redirect safety.

- [ ] **Step 4: Run tests**

Run: `python -m pytest worker/tests/test_url_safety.py worker/tests/test_safe_http.py worker/tests/test_robots.py -q`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add worker/app/url_safety.py worker/app/safe_http.py worker/app/robots.py worker/tests/test_url_safety.py worker/tests/test_safe_http.py worker/tests/test_robots.py
git commit -m "feat: enforce public crawl URL safety"
```

### Task 5: Native HTTP and Firecrawl page fetchers

**Files:**
- Create: `worker/app/page_fetchers.py`
- Modify: `worker/app/adapters.py`
- Test: `worker/tests/test_page_fetchers.py`

- [ ] **Step 1: Write contract tests with mocked transports**

```python
from unittest.mock import Mock

from worker.app.page_fetchers import NativeHttpPageFetcher
from worker.app.safe_http import SafeHttpResponse


def test_native_fetcher_returns_raw_html_after_safe_redirect_validation():
    safe_client = Mock()
    safe_client.get.return_value = SafeHttpResponse(
        status_code=200,
        headers={"content-type": "text/html; charset=utf-8"},
        body=b"<html><body>ok</body></html>",
        final_url="https://www.flyforum.cn/thread-522-1-1.html",
        peer_ip="93.184.216.34",
    )
    fetcher = NativeHttpPageFetcher(
        allowed_domains=["www.flyforum.cn"],
        client=safe_client,
    )
    page = fetcher.fetch("https://www.flyforum.cn/thread-522-1-1.html")
    assert page.http_status == 200
    assert page.raw_html == "<html><body>ok</body></html>"
```

- [ ] **Step 2: Verify failure**

Run: `python -m pytest worker/tests/test_page_fetchers.py -q`

Expected: FAIL because the fetcher module is missing.

- [ ] **Step 3: Implement `PageFetcher` and native HTTP**

Define a protocol returning the existing `FetchedPage`. Implement native fetching through `SafeHttpClient` with explicit timeouts, content-type checks, a 5 MiB page limit, and user-agent identification. Redirect, DNS, peer-IP, credential, cookie, and ambient-proxy protections remain centralized in `SafeHttpClient`; the page fetcher must not create a second raw httpx client.

- [ ] **Step 4: Implement Firecrawl REST fetching**

POST to the configured Firecrawl scrape endpoint using `FIRECRAWL_API_KEY`, request HTML/markdown only, and convert its response to `FetchedPage`. Raise `MissingProviderConfiguration` when the key is absent. Sanitize exception messages so the API key and request headers cannot appear.

- [ ] **Step 5: Register `configurable_video` adapter construction**

Change `get_adapter` to accept optional settings/job context or add `build_adapter(parser_profile, source, job)`. Keep deterministic adapters intact for existing tests. The configurable adapter is constructed per run and must not be a mutable module singleton.

- [ ] **Step 6: Run tests**

Run: `python -m pytest worker/tests/test_page_fetchers.py worker/tests/test_worker_runner.py -q`

Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add worker/app/page_fetchers.py worker/app/adapters.py worker/tests/test_page_fetchers.py worker/tests/test_worker_runner.py
git commit -m "feat: add bounded public page fetchers"
```

### Task 6: Generic/Discuz discovery and direct-media extraction

**Files:**
- Create: `worker/app/site_profiles.py`
- Create: `worker/app/video_discovery.py`
- Test: `worker/tests/fixtures/flyforum_forum.html`
- Test: `worker/tests/fixtures/flyforum_thread_video.html`
- Test: `worker/tests/test_video_discovery.py`

- [ ] **Step 1: Add sanitized FlyForum fixtures**

The thread fixture must contain one relative MP4 source, one absolute WebM link, one M3U8 link, one duplicate URL with a fragment, and one third-party iframe. Use synthetic paths and no user identifiers:

```html
<article id="post-demo">
  <video controls><source src="/data/attachment/demo-flight.mp4#t=1" type="video/mp4"></video>
  <a href="https://www.flyforum.cn/data/attachment/demo-flight.webm">WebM</a>
  <a href="https://media.flyforum.cn/demo/playlist.m3u8">HLS</a>
  <a href="/data/attachment/demo-flight.mp4">duplicate</a>
  <iframe src="https://third-party.example/player/1"></iframe>
</article>
```

- [ ] **Step 2: Write failing extraction/discovery tests**

```python
from pathlib import Path

from worker.app.video_discovery import extract_video_candidates


def test_extracts_and_deduplicates_direct_video_urls():
    html = Path("worker/tests/fixtures/flyforum_thread_video.html").read_text("utf-8")
    candidates = extract_video_candidates(
        html,
        page_url="https://www.flyforum.cn/thread-522-1-1.html",
        media_selectors=["article video source[src]", "article a[href]"],
    )
    assert [item.media_kind for item in candidates] == ["mp4", "webm", "m3u8"]
    assert all("third-party.example" not in item.url for item in candidates)
```

- [ ] **Step 3: Verify failure**

Run: `python -m pytest worker/tests/test_video_discovery.py -q`

Expected: FAIL because discovery modules are missing.

- [ ] **Step 4: Implement profiles and bounded breadth-first discovery**

`GenericSiteProfile` extracts allowed anchors using BeautifulSoup. `DiscuzSiteProfile` accepts rewritten paths matching `/forum-\d+-\d+\.html` and `/thread-\d+-\d+-\d+\.html`, rejects redirect/login/post/search paths, and returns stable normalized URLs. The crawler maintains `(url, depth)` queue entries, a visited set, page/video caps, and `time.sleep(request_interval_seconds)` between page requests. When `respect_robots_txt` is true, call `RobotsPolicy.can_fetch` before enqueue and again before fetch; a disallowed or fail-closed URL is recorded as skipped without making a network page request.

- [ ] **Step 5: Implement direct media extraction**

Create immutable `VideoCandidate(url, media_kind, source_element, declared_content_type)`. Extract only MP4/WebM/M3U8 from `<video>`, `<source>`, ordinary links, and the already validated `media_selectors`. Selector matches may contribute only `src` or `href` attributes; text and inline script are never interpreted. Do not process iframes. Normalize and preserve first-seen order. Cap selector matches per page so a valid but broad selector cannot bypass `max_videos` or create unbounded intermediate work.

- [ ] **Step 6: Run tests**

Run: `python -m pytest worker/tests/test_video_discovery.py -q`

Expected: PASS with exactly three candidates.

- [ ] **Step 7: Commit**

```bash
git add worker/app/site_profiles.py worker/app/video_discovery.py worker/tests/fixtures worker/tests/test_video_discovery.py
git commit -m "feat: discover direct videos from configurable sites"
```

### Task 7: Controlled HLS download and local-only FFprobe/FFmpeg fallback

**Files:**
- Create: `worker/app/hls_downloader.py`
- Create: `worker/app/media_probe.py`
- Test: `worker/tests/test_hls_downloader.py`
- Test: `worker/tests/test_media_probe.py`

- [ ] **Step 1: Write tests for nested URL validation, limits, local-only tools, and cleanup**

```python
from pathlib import Path
import pytest

from worker.app.hls_downloader import HlsLimitError, materialize_hls
from worker.app.media_probe import MediaLimitError, assert_media_limits, temporary_remux
from worker.app.url_safety import UnsafeUrlError


def test_media_limits_reject_oversized_video():
    with pytest.raises(MediaLimitError):
        assert_media_limits(size_bytes=101, duration_seconds=10, max_bytes=100, max_duration=20)


def test_hls_rejects_private_nested_playlist(fake_safe_client, tmp_path):
    fake_safe_client.add_text(
        "https://media.example/root.m3u8",
        "#EXTM3U\nhttp://127.0.0.1/private.m3u8\n",
    )
    with pytest.raises(UnsafeUrlError):
        with materialize_hls(
            "https://media.example/root.m3u8",
            client=fake_safe_client,
            allowed_media_domains=["media.example"],
            max_bytes=1_000_000,
            max_segments=10,
            temp_dir=tmp_path,
        ):
            pass


def test_remux_passes_only_local_paths_and_cleans_workspace(monkeypatch, tmp_path):
    local_playlist = tmp_path / "input" / "local.m3u8"
    local_playlist.parent.mkdir()
    local_playlist.write_text("#EXTM3U\n#EXT-X-ENDLIST\n", encoding="utf-8")
    seen = {}

    def fake_run(arguments, timeout):
        seen["arguments"] = arguments
        Path(arguments[-1]).write_bytes(b"mp4")

    monkeypatch.setattr("worker.app.media_probe._run_process", fake_run)
    with temporary_remux(local_playlist, temp_dir=tmp_path, max_duration=20) as output:
        assert output.exists()
    assert all(not value.startswith(("http://", "https://")) for value in seen["arguments"])
    assert "-protocol_whitelist" in seen["arguments"]
    assert not any(path.name.startswith("video-remux-") for path in tmp_path.iterdir())
```

- [ ] **Step 2: Verify failure**

Run: `python -m pytest worker/tests/test_hls_downloader.py worker/tests/test_media_probe.py -q`

Expected: FAIL because the HLS and media modules are missing.

- [ ] **Step 3: Implement the controlled HLS materializer**

Parse master and media playlists as UTF-8 text with a hard per-playlist byte cap. Resolve and fetch every nested playlist, `EXT-X-KEY` URI, `EXT-X-MAP` URI, and media segment through `SafeHttpClient` with `allowed_media_domains`. Reject unsupported URI schemes, unresolved variables, more than `VIDEO_MAX_HLS_SEGMENTS`, cycles, excessive nesting, or aggregate bytes above `VIDEO_MAX_BYTES`. Store each fetched resource under a generated local filename and rewrite all playlist references to those filenames. Never preserve a remote URI in the local playlist. The context manager removes its entire generated workspace in `finally`.

- [ ] **Step 4: Implement media metadata checks using local files**

Use bounded HEAD/range requests through `SafeHttpClient` only for initial type/size checks. Download MP4/WebM fallback inputs through bounded streaming before probing. Invoke FFprobe only on a local path with `-protocol_whitelist file,crypto`, parse numeric fields defensively, and enforce size/duration settings before VLM submission. Reject any public function call that supplies a URL to the FFprobe wrapper.

- [ ] **Step 5: Implement a local-path-only remux context manager**

Invoke FFmpeg with argument arrays, not a shell string:

```python
[
    "ffmpeg", "-nostdin", "-v", "error",
    "-protocol_whitelist", "file,crypto",
    "-i", str(local_playlist_path),
    "-t", str(max_duration), "-c", "copy", str(output_path),
]
```

Require `Path` inputs, reject non-local or missing paths, apply a process timeout, check output size, and remove every created file in `finally`. Use an environment without proxy or credential variables. Redact query strings from downloader errors and events. Tests must assert that neither FFmpeg nor FFprobe receives `http://` or `https://` arguments.

- [ ] **Step 6: Run tests**

Run: `python -m pytest worker/tests/test_hls_downloader.py worker/tests/test_media_probe.py -q`

Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add worker/app/hls_downloader.py worker/app/media_probe.py worker/tests/test_hls_downloader.py worker/tests/test_media_probe.py
git commit -m "feat: validate and remux bounded video media"
```

### Task 8: SiliconFlow VLM prompt, request, validation, and retry

**Files:**
- Create: `worker/app/video_vlm.py`
- Test: `worker/tests/test_video_vlm.py`

- [ ] **Step 1: Write failing request and validation tests**

```python
import httpx
import pytest

from app.schemas.video_config import VideoDescriptionPolicy
from worker.app.video_vlm import (
    DescriptionValidationError,
    SiliconFlowVideoVLM,
    VideoInputOptions,
)


def test_vlm_sends_video_url_and_policy_sections():
    seen = {}
    def handler(request):
        seen.update(request.json())
        return httpx.Response(200, json={"choices": [{"message": {"content": "内容概览\n" + "飞行" * 300 + "\n时间顺序描述\n关键细节\n风险与异常"}}]})
    client = SiliconFlowVideoVLM(
        api_key="test-key",
        base_url="https://api.siliconflow.cn/v1",
        model="Qwen/Qwen3-Omni-30B-A3B-Instruct",
        transport=httpx.MockTransport(handler),
    )
    policy = VideoDescriptionPolicy(min_chars=100, max_chars=3000)
    result = client.describe_url(
        "https://media.example/demo.mp4",
        policy,
        options=VideoInputOptions(detail="high", max_frames=128, fps=1),
    )
    video_part = seen["messages"][1]["content"][1]
    assert video_part == {
        "type": "video_url",
        "video_url": {"url": "https://media.example/demo.mp4"},
    }
    assert "max_frams" not in str(seen)
    assert "内容概览" in result.description


def test_invalid_description_raises_after_one_retry():
    with pytest.raises(DescriptionValidationError):
        validate_description("太短", VideoDescriptionPolicy(min_chars=100))
```

- [ ] **Step 2: Verify failure**

Run: `python -m pytest worker/tests/test_video_vlm.py -q`

Expected: FAIL because the VLM module is missing.

- [ ] **Step 3: Implement prompt builder and typed result**

Create `VideoDescriptionResult(description, provider, model, usage_json, trace_json)` and immutable `VideoInputOptions(detail: str | None, max_frames: int | None, fps: float | None)`. Build a fixed system instruction prohibiting unsupported claims and a user prompt that serializes language, focus areas, required sections, exclusions, and length bounds. Delimit `custom_instruction` as untrusted policy text.

- [ ] **Step 4: Implement SiliconFlow API calls**

POST to `${VLM_BASE_URL}/chat/completions` with Bearer auth, configured timeout, `temperature=0.2`, and the minimal provider contract:

```json
[
  {"type": "text", "text": "请按内容概览、时间顺序描述、关键细节、风险与异常四个章节描述视频。"},
  {"type": "video_url", "video_url": {"url": "https://www.flyforum.cn/data/attachment/demo-flight.mp4"}}
]
```

The pipeline passes `VideoInputOptions` to the adapter, but the default SiliconFlow capability map marks detail/frame/fps fields unsupported and therefore omits them. Add an optional field only after its exact spelling and location are confirmed in current official provider documentation and represented by a committed mocked request fixture; never guess or preserve the old `max_frams` spelling. This keeps optional provider concerns out of domain code.

Also implement Base64 MP4 input by prefixing the encoded bytes with `data:video/mp4;base64,`. Do not retain the encoded content in trace data. Parse only `choices[0].message.content`; raise typed errors for HTTP, shape, empty, and validation failures.

- [ ] **Step 5: Implement one validation-feedback retry**

Retry only invalid descriptions, appending exact missing sections or length violations. HTTP retry uses tenacity for 429/5xx/timeouts with at most three attempts and bounded exponential waits. Never retry 400/401/403.

- [ ] **Step 6: Run tests**

Run: `python -m pytest worker/tests/test_video_vlm.py -q`

Expected: PASS and captured traces contain no API key or Base64.

- [ ] **Step 7: Commit**

```bash
git add worker/app/video_vlm.py worker/tests/test_video_vlm.py
git commit -m "feat: analyze videos with constrained SiliconFlow VLM"
```

### Task 9: Real embedding provider for semantic Qdrant matching

**Files:**
- Modify: `backend/app/services/embeddings.py`
- Modify: `worker/app/chunk_indexer.py`
- Modify: `backend/app/services/search.py`
- Test: `backend/tests/test_chunking_and_indexing.py`
- Test: `backend/tests/test_search_pipeline.py`

- [ ] **Step 1: Write a failing OpenAI-compatible embedding test**

```python
import httpx

from app.services.embeddings import OpenAICompatibleEmbeddingService


def test_embedding_service_calls_configured_model():
    seen = {}
    def handler(request):
        seen.update(request.json())
        return httpx.Response(200, json={"data": [{"embedding": [0.1, 0.2, 0.3]}]})
    service = OpenAICompatibleEmbeddingService(
        api_key="test-key",
        base_url="https://api.siliconflow.cn/v1",
        model_name="BAAI/bge-m3",
        dimension=3,
        transport=httpx.MockTransport(handler),
    )
    assert service.embed("无人机飞行") == [0.1, 0.2, 0.3]
    assert seen == {"model": "BAAI/bge-m3", "input": "无人机飞行", "encoding_format": "float"}
```

- [ ] **Step 2: Verify failure**

Run: `python -m pytest backend/tests/test_chunking_and_indexing.py -q`

Expected: FAIL because the embedding class is missing.

- [ ] **Step 3: Implement provider and factory**

Keep `DeterministicEmbeddingService`. Add an OpenAI-compatible implementation with exact dimension validation, sanitized errors, timeouts, and Bearer auth. Add:

```python
def build_embedding_service(settings: Settings) -> EmbeddingService:
    if settings.embedding_provider == "deterministic":
        return DeterministicEmbeddingService()
    if settings.embedding_provider == "siliconflow":
        if not settings.vlm_api_key:
            raise RuntimeError("VLM_API_KEY is required for SiliconFlow embeddings")
        return OpenAICompatibleEmbeddingService(
            api_key=settings.vlm_api_key,
            base_url=settings.vlm_base_url,
            model_name=settings.embedding_model,
            dimension=1024,
        )
    raise ValueError(f"Unsupported embedding provider: {settings.embedding_provider}")
```

- [ ] **Step 4: Use the factory in indexing and retrieval**

Replace direct deterministic-service construction in `chunk_indexer.py` and default search service construction. Preserve dependency injection so tests continue using deterministic/fake services.

- [ ] **Step 5: Run indexing/search tests**

Run: `python -m pytest backend/tests/test_chunking_and_indexing.py backend/tests/test_search_pipeline.py -q`

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add backend/app/services/embeddings.py worker/app/chunk_indexer.py backend/app/services/search.py backend/tests/test_chunking_and_indexing.py backend/tests/test_search_pipeline.py
git commit -m "feat: add semantic embedding provider"
```

### Task 10: Video repository and idempotent content persistence

**Files:**
- Create: `backend/app/repositories/videos.py`
- Create: `backend/app/schemas/video.py`
- Modify: `backend/tests/conftest.py`
- Test: `backend/tests/test_video_repository.py`

- [ ] **Step 1: Write repository integration tests**

```python
async def test_successful_analysis_links_discovery_asset_content_and_pending_chunks(
    session, seeded_video_context
):
    repo = VideosRepository(session)
    discovered = await repo.record_discovery(
        source_site_id=seeded_video_context.source.id,
        crawl_run_id=seeded_video_context.run.id,
        raw_page_id=seeded_video_context.raw_page.id,
        source_page_url="https://www.flyforum.cn/thread-522-1-1.html",
        video_url="https://www.flyforum.cn/data/attachment/demo.mp4",
        normalized_video_url="https://www.flyforum.cn/data/attachment/demo.mp4",
        media_type="video/mp4",
    )
    persisted = await repo.persist_description_and_pending_chunks(
        asset=discovered.asset,
        discovery=discovered.discovery,
        description="内容概览\n飞行演示。\n时间顺序描述\n起飞后转弯。\n关键细节\nFPV。\n风险与异常\n未见异常。",
        provider="siliconflow",
        model="Qwen/Qwen3-Omni-30B-A3B-Instruct",
        policy_snapshot={"language": "zh-CN"},
        trace={"latency_ms": 10},
    )
    assert persisted.asset.content_item_id == persisted.content_item.id
    assert persisted.asset.analysis_discovery_id == discovered.discovery.id
    assert persisted.content_item.item_type == "video_description"
    assert persisted.chunks
    assert {chunk.embed_status for chunk in persisted.chunks} == {"pending"}


async def test_repeated_discovery_reuses_asset_without_overwriting_history(
    session, seeded_video_context, second_seeded_run_and_raw_page
):
    repo = VideosRepository(session)
    first = await repo.record_discovery(**seeded_video_context.discovery_kwargs)
    second_kwargs = {
        **seeded_video_context.discovery_kwargs,
        **second_seeded_run_and_raw_page.discovery_lineage_kwargs,
    }
    second = await repo.record_discovery(**second_kwargs)
    assert first.asset.id == second.asset.id
    assert first.discovery.id != second.discovery.id
    assert await repo.count_discoveries(first.asset.id) == 2
```

- [ ] **Step 2: Verify failure**

Run: `python -m pytest backend/tests/test_video_repository.py -q`

Expected: FAIL because repository/schema modules are missing.

- [ ] **Step 3: Implement asset upsert and claim**

Use SQLAlchemy's PostgreSQL insert builder to upsert only stable/latest asset fields on `(source_site_id, normalized_video_url)`: observed `video_url`, media type, status inputs, and `updated_at`. Never copy run, raw-page, or source-page lineage onto the asset. Insert a `VideoAssetDiscovery` for every deduplicated `(crawl_run_id, raw_page_id, video_asset_id)` occurrence and return `RecordedDiscovery(asset, discovery, created)`. `claim_for_analysis` locks the asset with `FOR UPDATE SKIP LOCKED`, changes eligible `discovered/failed` assets to `analyzing`, and receives the triggering discovery explicitly. An already successful unchanged asset skips analysis but retains the new discovery row.

- [ ] **Step 4: Implement description persistence**

Create/update one `ContentItem` with item type `video_description`, canonical URL equal to normalized video URL, source URL equal to the triggering discovery's page URL, full description in `cleaned_text`, asset and discovery identifiers in metadata, stable SHA-256 content hash, and a dedup key based on source plus normalized URL. In the same transaction, update the asset link/status/trace/`analysis_discovery_id`, build replacement chunks, mark replaced chunks `obsolete`, and create replacements with `embed_status="pending"`. Return `PersistedVideoDescription(asset, discovery, content_item, chunks, obsolete_vector_point_ids)`. The caller commits this transaction before performing any Qdrant deletion or upsert.

- [ ] **Step 5: Implement list/detail records**

Add source/status/media/text filters and a detail query joining asset, current analysis discovery, content, raw page, source, run, and chunks. Include a separately bounded recent-discoveries query so detail responses expose history without an unbounded join. Pydantic response models must exclude secrets and Base64.

- [ ] **Step 6: Run tests**

Run: `python -m pytest backend/tests/test_video_repository.py -q`

Expected: PASS, including two runs that reuse one asset while retaining two discovery rows and pending chunks created before the first commit.

- [ ] **Step 7: Commit**

```bash
git add backend/app/repositories/videos.py backend/app/schemas/video.py backend/tests/conftest.py backend/tests/test_video_repository.py
git commit -m "feat: persist video descriptions with content lineage"
```

### Task 11: End-to-end worker video pipeline

**Files:**
- Create: `worker/app/video_pipeline.py`
- Modify: `worker/app/runner.py`
- Modify: `worker/app/chunk_indexer.py`
- Test: `worker/tests/test_video_pipeline.py`
- Test: `worker/tests/test_worker_runner.py`

- [ ] **Step 1: Write a failing successful-run test with fakes**

```python
def test_configurable_video_run_persists_analyzes_and_indexes(
    monkeypatch, video_job_run_id, fake_page_fetcher, fake_vlm, fake_indexer, load_run
):
    monkeypatch.setattr("worker.app.video_pipeline.build_page_fetcher", lambda *a, **k: fake_page_fetcher)
    monkeypatch.setattr("worker.app.video_pipeline.build_video_vlm", lambda *a, **k: fake_vlm)
    monkeypatch.setattr("worker.app.chunk_indexer._build_qdrant_indexer", lambda: fake_indexer)
    result = run_once(run_id=video_job_run_id)
    assert result.succeeded == 1
    assert fake_vlm.urls == ["https://www.flyforum.cn/data/attachment/demo.mp4"]
    assert fake_indexer.upserted_payloads[0]["video_url"].endswith("demo.mp4")
    persisted_run = load_run(video_job_run_id)
    assert persisted_run.video_discovered_count == 1
    assert persisted_run.video_analyzed_count == 1
    assert persisted_run.video_chunked_count > 0
    assert persisted_run.video_indexed_count == persisted_run.video_chunked_count
```

- [ ] **Step 2: Verify failure**

Run: `python -m pytest worker/tests/test_video_pipeline.py -q`

Expected: FAIL because the pipeline module is missing.

- [ ] **Step 3: Implement orchestration boundaries**

`process_video_run(session, run, source, job)` parses config, crawls pages, persists each `RawPage` before extraction, records each candidate as a stable asset plus discovery row, validates/claims assets, probes media, and calls VLM. For a valid description it calls `persist_description_and_pending_chunks` and commits asset/content/pending/obsolete chunk state together. Only after that commit does it call an indexing function that deletes obsolete points, upserts pending chunks, and commits resulting vector states. It returns typed six-counter values and an outcome (`success`, `partial`, `failed`). Add a transaction-order test whose fake indexer asserts it cannot be called until a separate database session can observe the pending chunks.

- [ ] **Step 4: Add URL-first and M3U8 fallback behavior**

Call `describe_url` first. Only for a typed provider rejection on M3U8, enter `materialize_hls`, which downloads and rewrites every nested resource through `SafeHttpClient`; then pass its local playlist path to `temporary_remux`, enforce final file limits, and call `describe_base64_mp4`. FFmpeg and FFprobe never receive the original URL. Mark unsupported/disallowed/over-limit media skipped and genuine fetch/probe/VLM/persistence failures failed.

- [ ] **Step 5: Record sanitized run events and agent calls**

Add events for `video_discovered`, `video_skipped`, `video_validation_failed`, `vlm_started`, `vlm_succeeded`, `vlm_failed`, `description_invalid`, `video_chunks_pending`, `video_indexed`, and `video_index_failed`. Store provider/model/latency/usage summaries only. Increment all six dedicated counters according to the Spec definitions and preserve existing page/chunk/error counters. A Qdrant failure marks chunks failed, increments generic `error_count`, and produces a partial run; it does not change a successfully described asset to failed or increment `video_failed_count`.

- [ ] **Step 6: Route configurable jobs from the runner**

At the start of `_process_run`, when `parser_profile == "configurable_video"`, delegate to `process_video_run`; otherwise execute the existing extraction flow unchanged.

- [ ] **Step 7: Include video identifiers in Qdrant payloads**

For `video_description` items, read `video_asset_id`, `video_asset_discovery_id`, `video_url`, and `source_page_url` from metadata and add them to `_build_chunk_payload`. Add a focused `index_pending_content_chunks` path that consumes already-created pending chunks instead of rebuilding them. Do not alter non-video payloads or the existing general ingestion path.

- [ ] **Step 8: Run worker tests**

Run: `python -m pytest worker/tests/test_video_pipeline.py worker/tests/test_worker_runner.py backend/tests/test_chunking_and_indexing.py -q`

Expected: PASS for commit-before-index ordering, discovery-history retention, all six counters, partial Qdrant failure semantics, VLM failure isolation, unchanged-video skip, nested HLS rejection, and temporary cleanup.

- [ ] **Step 9: Commit**

```bash
git add worker/app/video_pipeline.py worker/app/runner.py worker/app/chunk_indexer.py worker/tests/test_video_pipeline.py worker/tests/test_worker_runner.py backend/tests/test_chunking_and_indexing.py
git commit -m "feat: run observable video ingestion pipeline"
```

### Task 12: Cron scheduling for due video jobs

**Files:**
- Create: `worker/app/scheduler.py`
- Create: `scripts/run_scheduler.py`
- Modify: `Makefile`
- Test: `worker/tests/test_scheduler.py`

- [ ] **Step 1: Write due-job tests**

```python
from datetime import datetime, timezone

from worker.app.scheduler import next_cron_time


def test_next_cron_time_is_timezone_aware():
    now = datetime(2026, 6, 23, 10, 0, tzinfo=timezone.utc)
    assert next_cron_time("*/15 * * * *", now).isoformat() == "2026-06-23T10:15:00+00:00"
```

- [ ] **Step 2: Verify failure**

Run: `python -m pytest worker/tests/test_scheduler.py -q`

Expected: FAIL because scheduler module is missing.

- [ ] **Step 3: Implement scheduler claiming**

Select enabled jobs where `trigger_mode == "cron"`, `cron_expr IS NOT NULL`, and `next_run_at <= now`, locking with `SKIP LOCKED`. For each, create one queued run plus event, set `last_run_at=now`, and compute `next_run_at` with croniter in UTC. For an invalid expression, emit a sanitized logger warning containing only job ID and exception class, and leave the job unchanged for operator correction.

- [ ] **Step 4: Add scheduler CLI and Make target**

`scripts/run_scheduler.py` supports `--once` and a polling loop using `WORKER_POLL_INTERVAL_SECONDS`. Add `scheduler-once` and `scheduler` targets without changing existing worker targets.

- [ ] **Step 5: Run tests**

Run: `python -m pytest worker/tests/test_scheduler.py backend/tests/test_control_api.py -q`

Expected: PASS and repeated scheduler polls do not create duplicate due runs.

- [ ] **Step 6: Commit**

```bash
git add worker/app/scheduler.py scripts/run_scheduler.py worker/tests/test_scheduler.py Makefile
git commit -m "feat: schedule due crawl jobs"
```

### Task 13: Video APIs and grouped retrieval evidence

**Files:**
- Create: `backend/app/api/videos.py`
- Create: `backend/app/agents/answer_client.py`
- Modify: `backend/app/api/router.py`
- Modify: `backend/app/schemas/search.py`
- Modify: `backend/app/services/retrieval.py`
- Modify: `backend/app/services/search.py`
- Test: `backend/tests/test_video_api.py`
- Test: `backend/tests/test_search_pipeline.py`
- Test: `backend/tests/test_answer_pipeline.py`

- [ ] **Step 1: Write failing API and grouping tests**

```python
async def test_video_search_groups_multiple_chunks(client, seeded_video_with_chunks):
    response = await client.post("/search", json={
        "query": "低空穿越 建筑 风险",
        "mode": "search",
        "filters": {"item_type": "video_description"},
        "top_k": 5,
    })
    assert response.status_code == 200
    evidence = response.json()["evidence"]
    assert len(evidence) == 1
    assert evidence[0]["video_url"].endswith("demo.mp4")
    assert len(evidence[0]["matched_chunks"]) <= 3
    assert evidence[0]["full_description"]
```

- [ ] **Step 2: Verify failure**

Run: `python -m pytest backend/tests/test_video_api.py backend/tests/test_search_pipeline.py -q`

Expected: FAIL because video routes/evidence fields are absent.

- [ ] **Step 3: Implement `/videos` routes**

Use repository list/detail records, `limit` 1–100, offset, source/status/media/q filters, and 404 for missing IDs. Detail includes the current analysis discovery and at most 20 recent discovery-history rows ordered newest first. Register the router.

- [ ] **Step 4: Extend filters and evidence schemas**

Add optional `media_type` to `SearchFilters`. Add optional video fields to `EvidenceObject` so non-video search remains backward compatible. Define:

```python
class MatchedChunk(BaseModel):
    chunk_id: uuid.UUID
    snippet: str
    score: float

video_asset_id: uuid.UUID | None = None
video_url: str | None = None
source_page_url: str | None = None
full_description: str | None = None
matched_chunks: list[MatchedChunk] = Field(default_factory=list)
```

- [ ] **Step 5: Group video hits after chunk hydration**

Join `VideoAsset` by `content_item_id` and its current `analysis_discovery_id` to obtain the source page that produced the indexed description. Non-video evidence remains one item per chunk. Video evidence groups by asset ID, takes the maximum combined score, preserves vector/keyword component maxima, sorts descending, and attaches at most three highest-scoring unique chunks. Return at most `top_k` grouped evidence entries.

- [ ] **Step 6: Upgrade answer synthesis without losing citations**

Create `EvidenceAnswerClient` in `backend/app/agents/answer_client.py`. It calls `${VLM_BASE_URL}/chat/completions` with the configured model in text-only mode, temperature `0.1`, a system instruction that forbids unsupported claims, and numbered descriptions/snippets capped at 20,000 input characters. It returns only `choices[0].message.content`, records sanitized latency/usage metadata, and falls back to the existing deterministic evidence join when provider configuration is absent or the request fails. Return the same grouped evidence in `supporting_evidence`.

- [ ] **Step 7: Run API/search/answer tests**

Run: `python -m pytest backend/tests/test_video_api.py backend/tests/test_search_pipeline.py backend/tests/test_answer_pipeline.py -q`

Expected: PASS for video grouping, media filter, full description, empty evidence, and non-video backward compatibility.

- [ ] **Step 8: Commit**

```bash
git add backend/app/api/videos.py backend/app/agents/answer_client.py backend/app/api/router.py backend/app/schemas/search.py backend/app/services/retrieval.py backend/app/services/search.py backend/tests/test_video_api.py backend/tests/test_search_pipeline.py backend/tests/test_answer_pipeline.py
git commit -m "feat: expose grouped video retrieval evidence"
```

### Task 14: Frontend API types, navigation, and Videos view

**Files:**
- Modify: `frontend/src/api/types.ts`
- Modify: `frontend/src/api/client.ts`
- Modify: `frontend/src/App.tsx`
- Create: `frontend/src/pages/VideosPage.tsx`
- Modify: `frontend/src/styles.css`
- Test: `frontend/tests/videos-page.test.tsx`
- Test: `frontend/tests/app-video-navigation.test.tsx`

- [ ] **Step 1: Write failing navigation/list/detail tests**

```tsx
it('opens the Videos view and loads a selected asset', async () => {
  vi.spyOn(api, 'listVideos').mockResolvedValue(videoPage);
  vi.spyOn(api, 'getVideo').mockResolvedValue(videoDetail);
  render(<App />);
  await userEvent.click(screen.getByRole('button', { name: /视频 Videos/ }));
  expect(await screen.findByText('公开视频分析 Video Analysis')).toBeInTheDocument();
  expect(screen.getByRole('link', { name: '打开视频' })).toHaveAttribute(
    'href',
    'https://www.flyforum.cn/data/attachment/demo.mp4',
  );
});
```

- [ ] **Step 2: Verify failure**

Run: `cd frontend && npm test -- videos-page.test.tsx app-video-navigation.test.tsx`

Expected: FAIL because video API functions and view do not exist.

- [ ] **Step 3: Add exact TypeScript contracts and API functions**

Define `VideoAssetListItem`, `VideoAssetDiscovery`, `VideoAssetDetail` (including `analysis_discovery` and bounded `recent_discoveries`), `MatchedChunk`, and extended evidence fields matching backend JSON. Add `listVideos(params)` and `getVideo(id)` using existing `request`/`buildPath` helpers.

- [ ] **Step 4: Add navigation and Videos page**

Add `'Videos'` to `View` and `NAV_ITEMS`, label it `视频 Videos`, and render `VideosPage`. The page shows list/detail loading and failures independently, status/model/media metadata, full description, chunks/vector state, source link, and direct video link through `safeExternalHref`. Do not embed remote video by default.

- [ ] **Step 5: Add focused styles**

Reuse cards, grids, badges, and link rows. Add only `.video-description`, `.video-link-row`, and responsive list/detail layout rules; avoid a parallel design system.

- [ ] **Step 6: Run tests and build**

Run: `cd frontend && npm test -- videos-page.test.tsx app-video-navigation.test.tsx && npm run build`

Expected: tests PASS and Vite build succeeds.

- [ ] **Step 7: Commit**

```bash
git add frontend/src/api frontend/src/App.tsx frontend/src/pages/VideosPage.tsx frontend/src/styles.css frontend/tests/videos-page.test.tsx frontend/tests/app-video-navigation.test.tsx
git commit -m "feat: add video analysis frontend view"
```

### Task 15: Frontend job configuration, run counters, and video evidence

**Files:**
- Create: `frontend/src/components/VideoJobConfig.tsx`
- Create: `frontend/src/components/VideoEvidenceCard.tsx`
- Modify: `frontend/src/pages/SourcesPage.tsx`
- Modify: `frontend/src/pages/RunDetailPage.tsx`
- Modify: `frontend/src/pages/SearchPage.tsx`
- Modify: `frontend/src/api/client.ts`
- Test: `frontend/tests/video-job-config.test.tsx`
- Test: `frontend/tests/run-detail-page.test.tsx`
- Test: `frontend/tests/search-page.test.tsx`

- [ ] **Step 1: Write failing component tests**

```tsx
it('submits bounded video job configuration and description policy', async () => {
  const onSave = vi.fn();
  render(<VideoJobConfig initialValue={flyforumConfig} onSave={onSave} />);
  await userEvent.clear(screen.getByLabelText('最大视频数'));
  await userEvent.type(screen.getByLabelText('最大视频数'), '10');
  await userEvent.click(screen.getByRole('button', { name: '保存配置' }));
  expect(onSave).toHaveBeenCalledWith(expect.objectContaining({
    seed_config_json: expect.objectContaining({ max_videos: 10 }),
    agent_policy_json: expect.objectContaining({ description_policy: expect.any(Object) }),
  }));
});
```

- [ ] **Step 2: Verify failure**

Run: `cd frontend && npm test -- video-job-config.test.tsx run-detail-page.test.tsx search-page.test.tsx`

Expected: FAIL because components/fields do not exist.

- [ ] **Step 3: Add create/update API calls and typed form**

Add source/job create/update functions already supported by backend routes. The form edits fetch mode, URLs/domains/patterns, media selectors, limits, request interval, Cron, language, focus areas, required sections, exclusions, length bounds, and custom instruction. Parse comma/newline lists explicitly, enforce selector/count/length and numeric limits client-side, and show backend 422 messages through `formatErrorMessage`.

- [ ] **Step 4: Extend Sources and Runs**

Render `VideoJobConfig` only for `configurable_video` jobs and support creating the FlyForum source/job. Add all six video counters to `COUNTER_FIELDS`, with labels that distinguish asset counts from chunk counts, and retain existing run/event behavior.

- [ ] **Step 5: Render grouped video evidence**

`VideoEvidenceCard` shows overall/vector/keyword scores, at most three matched chunks, full description, source-page link, safe direct-video link, and a button that opens the Videos detail view. Non-video evidence continues using `EvidenceCard`.

- [ ] **Step 6: Run frontend tests and build**

Run: `cd frontend && npm test && npm run build`

Expected: all frontend tests PASS and production build succeeds.

- [ ] **Step 7: Commit**

```bash
git add frontend/src/components frontend/src/pages frontend/src/api/client.ts frontend/tests
git commit -m "feat: configure and search video ingestion in frontend"
```

### Task 16: FlyForum seed, opt-in live verification, documentation, and complete regression

**Files:**
- Create: `scripts/seed_flyforum_video.py`
- Create: `scripts/smoke_video_demo.py`
- Modify: `README.md`
- Modify: `skills/dji-lito-demo-ingestion/SKILL.md`
- Test: `worker/tests/test_flyforum_seed_safety.py`
- Test: `backend/tests/test_video_smoke_contract.py`

- [ ] **Step 1: Write safety and smoke-contract tests**

```python
def test_flyforum_seed_is_bounded_and_public():
    config = build_flyforum_seed_config()
    assert config["start_urls"] == ["https://www.flyforum.cn/forum.php"]
    assert config["max_pages"] <= 5
    assert config["max_videos"] <= 2
    assert config["respect_robots_txt"] is True
    assert "www.flyforum.cn" in config["allowed_domains"]
    assert config["media_selectors"]
```

- [ ] **Step 2: Verify failure**

Run: `python -m pytest worker/tests/test_flyforum_seed_safety.py backend/tests/test_video_smoke_contract.py -q`

Expected: FAIL because seed/smoke modules are missing.

- [ ] **Step 3: Implement idempotent FlyForum seed**

Create/reuse one `SourceSite(site_type="forum", fetch_mode="native_http")` whose `allowed_domains` and `config_json.allowed_media_domains` establish the hard security bounds, plus one `CrawlJob(parser_profile="configurable_video")` whose repeated allowlists are subsets of those bounds. Use rewritten forum/thread paths, validated media selectors, maximum five pages/two videos, 1.5-second delay, and the approved Chinese description policy. Print only source/job/run IDs as JSON.

- [ ] **Step 4: Implement opt-in smoke verification**

Require `ALLOW_LIVE_FLYFORUM_SMOKE=1`. Trigger/execute one bounded run, then assert: at least one discovery-history row or an explicit no-video outcome; every discovery links asset/run/raw-page/source-page lineage; successful descriptions have pending-then-indexed chunks; successful chunks have vector point IDs; all six run counters reconcile with persisted rows; `/search` returns URL/full description/matched chunks; `/answer` returns supporting evidence. Print sanitized statuses and IDs only.

- [ ] **Step 5: Update project and required skill documentation**

Document infrastructure, migration, FFmpeg prerequisite, environment variable names, seed/run commands, live-test opt-in, retrieval queries, and known provider/media limits. Update `skills/dji-lito-demo-ingestion/SKILL.md` because AGENTS.md requires that skill to remain the authoritative demo workflow. Keep DJI and FlyForum instructions clearly separated.

- [ ] **Step 6: Run complete verification**

Run:

```bash
python -m ruff check backend worker scripts
python -m mypy backend worker
python -m pytest -q
cd frontend && npm test && npm run build
```

Expected: lint, type checking, all Python tests, all frontend tests, and frontend build PASS.

- [ ] **Step 7: Run local integration verification**

With local PostgreSQL/Qdrant and non-secret runtime environment configured:

```powershell
python -m alembic upgrade head
$seed = python scripts/seed_flyforum_video.py --json | ConvertFrom-Json
python scripts/run_worker_once.py --json --require-success --run-id $seed.run_id
```

Expected: bounded run finishes `success` or `partial`; any partial result has an explicit sanitized event explaining VLM/media/Qdrant failure. Do not run the live network path unless explicitly authorized at execution time.

- [ ] **Step 8: Commit**

```bash
git add scripts/seed_flyforum_video.py scripts/smoke_video_demo.py README.md skills/dji-lito-demo-ingestion/SKILL.md worker/tests/test_flyforum_seed_safety.py backend/tests/test_video_smoke_contract.py
git commit -m "docs: add bounded FlyForum video demo workflow"
```

---

## Final acceptance checklist

- [ ] A configured public site can be crawled without authentication or prohibited-path access.
- [ ] FlyForum direct MP4/WebM/M3U8 candidates are extracted and deduplicated.
- [ ] Every discovery occurrence retains asset, source, run, raw-page, and source-page lineage.
- [ ] Repeated runs reuse the stable asset while appending distinct discovery-history rows.
- [ ] SiliconFlow receives a constrained video-description prompt without secret logging.
- [ ] M3U8 URL rejection uses controlled nested-resource download plus local-only bounded remux/Base64 fallback and always cleans temporary files.
- [ ] DNS validation is bound to the actual peer connection, and FFmpeg/FFprobe receive local paths only.
- [ ] Full descriptions live in PostgreSQL and description chunks map to Qdrant point IDs.
- [ ] Production configuration uses semantic embeddings; tests remain deterministic.
- [ ] All six video counters reconcile with asset/discovery/chunk state and use non-overlapping semantics.
- [ ] Search groups chunk matches by video and returns URL, full description, snippets, and scores.
- [ ] Answer synthesis returns supporting video evidence and declines unsupported claims.
- [ ] Existing non-video ingestion/search paths remain backward compatible.
- [ ] Existing React frontend supports configuration, run inspection, video detail, search, and answer.
- [ ] Full Python/frontend regression, lint, typing, migration, and build checks pass.
