# Configurable Video VLM RAG Design

## 1. Goal

Extend the existing Intelligence RAG modular monolith into a configurable, multi-site public-web video ingestion system. The first supported site is `https://www.flyforum.cn/forum.php`. The system discovers directly accessible MP4, WebM, and M3U8 URLs, sends each video to a configurable VLM API for policy-constrained Chinese descriptions, persists the video and description in PostgreSQL, indexes description chunks in Qdrant, and returns the corresponding video URL, full description, matched chunks, and scores through search and answer workflows.

The first release handles only public, directly accessible media URLs. It does not log in, bypass CAPTCHA or anti-bot controls, resolve protected third-party players, or extract hidden streams from services such as Bilibili or Youku.

## 2. Existing-System Fit

The implementation extends the current backend, worker, database, retrieval pipeline, and React frontend rather than adding a separate service.

- PostgreSQL remains the source of truth.
- Qdrant stores vectors and retrieval payloads only.
- Raw pages are persisted before parsing or VLM analysis.
- The existing `SourceSite -> CrawlJob -> CrawlRun -> CrawlRunEvent` control model remains authoritative.
- Video descriptions use the existing `ContentItem -> ContentChunk -> vector_point_id` lineage.
- Existing `/search` and `/answer` workflows are extended instead of duplicated.

## 3. Architecture

### 3.1 Site and job configuration

`SourceSite` owns site-level security bounds: base URL, page-domain allowlist, fetch mode, path rules, adapter/profile name, and `config_json.allowed_media_domains`. A job may narrow these bounds but cannot expand them.

`CrawlJob.seed_config_json` owns run controls and repeats page/media allowlists as an immutable run snapshot. Both lists must be subsets of the owning source's allowlists, and the API and worker validate that relationship independently:

```json
{
  "start_urls": ["https://www.flyforum.cn/forum.php"],
  "allowed_domains": ["www.flyforum.cn"],
  "follow_patterns": ["/forum-*.html", "/thread-*.html"],
  "exclude_patterns": ["/member.php*", "/search.php*", "*mod=post*"],
  "media_selectors": ["video source[src]", "a[href$='.mp4']"],
  "max_depth": 2,
  "max_pages": 50,
  "max_videos": 10,
  "request_interval_seconds": 1.5,
  "respect_robots_txt": true
}
```

The job supports manual triggering and Cron scheduling. All limits are enforced again inside the worker so malformed or stale configuration cannot create an unbounded crawl.

### 3.2 Page fetchers and site profiles

A `PageFetcher` interface isolates page acquisition from discovery and parsing.

- `NativeHttpPageFetcher` uses `httpx` and is the default for FlyForum's server-rendered Discuz pages.
- `FirecrawlPageFetcher` is optional for sites that require JavaScript rendering and uses `FIRECRAWL_API_KEY`.
- Neither fetcher is permitted to bypass authentication, CAPTCHA, access controls, robots rules, or explicit rate limits.

A `ConfigurableWebsiteAdapter` handles rules shared across sites. Site profiles add narrow behavior without changing the ingestion core. The first `DiscuzSiteProfile` understands FlyForum forum pages, thread pages, rewritten URLs, and pagination while avoiding robots-disallowed redirect, login, posting, and search paths.

### 3.3 Video discovery

After a page is stored as `RawPage`, the parser discovers video candidates from:

- `<video src>`;
- `<video><source src>`;
- ordinary links ending in `.mp4`, `.webm`, or `.m3u8`;
- configured CSS selectors for site-specific markup.

Relative URLs are resolved against the final page URL. Fragments are removed, host and scheme are normalized, and duplicate candidates are collapsed by normalized URL. Each candidate records the source page and raw-page lineage.

### 3.4 VLM analysis

The first provider is SiliconFlow's OpenAI-compatible API:

- base URL: `VLM_BASE_URL`;
- API key: `VLM_API_KEY`;
- model: `VLM_MODEL`, initially `Qwen/Qwen3-Omni-30B-A3B-Instruct`.

The provider client sends a chat-completions request whose user message contains a policy-built text prompt and a `video_url` content part. MP4, WebM, and M3U8 candidates are first submitted as public URLs.

If the provider rejects a public M3U8 URL, the worker may materialize it through an application-controlled HLS downloader. The downloader validates the playlist URL, every redirect, nested playlist, encryption-key URL, and media-segment URL against the media-domain and public-IP policy before fetching bytes. It enforces aggregate byte, segment, redirect, and duration limits and writes only bounded local inputs. FFmpeg never receives a remote URL: it operates only on the validated local playlist and local segments, remuxes them into a temporary MP4, and submits bounded Base64 video data. Temporary playlists, segments, and outputs are deleted in a `finally` path after success or failure. MP4/WebM download-to-Base64 is not the default and is used only by an explicitly configured bounded fallback through the same controlled downloader.

The SiliconFlow request serializer is provider-specific. Its field names and supported content-part options must be verified against the provider documentation current at implementation time and captured in a mocked contract fixture. The core pipeline passes typed values such as URL, detail level, frame cap, and sampling rate to the adapter; it does not embed undocumented provider spellings in domain code.

The VLM response must be non-empty, fall within the configured character limits, and contain all required sections. A response that fails validation is retried once with validation feedback. A second invalid response marks the asset failed without affecting other videos in the run.

## 4. Description Policy

`CrawlJob.agent_policy_json.description_policy` controls the description:

```json
{
  "description_policy": {
    "language": "zh-CN",
    "focus_areas": ["场景", "主体", "动作过程", "设备与技术细节", "异常与风险"],
    "required_sections": ["内容概览", "时间顺序描述", "关键细节", "风险与异常"],
    "exclude_topics": [],
    "min_chars": 500,
    "max_chars": 3000,
    "custom_instruction": ""
  }
}
```

The backend validates policy shape and bounds before a job can be created or updated. The prompt builder treats these fields as data, not executable instructions, and places system safety requirements outside the user-configurable portion.

## 5. Data Model

### 5.1 `video_assets`

Add a `video_assets` table with:

- `id` UUID primary key;
- `source_site_id` foreign key;
- nullable `content_item_id` foreign key populated after successful analysis;
- nullable `analysis_discovery_id` foreign key identifying the discovery occurrence used for the current description;
- original `video_url`;
- `normalized_video_url`;
- `media_type` (`video/mp4`, `video/webm`, or an M3U8 media type);
- `status` (`discovered`, `analyzing`, `success`, `failed`, or `skipped`);
- nullable `duration_seconds` and `size_bytes`;
- nullable `content_hash`;
- nullable `vlm_provider` and `vlm_model`;
- `description_policy_json` snapshot;
- `analysis_trace_json` containing sanitized request/response metadata;
- nullable `error_message`;
- creation and update timestamps.

`(source_site_id, normalized_video_url)` is unique. This row represents the stable identity and latest analysis state of a video, not an individual crawl occurrence. `analysis_discovery_id` points to the exact run, raw page, and source page used for the current description. Because it references the discovery table, the migration creates both tables first and then adds this foreign key. The worker locks an asset before analysis to prevent concurrent duplicate VLM calls.

### 5.2 `video_asset_discoveries`

Add a separate append-only discovery table so repeated crawls do not overwrite historical lineage:

- `id` UUID primary key;
- `video_asset_id` foreign key;
- `source_site_id` foreign key;
- `crawl_run_id` foreign key;
- `raw_page_id` foreign key;
- `source_page_url`;
- observed `video_url`;
- `discovered_at` timestamp;
- discovery metadata such as source element and declared content type.

`(crawl_run_id, raw_page_id, video_asset_id)` is unique. An asset may therefore be discovered by many runs and raw pages while retaining one stable analysis/content identity. Detail APIs expose the latest discovery and a bounded discovery-history summary.

### 5.3 Description and chunk lineage

For a successful analysis, create or update one `ContentItem`:

- `item_type = "video_description"`;
- `canonical_url = normalized_video_url`;
- `source_url = source_page_url`;
- `cleaned_text = full VLM description`;
- `summary_text = deterministic leading summary or VLM-provided overview`;
- `metadata_json.video_asset_id` and `metadata_json.video_url` preserve typed lineage;
- `content_hash` and `dedup_key` control idempotent updates.

The `VideoAsset.content_item_id` points back to this row. Existing chunking creates `ContentChunk` rows from the description. Existing indexing stores the backend and Qdrant point ID in each PostgreSQL chunk row. The Qdrant payload includes `video_asset_id`, `video_url`, and `source_page_url` in addition to the existing content identifiers.

When the video hash, model, or description-policy version changes, reanalysis updates the content item and reconciles its PostgreSQL chunks in the same transaction. Replacement chunks are created with `embed_status="pending"`; superseded chunks are marked `obsolete` and retain the vector point IDs that must be deleted. Only after that transaction commits does the indexer delete obsolete points and upsert replacement points. Unchanged successful assets are skipped from analysis but still receive a discovery-history row.

## 6. Processing State and Transaction Boundaries

For each run:

1. Validate source/job configuration and load `robots.txt`.
2. Discover allowed pages within depth, page, video, and rate limits.
3. Fetch and persist each raw page.
4. Extract, normalize, validate, and deduplicate video URLs.
5. Upsert stable `VideoAsset` rows, append `VideoAssetDiscovery` rows, and emit discovery events.
6. Claim one asset, validate its URL and media metadata, and set it to `analyzing`.
7. Call and validate the VLM.
8. In one PostgreSQL transaction, persist the successful asset state and content item, reconcile obsolete chunks, and create replacement chunks in `pending` state.
9. After the PostgreSQL commit, delete obsolete Qdrant points, index pending chunks, and update their PostgreSQL vector state in a new transaction.
10. Finalize run counters and status.

A single page, video, or VLM failure does not terminate the run. Network failures use bounded exponential backoff. Deterministic policy, media, or security failures do not retry. PostgreSQL failure occurs before Qdrant writes. A Qdrant failure leaves PostgreSQL chunks marked `failed`, makes the run `partial`, and permits later reindexing.

Events include page discovery, raw persistence, video discovery, media validation, VLM start/success/failure, description validation, chunk creation, vector indexing, and cleanup failures. `AgentCall` records provider, model, latency, status, token/usage data when returned, and sanitized summaries. Secrets, authorization headers, full Base64 content, and credential-bearing URLs are never logged.

## 7. Security and Crawl Safety

Before fetching a page or media URL, the system:

- allows only HTTP and HTTPS;
- enforces configured domains for pages;
- applies an explicit media-host policy for video URLs;
- resolves DNS and rejects loopback, private, link-local, multicast, and reserved targets;
- repeats validation after every redirect;
- limits redirects, response bytes, duration, request time, pages, videos, and crawl depth;
- respects `robots.txt` and configured request intervals;
- rejects URLs containing embedded credentials;
- never executes page scripts or renders untrusted page HTML in the application.

Remote media URLs are never passed to FFmpeg or FFprobe. These tools operate only on local files produced by the controlled downloader. This prevents FFmpeg's internal protocol, redirect, nested-playlist, and segment loading behavior from bypassing application URL validation. DNS validation must be bound to the actual connection target: the safe HTTP transport either connects to the validated IP while preserving TLS hostname verification or verifies the connected peer against the validated resolution set, preventing DNS-rebinding between validation and connection.

Because video CDNs may use a different host from the source site, each source configuration has a separate `allowed_media_domains` list. Auto-discovered cross-domain video hosts are recorded as skipped until explicitly approved.

## 8. API Changes

### 8.1 Control and inspection

- Reuse `POST /jobs/{job_id}/trigger` for manual runs.
- Reuse `GET /runs/{id}` and `GET /runs/{id}/events` for progress and traceability.
- Add `GET /videos` with source, status, media type, and text-query filters.
- Add `GET /videos/{id}` with the asset, full description, content lineage, chunks, and sanitized analysis trace.

Run responses expose six dedicated video counters:

- `video_discovered_count`: discovery-history rows recorded for this run, deduplicated by run, raw page, and asset;
- `video_analyzed_count`: assets that produced a valid persisted description;
- `video_skipped_count`: assets intentionally not analyzed because they were unchanged, unsupported, disallowed, or over a deterministic limit;
- `video_failed_count`: assets whose media fetch, probe, VLM, validation, or PostgreSQL persistence ended in terminal `failed` state;
- `video_chunked_count`: replacement description chunks created by this run;
- `video_indexed_count`: chunks successfully written to the configured vector backend by this run.

These counters do not reuse the existing generic `chunked_count` or `embedded_count`; both generic and video-specific counters remain available and their meanings are tested independently.
Qdrant failures do not change an otherwise successful asset to `failed` and do not increment `video_failed_count`; they mark affected chunks `failed`, increment the generic run error count, and make the run `partial`.

### 8.2 Retrieval

Extend video evidence with:

- `video_asset_id`;
- `video_url`;
- `source_page_url`;
- `full_description`;
- `matched_chunks` containing up to three snippets and chunk IDs;
- overall, vector, and keyword scores.

`POST /search` merges existing vector and keyword retrieval, filters by source/media metadata, groups matches by video asset, and ranks a video by its highest matching chunk score. It returns the full description plus no more than three matched chunks.

`POST /answer` uses the highest-ranked video evidence to synthesize an answer and returns the same supporting evidence. When evidence is insufficient, the endpoint states that the indexed videos do not support an answer rather than inventing one.

Filters add `media_type` while retaining `source_site_id`, item type, language, tags, and publication-time filters.

## 9. Frontend Changes

All frontend work extends the existing React/Vite application and API client.

- Sources/Jobs configuration adds fetch mode, start URLs, allowed page/media domains, patterns, limits, schedule, and description policy controls.
- Runs adds video discovery, VLM, skip/failure, and indexing counters while continuing to show the event timeline.
- A Videos view lists assets and displays source page, direct video link, status, full description, chunks, and model/policy metadata.
- Search displays the synthesized answer, ranked videos, scores, matched chunks, full descriptions, and safe links for opening the media and source page.
- External URLs pass through the existing safe-link utility. Untrusted HTML is never rendered.

## 10. Configuration

Runtime configuration uses environment variables without exposing values:

```dotenv
VLM_API_KEY=
VLM_BASE_URL=https://api.siliconflow.cn/v1
VLM_MODEL=Qwen/Qwen3-Omni-30B-A3B-Instruct
FIRECRAWL_API_KEY=
VIDEO_MAX_BYTES=104857600
VIDEO_MAX_DURATION_SECONDS=900
VIDEO_MAX_HLS_SEGMENTS=512
VIDEO_VLM_TIMEOUT_SECONDS=180
VIDEO_TEMP_DIR=
```

The workspace does not currently contain a project-root `.env`; deployments may inject these values through their runtime environment or create a local untracked `.env`. Startup validation reports missing variable names but never their values.

## 11. Testing Strategy

### 11.1 Unit tests

- Job and description-policy validation.
- Custom media-selector validation and bounded selector execution.
- Path matching, URL resolution/normalization, media-type recognition, and deduplication.
- Robots handling, domain policy, DNS/IP rejection, redirects, byte limits, and duration limits.
- MP4/WebM/M3U8 extraction from HTML.
- VLM request generation and response-policy validation.
- URL-first analysis, controlled HLS download, local-only bounded remux fallback, retry behavior, and temporary-file cleanup.
- DNS-rebinding resistance and validation of every nested playlist, key, segment, and redirect target.
- Asset/discovery-history/content/chunk lineage and idempotent reanalysis.
- Qdrant payload construction and obsolete-vector cleanup.
- Video-level evidence grouping and ranking.

### 11.2 Integration and API tests

- Migration integrity and uniqueness constraints.
- Worker success, partial, skip, and failure runs with fake page, media, VLM, FFmpeg, and vector providers.
- `/videos`, `/search`, and `/answer` response contracts and filters.
- PostgreSQL commit before Qdrant write and failed-index recovery.
- Frontend job configuration, run counters, video detail, search evidence, navigation, loading, empty, and error states.

### 11.3 FlyForum verification

Normal automated tests use committed, sanitized HTML fixtures representing FlyForum forum/thread pages and direct media markup. A live test is opt-in, uses only public unauthenticated pages, observes robots rules and a conservative request interval, caps the run to a small number of pages/videos, and never prints secrets or raw credential-bearing logs.

## 12. Acceptance Criteria

A release is accepted when a bounded FlyForum run can:

1. discover at least one public MP4, WebM, or M3U8 URL from an allowed public thread;
2. preserve the source page as a raw snapshot;
3. persist a deduplicated `VideoAsset` plus an append-only discovery row with complete run/raw-page lineage;
4. obtain a Chinese VLM description satisfying the configured policy;
5. persist the full description in PostgreSQL;
6. create PostgreSQL chunks and corresponding Qdrant points;
7. expose the PostgreSQL-to-Qdrant relationship through chunk IDs and vector point IDs;
8. return the video URL, full description, matched chunks, and scores from `/search`;
9. return a supported synthesized response plus video evidence from `/answer`;
10. show configuration, run progress, video detail, and retrieval evidence in the existing frontend;
11. leave no temporary playlists, segments, or media files after success or failure;
12. avoid authentication bypass, prohibited paths, secret disclosure, DNS rebinding, and private-network fetching;
13. ensure FFmpeg and FFprobe receive local paths only and cannot perform network access;
14. report all six dedicated video counters with defined, non-overlapping semantics.

## 13. Explicit Non-Goals for the First Release

- Logging into websites or handling private content.
- Bypassing CAPTCHA, anti-bot controls, paywalls, or rate limits.
- Resolving protected streams from third-party player pages.
- Downloading or permanently storing video binaries.
- Speech transcription as a separate pipeline.
- Frame-level timestamps in retrieval results.
- A standalone crawler or VLM microservice.
