# OpenClaw + Summarize YouTube 摄取实施计划（中文版）

> **供智能代理执行：** 必须使用 `superpowers:subagent-driven-development`（推荐）或 `superpowers:executing-plans`，按任务逐项实施。所有步骤使用复选框（`- [ ]`）跟踪状态。

**目标：** 建立一条手动触发的摄取链路：由 OpenClaw 搜索公开 YouTube 视频，Summarize 配合本地 whisper.cpp 和自适应关键帧生成中文摘要，再将可追溯内容写入 PostgreSQL、向量写入 Qdrant。

**架构：** 新增聚焦的 `app.services.youtube_ingestion` 包，将纯校验/策略、受限子进程适配、同步持久化仓库和流程编排分开。CLI 只负责解析参数和组装服务；现有 Run、RawPage、ContentItem、ContentChunk 与 Qdrant 继续承担事实、血缘和检索职责。

**技术栈：** Python 3.12、SQLAlchemy 2、PostgreSQL JSONB、Qdrant、OpenClaw CLI、`@steipete/summarize`、yt-dlp、FFmpeg、Tesseract、本地 whisper.cpp、pytest。

---

## 范围与前置条件

以已批准的设计文档 `docs/superpowers/specs/2026-07-07-openclaw-summarize-youtube-ingestion-design.md` 为准。

当前分支存在重复 embedding 配置和两个同名 `build_embedding_service()`。它们会阻断向量写入，因此必须先修复。README 契约和 FlyForum 默认值等既有失败不属于本功能；若目标测试通过后仍存在，应单独记录，不能混入本功能提交。

## 文件规划

新增：

- `backend/app/services/youtube_ingestion/__init__.py`：导出公共接口。
- `backend/app/services/youtube_ingestion/contracts.py`：不可变 DTO 和结果类型。
- `backend/app/services/youtube_ingestion/config.py`：环境变量解析和启动预检。
- `backend/app/services/youtube_ingestion/policy.py`：URL、数量、关键帧、安全路径与脱敏策略。
- `backend/app/services/youtube_ingestion/process.py`：无 shell、有限输出的子进程执行器。
- `backend/app/services/youtube_ingestion/adapters.py`：OpenClaw、yt-dlp 和 Summarize 适配器。
- `backend/app/services/youtube_ingestion/chunks.py`：摘要和时间戳字幕分块。
- `backend/app/services/youtube_ingestion/repository.py`：同步 PostgreSQL 血缘与持久化。
- `backend/app/services/youtube_ingestion/service.py`：端到端编排与清理。
- `scripts/ingest_openclaw_youtube.py`：手动 CLI 入口。
- `tests/unit/test_youtube_ingestion_policy.py`：纯策略测试。
- `tests/unit/test_youtube_ingestion_process.py`：子进程限制测试。
- `tests/unit/test_youtube_ingestion_adapters.py`：外部 JSON 适配测试。
- `tests/unit/test_youtube_ingestion_chunks.py`：视频分块测试。
- `tests/unit/test_ingest_openclaw_youtube_cli.py`：CLI 契约测试。
- `tests/unit/test_openclaw_youtube_docs.py`：文档契约测试。
- `backend/tests/test_youtube_ingestion_repository.py`：数据库血缘与幂等测试。
- `backend/tests/test_youtube_ingestion_service.py`：编排与降级测试。
- `tests/fixtures/openclaw_youtube_search.json`：脱敏后的 OpenClaw 固定输出。
- `tests/fixtures/summarize_youtube.json`：脱敏后的 Summarize 固定输出。

修改：

- `backend/app/core/config.py`：消除重复 embedding 字段并恢复 deterministic 默认值。
- `backend/app/services/embeddings.py`：只保留一个一致的 embedding 工厂。
- `worker/app/chunk_indexer.py`：Qdrant payload 增加视频与时间段元数据。
- `.env.example`：仅在确认其删除并非用户有意操作后，补充非敏感配置项。
- `README.md`：补充手动运行和验证说明。

第一版不修改通用 worker 状态机，不新增 video asset 表。

### 任务 1：恢复 embedding 基线

**文件：**
- 修改：`backend/app/core/config.py`
- 修改：`backend/app/services/embeddings.py`
- 测试：`backend/tests/test_embedding_services.py`
- 测试：`tests/unit/test_embeddings.py`

- [ ] **步骤 1：运行聚焦基线测试并保存失败证据**

```bash
pytest -q backend/tests/test_embedding_services.py tests/unit/test_embeddings.py
```

预期：能看到后定义的重复工厂拒绝 `sentence-transformers` 或 `deterministic`，默认 provider 也不是 deterministic。

- [ ] **步骤 2：删除重复的 Settings 字段**

`Settings` 中只保留：

```python
embedding_provider: str = Field(default="deterministic", alias="EMBEDDING_PROVIDER")
embedding_model: str = Field(default="deterministic-hash-v1", alias="EMBEDDING_MODEL")
embedding_dimension: int = Field(default=384, alias="EMBEDDING_DIMENSION")
```

保留现有 VLM 字段，但后文不得再次声明 `embedding_provider` 或 `embedding_model`。

- [ ] **步骤 3：只保留一个 embedding 工厂**

删除错误缩进的 SiliconFlow 片段和第二个同名工厂，最终工厂为：

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

- [ ] **步骤 4：重新运行聚焦测试**

```bash
pytest -q backend/tests/test_embedding_services.py tests/unit/test_embeddings.py
```

预期：安装本地 embedding 可选依赖时全部通过。若明确不安装，排除三个真实模型集成测试后，其余选择项必须全部通过。

- [ ] **步骤 5：提交基线修复**

```bash
git add backend/app/core/config.py backend/app/services/embeddings.py
git commit -m "fix: restore embedding provider factory"
```

### 任务 2：定义摄取契约与纯策略

**文件：**
- 新增：`backend/app/services/youtube_ingestion/__init__.py`
- 新增：`backend/app/services/youtube_ingestion/contracts.py`
- 新增：`backend/app/services/youtube_ingestion/policy.py`
- 新增：`tests/unit/test_youtube_ingestion_policy.py`

- [ ] **步骤 1：先写失败的策略测试**

覆盖 watch、短链和 Shorts 规范化，非法域名、候选去重、五档关键帧上限、路径逃逸及脱敏：

```python
def test_normalizes_youtube_urls() -> None:
    expected = "https://www.youtube.com/watch?v=fAZZLPwbPyg"
    assert normalize_youtube_url("https://youtu.be/fAZZLPwbPyg?t=30") == (
        expected,
        "fAZZLPwbPyg",
    )
    assert normalize_youtube_url("https://youtube.com/shorts/fAZZLPwbPyg") == (
        expected,
        "fAZZLPwbPyg",
    )


@pytest.mark.parametrize(
    ("seconds", "expected"),
    [(300, 6), (301, 10), (901, 16), (1801, 24), (3601, 32)],
)
def test_adaptive_slide_caps(seconds: int, expected: int) -> None:
    assert slides_max_for_duration(seconds) == expected


def test_slide_path_cannot_escape_root(tmp_path: Path) -> None:
    root = tmp_path / "slides"
    with pytest.raises(ValueError, match="unsafe slide path"):
        resolve_slide_path(root, "fAZZLPwbPyg", "../../secret")
```

- [ ] **步骤 2：确认测试先失败**

```bash
pytest -q tests/unit/test_youtube_ingestion_policy.py
```

预期：包尚不存在，导入失败。

- [ ] **步骤 3：增加不可变数据契约**

在 `contracts.py` 定义：

```python
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
class VideoMetadata:
    video_id: str
    title: str
    duration_seconds: int

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
```

同时定义 `CommandResult` 与 `IngestionResult`，字段与英文计划一致。

- [ ] **步骤 4：实现纯策略函数**

只允许 11 字符 video ID；域名 allowlist 为 `youtube.com`、`www.youtube.com`、`m.youtube.com`、`youtu.be`；按 rank 稳定排序并限制 `1 <= limit <= 5`。路径用 `resolve()` 与 `is_relative_to()` 校验。脱敏 Bearer token、配置密钥及 `sig/signature/token/key/expire/expires` 查询参数。

- [ ] **步骤 5：测试并提交**

```bash
pytest -q tests/unit/test_youtube_ingestion_policy.py
git add backend/app/services/youtube_ingestion tests/unit/test_youtube_ingestion_policy.py
git commit -m "feat: add YouTube ingestion policy"
```

### 任务 3：配置预检与受限子进程

**文件：**
- 新增：`backend/app/services/youtube_ingestion/config.py`
- 新增：`backend/app/services/youtube_ingestion/process.py`
- 新增：`tests/unit/test_youtube_ingestion_process.py`

- [ ] **步骤 1：编写配置与子进程失败测试**

测试缺失 whisper 模型、输出超限和超时：

```python
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

- [ ] **步骤 2：确认测试先失败**

```bash
pytest -q tests/unit/test_youtube_ingestion_process.py
```

- [ ] **步骤 3：实现 `YoutubeIngestionConfig.from_env()`**

配置字段包括 OpenClaw/Summarize/yt-dlp 路径与超时、SiliconFlow endpoint/key/model、whisper-cli 与模型路径、slides 根目录、OCR 开关和输出上限。默认 OpenClaw 120 秒、Summarize 1200 秒、16 MiB 输出上限；所有可执行程序用 `shutil.which()` 验证，whisper 模型必须是可读文件。

- [ ] **步骤 4：实现无 shell 的受限执行器**

使用 `subprocess.Popen(..., shell=False, stdout=PIPE, stderr=PIPE)` 和 `selectors.DefaultSelector` 增量读取。用 `time.monotonic()` 控制 wall-clock timeout；总输出超限或超时时终止进程组、排空管道并抛出脱敏的 `CommandFailed`。不得打印子进程环境。

- [ ] **步骤 5：测试并提交**

```bash
pytest -q tests/unit/test_youtube_ingestion_process.py
git add backend/app/services/youtube_ingestion/config.py \
  backend/app/services/youtube_ingestion/process.py \
  tests/unit/test_youtube_ingestion_process.py
git commit -m "feat: add bounded ingestion subprocess runner"
```

### 任务 4：实现 OpenClaw、yt-dlp 与 Summarize 适配器

**文件：**
- 新增：`backend/app/services/youtube_ingestion/adapters.py`
- 新增：`tests/unit/test_youtube_ingestion_adapters.py`
- 新增：`tests/fixtures/openclaw_youtube_search.json`
- 新增：`tests/fixtures/summarize_youtube.json`

- [ ] **步骤 1：建立脱敏固定输出 fixture**

OpenClaw fixture 必须包含一个 `message` 字符串，其内部是带 `candidates` 的 JSON；Summarize fixture 必须包含 summary、title、带时间戳 transcript、duration、video ID、transcript source、slides 和 model。

- [ ] **步骤 2：先写失败的 adapter 测试**

测试准确 argv、嵌套 JSON、video ID 不一致、yt-dlp duration、Summarize 字段映射、空摘要、caption/whisper 来源、视觉能力探测和 slide 路径：

```python
def test_metadata_probe_supplies_duration_before_summarize(fake_runner) -> None:
    metadata = YtDlpMetadataAdapter(config(), runner=fake_runner).probe(
        "https://www.youtube.com/watch?v=fAZZLPwbPyg"
    )
    assert metadata.video_id == "fAZZLPwbPyg"
    assert metadata.duration_seconds == 600

def test_vision_mode_requires_successful_probe(fake_http_client) -> None:
    adapter = SummarizeYoutubeAdapter(config(), http_client=fake_http_client)
    assert adapter.probe_vision_support() is False
```

- [ ] **步骤 3：确认测试先失败**

```bash
pytest -q tests/unit/test_youtube_ingestion_adapters.py
```

- [ ] **步骤 4：实现 OpenClaw 搜索 adapter**

argv：

```python
[
    config.openclaw_path, "agent", "--agent", config.openclaw_agent,
    "--message", build_search_prompt(query, limit), "--json",
]
```

prompt 强制返回一个无 Markdown fence 的 `candidates` JSON。解析外层 CLI JSON 后，只允许从顶层 `candidates` 或 `message/content/text` 字符串中读取候选。每条都走 `normalize_youtube_url()`，声明 ID 与 URL ID 不一致时拒绝。

- [ ] **步骤 5：实现 yt-dlp metadata probe**

```python
[
    config.yt_dlp_path,
    "--dump-single-json",
    "--skip-download",
    "--no-playlist",
    canonical_url,
]
```

要求 `id` 与 canonical URL 一致，duration 是正整数；返回 `VideoMetadata`。必须在 Summarize 前完成，以便计算自适应 `slides_max`。

- [ ] **步骤 6：实现 Summarize adapter**

核心 argv：

```python
[
    config.summarize_path, canonical_url,
    "--youtube", "auto",
    "--language", "zh",
    "--length", "medium",
    "--force-summary",
    "--slides", "--slides-max", str(slides_max),
    "--slides-dir", str(staging_slides_dir),
    "--json", "--timeout", f"{int(config.summarize_timeout_seconds)}s",
]
```

OCR 启用时追加 `--slides-ocr`。只在子进程环境映射：

```python
child_env["OPENAI_API_KEY"] = config.siliconflow_api_key
child_env["OPENAI_BASE_URL"] = config.siliconflow_base_url
child_env["SUMMARIZE_MODEL"] = f"openai/{config.siliconflow_summary_model}"
child_env["SUMMARIZE_WHISPER_CPP_BINARY"] = config.whisper_cpp_binary
child_env["SUMMARIZE_WHISPER_CPP_MODEL_PATH"] = str(config.whisper_cpp_model_path)
```

完整保留解析后的 JSON；title、duration、transcript、video ID 或 summary 缺失时抛 `AdapterSchemaError`。不得用简介冒充字幕。slide 必须校验路径、计算 SHA-256，再从 staging 移入最终目录。

- [ ] **步骤 7：实现真实视觉能力探测**

向 SiliconFlow Chat Completions 发送一次缓存的 1x1 PNG data URL 探测。只有响应成功且 assistant 文本非空时返回 true；明确的媒体不支持 4xx 返回 false；网络错误应导致预检失败。错误中不能包含 key 或完整响应体。

- [ ] **步骤 8：测试并提交**

```bash
pytest -q tests/unit/test_youtube_ingestion_adapters.py
git add backend/app/services/youtube_ingestion/adapters.py \
  tests/unit/test_youtube_ingestion_adapters.py tests/fixtures
git commit -m "feat: add OpenClaw and Summarize adapters"
```

### 任务 5：生成摘要与时间戳字幕 chunks

**文件：**
- 新增：`backend/app/services/youtube_ingestion/chunks.py`
- 新增：`tests/unit/test_youtube_ingestion_chunks.py`
- 修改：`worker/app/chunk_indexer.py`
- 测试：`worker/tests/test_worker_runner.py`

- [ ] **步骤 1：先写失败的分块测试**

```python
def test_builds_summary_then_timestamped_transcript_chunks() -> None:
    chunks = build_video_chunks(
        title="DJI test",
        summary="中文摘要",
        transcript="[00:00] 开始介绍。\n[00:30] 展示升级界面。\n[02:00] 完成。",
        max_chars=40,
    )
    assert chunks[0].chunk_type == "summary"
    assert chunks[1].chunk_type == "transcript"
    assert chunks[1].chunk_metadata_json["start_seconds"] == 0
    assert chunks[-1].chunk_metadata_json["end_seconds"] == 120
```

- [ ] **步骤 2：确认测试先失败**

```bash
pytest -q tests/unit/test_youtube_ingestion_chunks.py
```

- [ ] **步骤 3：实现视频专用 builder**

复用 `BuiltChunk` 与现有 hash/version 约定。解析 `[HH:MM:SS]` 和 `[MM:SS]`，相邻字幕累积到 `max_chars`，不得把时间戳与正文拆开。chunk 0 为 summary，后续为 transcript；metadata 保存 `chunk_type/start_seconds/end_seconds/offset_basis/chunker_version/embed_text_hash`。

- [ ] **步骤 4：扩展 Qdrant payload**

从 PostgreSQL 权威字段加入：

```python
{
    "youtube_video_id": _metadata_string(metadata, "youtube_video_id"),
    "video_url": content_item.canonical_url,
    "summary_input_mode": _metadata_string(metadata, "summary_input_mode"),
    "chunk_type": chunk.chunk_metadata_json.get("chunk_type"),
    "start_seconds": chunk.chunk_metadata_json.get("start_seconds"),
    "end_seconds": chunk.chunk_metadata_json.get("end_seconds"),
}
```

测试 payload 不得包含签名媒体 URL。

- [ ] **步骤 5：测试并提交**

```bash
pytest -q tests/unit/test_youtube_ingestion_chunks.py \
  worker/tests/test_worker_runner.py -k 'payload or chunk'
git add backend/app/services/youtube_ingestion/chunks.py \
  tests/unit/test_youtube_ingestion_chunks.py \
  worker/app/chunk_indexer.py worker/tests/test_worker_runner.py
git commit -m "feat: add timestamped YouTube chunks"
```

### 任务 6：实现 PostgreSQL 血缘和幂等仓库

**文件：**
- 新增：`backend/app/services/youtube_ingestion/repository.py`
- 新增：`backend/tests/test_youtube_ingestion_repository.py`

- [ ] **步骤 1：先写失败的数据库测试**

```python
async def test_pending_raw_page_commits_before_external_extraction(sync_repository) -> None:
    context = sync_repository.start_run(query="DJI 固件", candidate_limit=5, video_limit=3)
    raw_page_id = sync_repository.create_pending_video_raw_page(
        context=context,
        canonical_url="https://www.youtube.com/watch?v=fAZZLPwbPyg",
    )
    assert sync_repository.read_raw_page(raw_page_id).parse_status == "pending"
```

另测成功 video ID 跨 Run 去重。测试可使用指向 `TEST_DATABASE_URL` 的同步 engine，但不得跨线程或子进程共享 Session。

- [ ] **步骤 2：确认测试先失败**

```bash
pytest -q backend/tests/test_youtube_ingestion_repository.py
```

- [ ] **步骤 3：实现 Source、Job、Run 与搜索快照**

复用一个稳定 YouTube Source（公开域名 allowlist）和一个 manual Job。`start_run()` 保存脱敏配置快照及 URL 编码搜索页；`persist_search_snapshot()` 必须在处理候选前写入 OpenClaw JSON 和 `search_completed` 事件。

- [ ] **步骤 4：实现 pending RawPage 与失败状态**

`create_pending_video_raw_page()` 必须用短事务提交 pending 记录后才返回 UUID。成功时更新 transcript、脱敏 raw JSON、提取方法、confidence 和 body hash；失败时提交 `parse_status=failed` 与脱敏错误。

- [ ] **步骤 5：实现 ContentItem 与 chunk 持久化**

核心映射：`item_type=video_description`，canonical/source URL 都是稳定 watch URL，`raw_text` 是完整字幕，`cleaned_text` 是摘要加字幕，`summary_text` 是纯中文摘要；dedup key 为 `<source_id>:youtube:<video_id>:video_description`。从 `build_video_chunks()` 创建 chunks，并与 ContentItem 一起提交。唯一键竞争时重新加载成功项并返回 dedupe。

- [ ] **步骤 6：实现计数器和最终状态**

无候选且无工具错误为 success；所有尝试均失败为 failed；成功/失败混合或向量失败为 partial；其余为 success。所有 counters 从已保存事实计算。

- [ ] **步骤 7：测试并提交**

```bash
pytest -q backend/tests/test_youtube_ingestion_repository.py
git add backend/app/services/youtube_ingestion/repository.py \
  backend/tests/test_youtube_ingestion_repository.py
git commit -m "feat: persist YouTube ingestion lineage"
```

### 任务 7：编排搜索、抽取、持久化、清理和索引

**文件：**
- 新增：`backend/app/services/youtube_ingestion/service.py`
- 新增：`backend/tests/test_youtube_ingestion_service.py`
- 修改：`backend/app/services/youtube_ingestion/__init__.py`

- [ ] **步骤 1：使用 fake 编写失败的编排测试**

覆盖全成功、全去重、无候选、搜索 JSON 非法、单视频失败、caption、whisper fallback、抽帧降级、摘要失败、Qdrant 失败与资源清理。

```python
def test_processes_only_top_three_new_candidates(service_factory) -> None:
    service, search, summarize, repo, indexer = service_factory(candidate_count=5)
    result = service.run(query="DJI 固件", candidate_limit=5, video_limit=3)
    assert len(summarize.calls) == 3
    assert result.succeeded_count == 3

def test_one_video_failure_yields_partial(service_factory) -> None:
    service, search, summarize, repo, indexer = service_factory(
        candidate_count=3,
        summarize_failures={"video-id-2"},
    )
    result = service.run(query="DJI 固件", candidate_limit=5, video_limit=3)
    assert result.status == "partial"
    assert result.succeeded_count == 2
    assert result.failed_count == 1
```

- [ ] **步骤 2：确认测试先失败**

```bash
pytest -q backend/tests/test_youtube_ingestion_service.py
```

- [ ] **步骤 3：按明确阶段实现 `run()`**

顺序必须是：校验 5/3 硬限制；创建 Run；搜索并保存原始结果；规范化/排序/去重；查询已有成功 ID；在每次 Summarize 前提交 pending RawPage；yt-dlp 探测 duration；计算 slides cap；抽取并保存 RawPage、ContentItem、chunks；根据真实探测选择 input mode；索引已提交 chunks；记录向量失败；`finally` 清理 staging；汇总 counters/status。

每条视频单独 `try/except/finally`。只捕获预期 adapter/DB/vector 异常；`KeyboardInterrupt` 和 `SystemExit` 清理后继续抛出。

- [ ] **步骤 4：新增已持久化 chunk 的聚焦索引函数**

不能再次调用通用 chunk builder。加载 pending chunks，ensure collection，逐条 upsert 已保存的 `embed_text`，提交 success/failed。payload 只能来自 PostgreSQL 中的 ContentItem/ContentChunk。

- [ ] **步骤 5：测试并提交**

```bash
pytest -q backend/tests/test_youtube_ingestion_service.py \
  backend/tests/test_youtube_ingestion_repository.py \
  tests/unit/test_youtube_ingestion_policy.py \
  tests/unit/test_youtube_ingestion_adapters.py \
  tests/unit/test_youtube_ingestion_chunks.py
git add backend/app/services/youtube_ingestion backend/tests/test_youtube_ingestion_service.py
git commit -m "feat: orchestrate YouTube search ingestion"
```

### 任务 8：新增手动 CLI 与 JSON 契约

**文件：**
- 新增：`scripts/ingest_openclaw_youtube.py`
- 新增：`tests/unit/test_ingest_openclaw_youtube_cli.py`

- [ ] **步骤 1：先写失败的 CLI 测试**

```python
def test_parse_args_defaults() -> None:
    args = load_script().parse_args(["--query", "DJI 固件"])
    assert args.candidate_limit == 5
    assert args.video_limit == 3

def test_parse_args_rejects_limits_above_hard_caps() -> None:
    with pytest.raises(SystemExit):
        load_script().parse_args(["--query", "DJI", "--candidate-limit", "6"])
```

- [ ] **步骤 2：确认测试先失败**

```bash
pytest -q tests/unit/test_ingest_openclaw_youtube_cli.py
```

- [ ] **步骤 3：实现薄 CLI**

公开 `parse_args(argv)`、`build_service()` 和 `main(argv)`。用 argparse 限制参数，调用 `YoutubeIngestionConfig.from_env()` 并组装服务。退出码：success=0、partial=2、failed/preflight=1。`--json` 在 stdout 只输出一个 JSON，日志全部写 stderr，禁止打印环境变量。

- [ ] **步骤 4：测试并提交**

```bash
pytest -q tests/unit/test_ingest_openclaw_youtube_cli.py
git add scripts/ingest_openclaw_youtube.py tests/unit/test_ingest_openclaw_youtube_cli.py
git commit -m "feat: add manual YouTube ingestion CLI"
```

### 任务 9：补充配置与运行验证文档

**文件：**
- 修改：`.env.example`（仅在确认允许后）
- 修改：`README.md`
- 新增：`tests/unit/test_openclaw_youtube_docs.py`

- [ ] **步骤 1：先确认 `.env.example` 的用户状态**

```bash
git status --short .env.example
git diff -- .env.example
git diff --cached -- .env.example
```

若删除是用户有意操作，不得恢复，只在 README 写配置；若确定是误删并获准恢复，再编辑该文件。

- [ ] **步骤 2：编写文档契约测试**

断言 README 包含准确命令、硬限制、仅公开内容、禁止 Cookie、PostgreSQL 验证和空格分隔的中文搜索请求。若 `.env.example` 存在，断言只有变量名，没有非空 secret。

- [ ] **步骤 3：增加运维说明**

```bash
python scripts/ingest_openclaw_youtube.py \
  --query "DJI 飞控 固件" \
  --candidate-limit 5 \
  --video-limit 3 \
  --json
```

列出 Node 24+、Summarize、OpenClaw、yt-dlp、FFmpeg、Tesseract、whisper-cli/model、PostgreSQL/Qdrant 与 SiliconFlow 的检查方式。明确禁止浏览器 Cookie、登录视频、限制绕过和签名 URL 持久化。

- [ ] **步骤 4：增加 Run、内容与检索验证命令**

```bash
curl -fsS "http://127.0.0.1:8000/runs/$RUN_ID" | python -m json.tool
curl -fsS "http://127.0.0.1:8000/contents?source_site_id=$SOURCE_ID&limit=10" | python -m json.tool
curl -fsS -X POST http://127.0.0.1:8000/search \
  -H 'Content-Type: application/json' \
  -d "{\"query\":\"DJI 飞控 固件\",\"mode\":\"search\",\"filters\":{\"source_site_id\":\"$SOURCE_ID\",\"item_type\":\"video_description\"},\"top_k\":3}" \
  | python -m json.tool
```

- [ ] **步骤 5：测试并提交**

```bash
pytest -q tests/unit/test_openclaw_youtube_docs.py
git add README.md tests/unit/test_openclaw_youtube_docs.py
test ! -f .env.example || git add .env.example
git commit -m "docs: add YouTube ingestion operations guide"
```

### 任务 10：最终验证与单视频 live smoke

**文件：**
- 仅测试；除非测试定位到缺陷，否则不修改生产代码。

- [ ] **步骤 1：运行 lint 与类型检查**

```bash
ruff check backend/app/services/youtube_ingestion scripts/ingest_openclaw_youtube.py \
  tests/unit/test_youtube_ingestion_*.py backend/tests/test_youtube_ingestion_*.py
mypy backend/app/services/youtube_ingestion
```

预期：两个命令退出码均为 0。

- [ ] **步骤 2：运行完整功能测试集**

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

预期：全部通过。

- [ ] **步骤 3：运行全量回归**

```bash
pytest -q
```

预期：本功能没有引入新失败。若 README/FlyForum 等既有失败仍存在，必须与基线比较并明确报告，不能宣称全套通过。

- [ ] **步骤 4：执行受限的单公开视频 live smoke**

```bash
docker compose up -d postgres qdrant
alembic upgrade head
python scripts/ingest_openclaw_youtube.py \
  --query "DJI 飞控 固件" \
  --candidate-limit 1 \
  --video-limit 1 \
  --json | tee /tmp/openclaw-youtube-smoke.json
```

预期：stdout 只有一个 JSON，不泄露凭据；结果为 success，或给出明确归因的公开来源/工具失败。禁止为了成功加入 Cookie 或绕过措施。

- [ ] **步骤 5：验证数据库与 Evidence**

使用 `jq` 提取 Run/Source ID，执行文档中的三个请求，并确认：稳定 watch URL；字幕和中文摘要非空；input mode 与真实输入一致；slides cap 自适应且路径安全；至少一个 summary chunk，存在字幕时至少一个 transcript chunk；Qdrant 成功或 chunk 明确标记 failed；任何 JSON/日志均无 API Key、Cookie、浏览器 Profile、签名 CDN URL 或本地绝对路径。

- [ ] **步骤 6：仅在验证确实修复了代码时提交**

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

预期：提交中不包含任何无关用户改动。
