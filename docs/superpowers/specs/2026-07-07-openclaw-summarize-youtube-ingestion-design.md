# OpenClaw + Summarize YouTube 自动检索入库设计

## 1. 目标

为现有 Intelligence RAG 项目增加一个手动触发的 YouTube 视频摄取闭环：用户提交中文搜索词后，OpenClaw 搜索公开 YouTube 视频，系统筛选候选并调用 Summarize 提取字幕或执行本地转录、抽取关键帧和 OCR、通过 SiliconFlow 生成中文摘要，最后将原始证据、规范化内容和检索向量写入 PostgreSQL 与 Qdrant。

第一版的默认边界是：每次搜索最多接收 5 个候选，按搜索排名最多处理 3 个尚未成功入库的视频。PostgreSQL 是事实来源，Qdrant 只保存可重建的 chunk 向量与检索 payload。

## 2. 已确认的产品决策

- 搜索平台仅限公开 YouTube 视频。
- 任务由用户手动提交搜索词触发，不实现定时调度。
- OpenClaw 最多返回 5 个候选，系统最多处理其中 3 个新视频。
- YouTube video ID 是跨运行的稳定去重身份；已成功入库的视频跳过重复媒体处理。
- OpenClaw 只负责搜索候选，不负责摘要或入库。
- Summarize 负责单条视频的字幕、转录、关键帧、OCR 和摘要处理。
- Summarize 直接调用现有 SiliconFlow API，不把 OpenClaw 作为摘要模型后端。
- 无公开字幕时使用本地 `whisper.cpp`，不增加云端转录供应商。
- 关键帧存储在项目受控目录，数据库保存相对路径及元数据，不保存图片二进制。
- 中文摘要保存到 PostgreSQL；完整字幕、原始工具输出和关键帧元数据也必须保留。
- 第一版使用独立 Python 导入脚本，不扩展通用 worker 的调度职责。

## 3. 方案选择

### 3.1 采用：独立导入脚本

新增 `scripts/ingest_openclaw_youtube.py`，由它协调 OpenClaw、Summarize、PostgreSQL 和 Qdrant。该路径贴合现有 FlyForum 与 YouTube demo 脚本，便于手动运行、隔离测试和观察失败阶段，并避免在第一版扩大通用 worker 的职责。

建议入口：

```bash
python scripts/ingest_openclaw_youtube.py \
  --query "DJI 飞控固件" \
  --candidate-limit 5 \
  --video-limit 3 \
  --json
```

命令行必须拒绝空搜索词、非正整数限制，以及超过硬上限 5/3 的限制。硬上限不能通过环境变量或普通命令行参数放大。

### 3.2 未采用的方案

- **扩展通用 worker**：长期架构更统一，但第一版会同时改变调度、adapter、worker 状态机和视频处理，风险与验证面过大。
- **OpenClaw 主导全流程**：搭建更快，但幂等、事务、原始证据和错误恢复容易散落在 agent prompt 中，无法形成稳定、可测试的摄取边界。

## 4. 系统边界与职责

### 4.1 OpenClaw 搜索器

专用搜索 agent 接收搜索词和候选上限，只返回结构化 JSON。每个候选至少包含：

- `url`：YouTube 页面 URL；
- `video_id`：声明的 YouTube video ID；
- `title`：搜索结果标题；
- `snippet`：搜索结果摘要；
- `rank`：从 1 开始的结果顺序；
- `selection_reason`：入选理由。

编排层不得信任 agent 声明的 `video_id`，必须从 URL 重新解析并比对。agent 的自由文本、工具 trace 和搜索结果都属于不可信外部输入。

### 4.2 Python 编排层

编排层负责：

- 创建或复用 Source 与 Job，并为每次手动请求创建 Run；
- 受控启动 OpenClaw 与 Summarize 子进程；
- 校验 JSON schema、URL allowlist、video ID 和数量上限；
- 规范化 URL、跨候选去重和查询数据库幂等状态；
- 计算关键帧策略；
- 保存搜索和媒体处理原始证据；
- 将 Summarize 输出映射到现有内容模型；
- 管理逐视频事务、Run counters、事件和最终状态；
- 在 PostgreSQL 提交后写入 Qdrant。

### 4.3 Summarize

Summarize 对单条规范化 YouTube URL 执行 transcript-first 流程：

1. 优先读取公开 YouTube 字幕；
2. 无字幕时通过 `yt-dlp` 获取音频并调用本地 `whisper.cpp`；
3. 使用场景检测抽取关键帧；
4. 可用时执行 Tesseract OCR；
5. 通过 SiliconFlow OpenAI-compatible API 生成中文摘要；
6. 输出机器可解析 JSON。

编排层必须固定 `--json`、中文输出、超时和每条视频的自适应 `--slides-max`。不得通过 shell 拼接未转义的搜索词或 URL。

### 4.4 RAG 持久化层

PostgreSQL 保存搜索快照、Summarize 原始响应、字幕、摘要、内容血缘、关键帧元数据、chunks 与索引状态。Qdrant 保存 chunk 向量及引用 PostgreSQL 身份的 payload。任何 Qdrant 数据都必须能从 PostgreSQL 重建。

## 5. 端到端处理流程

1. 校验本地配置和所有外部可执行程序。
2. 创建或复用 YouTube Source 与手动 Job，创建状态为 `running` 的 Run。
3. 调用 OpenClaw 搜索 agent，并持久化脱敏后的完整搜索 JSON。
4. 严格解析候选，只接受 `youtube.com/watch` 与 `youtu.be` 的公开 HTTP(S) URL。
5. 将地址规范化为 `https://www.youtube.com/watch?v=<video_id>`，去除播放列表、时间戳和跟踪参数。
6. 按 rank 稳定排序，对相同 video ID 去重，最多保留 5 个候选。
7. 查询 PostgreSQL；成功内容计入 `deduped_count`，失败或部分完成的内容允许重试。
8. 从剩余候选中最多选择前 3 个，为每个视频创建并提交 `pending` RawPage，建立先于媒体抽取的稳定血缘。
9. 读取视频元数据和时长、计算关键帧上限，再调用 Summarize；完成后更新同一 RawPage，写入脱敏原始 JSON、完整字幕和处理状态，并保存关键帧及 OCR。
10. 验证中文摘要非空，规范化为 ContentItem 和 ContentChunk。
11. 逐视频提交 PostgreSQL 事实数据；提交成功后写 Qdrant。
12. 记录成功、降级、跳过和失败事件，更新 Run counters。
13. 全部成功或去重时 Run 为 `success`；成功与失败并存时为 `partial`；没有任何视频成功且存在错误时为 `failed`。
14. 输出 `run_id`、`source_id`、候选数、处理数、去重数、成功数、失败数和 chunk/vector 计数。

## 6. 搜索校验与去重

允许的稳定页面形式只有：

- `https://www.youtube.com/watch?v=<11-character-id>`；
- `https://youtu.be/<11-character-id>`，入库前转换为上一种形式。

不接受频道页、搜索页、播放列表页、Shorts 以外的未定义页面类型、非 HTTP(S) scheme 或非 allowlist 域名。若需要支持 Shorts，必须明确将 `/shorts/<id>` 转换为稳定 watch URL 后再进入现有流程。

候选内按 video ID 去重；跨运行的 ContentItem 去重键为：

```text
<source_site_id>:youtube:<video_id>:video_description
```

已成功入库的视频不再次调用 Summarize，但当前 Run 仍保存搜索快照和 `deduped` 事件。之前失败或未生成完整 ContentItem 的视频允许重试。

## 7. 自适应关键帧策略

关键帧数量同时考虑视频时长与场景变化。编排层根据时长设置 `--slides-max`：

| 视频时长 | 关键帧上限 |
|---|---:|
| 不超过 5 分钟 | 6 |
| 5–15 分钟 | 10 |
| 15–30 分钟 | 16 |
| 30–60 分钟 | 24 |
| 超过 60 分钟 | 32 |

32 是不可通过普通配置放大的硬上限。Summarize 的场景检测决定实际帧数，编排层再以内容哈希过滤完全重复帧；近似去重由 Summarize 的自动阈值负责，第一版不增加新的图像相似度模型。最终帧数可以低于上限，系统不得用重复帧凑数。

关键帧保存在：

```text
data/video_slides/<youtube_video_id>/
```

数据库只保存项目根目录下的相对路径、时间戳、SHA-256 和 OCR。解析后的绝对路径必须仍位于配置的 slides 根目录内，防止路径逃逸。

摘要输入模式必须记录为以下之一：

- `transcript_only`：只有字幕或转录；
- `transcript_ocr`：字幕或转录加关键帧 OCR；
- `transcript_vision`：字幕或转录加实际图片输入。

第一版必须保证 `transcript_ocr` 可用。只有预检和一次受控探测确认当前 SiliconFlow 模型及 Summarize 调用链支持图片附件时，才允许标记 `transcript_vision`。保存了图片不等于模型看过图片。

## 8. 数据模型映射

### 8.1 搜索 RawPage

每次 Run 保存一条搜索快照：

- `requested_url`：带编码查询词的稳定 YouTube 搜索页 URL；
- `raw_json`：脱敏后的 OpenClaw 结构化输出；
- `parser_profile`：`openclaw-youtube-search-v1`；
- `extraction_method`：`openclaw_search`；
- `parse_status`：schema 与候选校验结果。

即使候选全部去重，也必须保留该搜索快照。

### 8.2 视频 RawPage

每次实际媒体处理保存一条 RawPage。编排层必须先以 `parse_status=pending` 创建并提交该记录，再调用 Summarize；Summarize 完成或失败后更新同一记录：

- `requested_url`、`final_url`：规范化 YouTube watch URL；
- `raw_text`：完整带时间戳字幕或 `whisper.cpp` 转录；
- `raw_json`：脱敏后的 Summarize 完整 JSON 与处理诊断；
- `parser_profile`：`summarize-youtube-v1`；
- `extraction_method`：`youtube_caption` 或 `whisper_cpp`；
- `body_hash`：基于规范化原始输出计算的 SHA-256；
- `parse_status`、`parse_error`：规范化结果。

Summarize 原始响应必须先写回 RawPage，再创建 ContentItem。摘要失败时仍保留预先创建的 RawPage 与脱敏失败信息，但不创建半成品 ContentItem。

### 8.3 ContentItem

```text
item_type       = video_description
canonical_url   = https://www.youtube.com/watch?v=<video_id>
source_url      = canonical_url
title           = YouTube 标题
raw_text        = 完整字幕或转录
cleaned_text    = 结构化中文摘要 + 字幕正文
summary_text    = 纯中文摘要
language        = zh
structured_by   = summarize-youtube-v1
```

`metadata_json` 至少包含：

```json
{
  "platform": "youtube",
  "youtube_video_id": "...",
  "duration_seconds": 1200,
  "search_query": "DJI 飞控固件",
  "search_rank": 1,
  "transcript_source": "caption",
  "summary_provider": "siliconflow",
  "summary_model": "configured-model-id",
  "summary_input_mode": "transcript_ocr",
  "slides_max": 16,
  "slides": [
    {
      "timestamp_seconds": 123.4,
      "relative_path": "data/video_slides/video-id/slide-003.jpg",
      "sha256": "...",
      "ocr_text": "..."
    }
  ]
}
```

不持久化签名 CDN URL、Cookie、鉴权头、API Key、浏览器 Profile 或本地绝对路径。

### 8.4 ContentChunk 与 Qdrant

- chunk 0 保存标题和完整中文摘要，`chunk_type=summary`；
- 后续 chunks 按字幕时间段分块，`chunk_type=transcript`；
- transcript chunk metadata 保存 `start_seconds` 与 `end_seconds`；
- `display_text` 保留用户可读的时间戳文本；
- `embed_text` 包含标题、必要上下文和当前正文；
- Qdrant payload 保存 content item ID、video ID、canonical URL、chunk type 与时间范围。

检索结果始终返回稳定 YouTube 页面 URL，不返回临时音视频 URL。

## 9. 配置与启动预检

建议配置：

```dotenv
OPENCLAW_PATH=openclaw
OPENCLAW_SEARCH_AGENT=youtube-searcher
OPENCLAW_SEARCH_TIMEOUT_SECONDS=120

SUMMARIZE_PATH=summarize
SUMMARIZE_TIMEOUT_SECONDS=1200
SUMMARIZE_CANDIDATE_LIMIT=5
SUMMARIZE_VIDEO_LIMIT=3

SILICONFLOW_API_KEY=
SILICONFLOW_BASE_URL=https://api.siliconflow.cn/v1
SILICONFLOW_SUMMARY_MODEL=

WHISPER_CPP_BINARY=whisper-cli
WHISPER_CPP_MODEL_PATH=

VIDEO_SLIDES_DIR=data/video_slides
VIDEO_SLIDES_OCR=true
VIDEO_SLIDES_HARD_MAX=32
```

示例中留空的值是部署时必填的本地配置，不得获得隐式运行时默认值。缺少这些必填值时，预检必须在创建媒体处理子进程前给出清晰错误。

编排层将 SiliconFlow 配置仅映射到 Summarize 子进程所需的 OpenAI-compatible 环境变量。密钥通过子进程环境传递，不进入 argv。具体变量名由锁定的 Summarize 版本集成测试确定；适配层对调用方暴露稳定的 `SiliconFlowSummaryConfig`，避免业务逻辑依赖第三方 CLI 的环境变量细节。

启动预检必须验证：

- `openclaw`、`summarize`、`yt-dlp`、FFmpeg、`whisper-cli` 与 Tesseract 可执行；
- `whisper.cpp` 模型文件存在且可读；
- SiliconFlow endpoint、模型名与密钥已配置；
- PostgreSQL 可连接；
- Qdrant 配置可解析；
- slides 目录可创建且可写；
- 摘要模型视觉能力探测结果已明确。

Qdrant 不可用不阻止 PostgreSQL 摄取，但预检必须记录该状态，最终 Run 不能错误报告全部向量成功。

## 10. 事务、错误处理与降级

### 10.1 搜索阶段

- OpenClaw 超时、非零退出或 JSON 非法：Run 为 `failed`，不处理视频。
- 单个候选非法：记录 warning 并继续；非法候选不计入有效 discovered 数。
- 有效候选为零且没有工具错误：Run 为 `success`，计数为零并记录 `no_candidates` 事件。

### 10.2 视频阶段

- 无公开字幕：自动使用本地 `whisper.cpp`。
- `whisper.cpp` 失败：该视频失败，不用简介冒充完整转录。
- 抽帧全部失败：降级为 `transcript_only`，允许继续摘要。
- 部分关键帧或 OCR 失败：保留成功结果，并记录请求数、实际数与失败数。
- SiliconFlow 摘要失败或返回空摘要：保留失败 RawPage，不创建 ContentItem。
- 一个视频失败不影响后续视频；成功和失败并存时 Run 为 `partial`。

### 10.3 数据库与向量阶段

- 每条视频先以一个短事务创建并提交 pending RawPage，随后才允许调用 Summarize。
- Summarize 返回后，以独立事务更新 RawPage，并写入 ContentItem 与 chunks；失败路径也必须提交 RawPage 的失败状态。
- PostgreSQL 事务失败：回滚该视频的规范化数据并记录错误。
- PostgreSQL 成功后再写 Qdrant。
- Qdrant 失败：保留 PostgreSQL，相关 chunk 标记 `failed` 并保存脱敏错误，允许重建索引。

### 10.4 资源清理

临时音频、临时视频和中间文件在成功、失败与超时路径都必须清理。最终关键帧只在数据库事务成功后保留；若事务失败，删除本次新建且未被已有内容引用的关键帧目录。

## 11. 可观测性

Run counters 复用现有通用字段：

- `discovered_count`：通过校验的唯一候选数；
- `fetched_count`：实际启动 Summarize 的视频数；
- `parsed_count`：产生有效字幕/转录的数量；
- `extracted_count`：产生有效中文摘要的数量；
- `deduped_count`：因成功 video ID 已存在而跳过的数量；
- `chunked_count`：新建 chunks 数；
- `embedded_count`：成功写入向量的 chunks 数；
- `error_count`：候选或视频级错误数。

事件至少覆盖：搜索开始/结束、候选拒绝、去重、字幕来源、whisper fallback、关键帧请求/实际数量、OCR 降级、摘要开始/结束、数据库提交、Qdrant 失败和资源清理。事件可记录模型名、耗时和数量，但不得记录密钥、完整签名 URL 或未脱敏子进程输出。

## 12. 安全与运行限制

- 仅处理公开、无需认证的 YouTube 内容。
- 不读取或传递浏览器 Cookie，不使用浏览器 Profile，不绕过登录、验证码、地区限制、付费墙或反爬措施。
- 默认串行处理，单次硬限制 5 个候选和 3 个媒体任务。
- 子进程设置 wall-clock timeout、stdout/stderr 大小上限和受控环境变量。
- OpenClaw 与 Summarize 输出必须经过 schema、类型、长度和 URL 校验。
- 文件写入只能发生在配置的临时目录与 slides 根目录。
- 日志、Run config snapshot、RawPage 与异常消息统一经过凭据和签名 URL 脱敏。
- 不长期保存完整视频或音频文件。

## 13. 测试策略

### 13.1 单元测试

- OpenClaw JSON schema 和数量限制；
- YouTube URL 规范化与 allowlist；
- URL 声明 video ID 与解析 ID 不一致；
- 候选内和跨运行去重；
- 五档时长对应的关键帧上限及 32 硬上限；
- slides 相对路径和路径逃逸拒绝；
- summary input mode 判定；
- 凭据、签名 URL 与子进程错误脱敏；
- chunk 类型、时间范围与 Qdrant payload。

### 13.2 Adapter 测试

使用假子进程验证：

- OpenClaw 参数、agent、timeout 和结构化输出；
- Summarize 参数、环境变量、timeout 和 `--slides-max`；
- 搜索词和 URL 不经过 shell 插值；
- 非零退出、超时、超大输出和无效 JSON；
- SiliconFlow 密钥只存在于子进程环境；
- whisper、OCR、vision capability 的降级路径。

### 13.3 PostgreSQL/Qdrant 集成测试

- 搜索 RawPage 与视频 RawPage 的完整 Run 血缘；
- RawPage 先于 ContentItem 持久化；
- summary、transcript、slides metadata 和 chunks 映射；
- 相同 video ID 再次运行时不调用 Summarize；
- 摘要失败不产生半成品 ContentItem；
- Qdrant 失败时 PostgreSQL 内容保留且 chunk 可重试；
- 检索 Evidence 返回稳定 YouTube URL、摘要和匹配字幕片段。

### 13.4 Live smoke

Live smoke 默认使用 1 个候选、1 个视频，人工确认：

- OpenClaw 搜索结果确实对应查询词；
- 字幕优先路径或 `whisper.cpp` fallback 可观察；
- 自适应关键帧数、OCR 与相对路径正确；
- 中文摘要非空且与视频内容一致；
- PostgreSQL 血缘完整；
- Qdrant 可用时中文查询返回正确 Evidence。

Live smoke 只能使用公开、无需认证的视频，且不能成为普通单元测试的前置条件。

## 14. 验收标准

1. 输入中文搜索词后创建一个可观察 Run。
2. OpenClaw 最多返回并通过校验 5 个唯一公开 YouTube 候选。
3. 最多处理 3 个新视频，已成功入库的 video ID 不再次调用 Summarize。
4. 有公开字幕时不启动 `whisper.cpp`；无字幕时能够使用本地 `whisper.cpp`。
5. 关键帧上限按时长自适应、硬上限为 32，实际结果不包含完全重复帧。
6. PostgreSQL 保存稳定视频原址、完整字幕、中文摘要、Summarize 原始 JSON 与关键帧元数据。
7. 摘要输入模式准确；OCR 路径不得标记为 vision。
8. 每个成功视频至少产生一个 summary chunk 和一个 transcript chunk；极短且无可分字幕的合法视频必须产生 summary chunk，并记录 transcript chunk 缺失原因。
9. Qdrant 可用时，中文查询返回视频 Evidence、稳定 YouTube URL 与匹配片段。
10. 单视频失败不阻止其他视频，最终 Run 状态和 counters 与事实一致。
11. 日志和数据库中不存在 API Key、Cookie、浏览器 Profile、签名媒体 URL或本地绝对路径。
12. 再次运行相同搜索时，已有成功视频计入 `deduped_count`，并保留新的搜索快照。

## 15. 第一版非目标

- 定时或事件驱动调度；
- Bilibili、Vimeo 或其他平台；
- 登录态、Cookie、受限内容或地区限制绕过；
- 多视频并行处理；
- 搜索与任务管理前端；
- 对象存储或图片二进制入库；
- 完整视频/音频长期保存；
- 说话人分离与身份识别；
- 自动刷新已有视频摘要；
- 将该流程并入通用 worker；
- 新增通用视频资产/发现关系表。

## 16. 后续演进

当独立脚本的状态、数据契约和失败策略通过验证后，可按优先级考虑：

1. 将流程迁入通用 worker 并保留相同 adapter 接口；
2. 增加手动任务 API 与前端页面；
3. 增加定时搜索和可配置查询；
4. 为已入库视频增加版本化刷新策略；
5. 引入对象存储和第一类关键帧实体；
6. 扩展其他公开视频平台。
