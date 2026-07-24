---
name: video-crawler
description: 搜索无人机公开视频并以字幕、关键帧和 VLM 摘要入库 RAG。
---

# Video Crawler — YouTube 视频证据入库

## 适用范围

仅使用 `scripts/youtube_transcript_evidence.py` 处理公开 YouTube 视频。旧的
`video_fetch.py` 已删除；不再支持将 Bilibili 页面、完整视频文件或公网媒体 URL
直接交给 VLM。

## 搜索主题

按以下主题搜索公开 YouTube 视频，并优先选择时长不超过五分钟、具有可用字幕的候选：

1. 无人机型号、参数、硬件展示和性能测试。
2. 无人机安全：调试接口、GPS 干扰/欺骗、通信链路、遥控劫持、MAVLink 和遥测。
3. 无人机故障：失联、坠机、飞控/电机异常和 GPS 丢失后的行为。

## 完整工作流

```text
搜索 YouTube 候选 → 规范化 URL 并查询 dedup_key
  → 公开字幕（无字幕时可本地 Whisper ASR）
  → 临时媒体流的场景关键帧 + pHash 去重
  → VLM 基于真实字幕/ASR 与内存 JPEG 生成中文摘要
  → PostgreSQL 写入 + Qdrant 索引
```

1. 搜索候选后，先检查 `video-evidence:<canonical_url>` 是否已存在；重复视频不能作为本次成功入库结果。
2. 人工字幕优先于自动字幕。英文原始字幕可用时，传入 `--language en`；不要将标题或简介伪装成转写。
3. 脚本默认抽取关键帧、生成 VLM 摘要并写入 PostgreSQL/Qdrant。只有 `ingestion.status == "success"` 才算完成。
4. 不保存完整视频。只持久化关键帧 JPEG 到 `downloads/<video_id>/keyframes/`，并持久化真实转写、摘要和关键帧元数据。

## 执行命令

在仓库根目录运行：

```bash
uv run --extra video-keyframes --extra local-embeddings python3 \
  skills/video-crawler/scripts/youtube_transcript_evidence.py \
  --video-url "https://www.youtube.com/watch?v=VIDEO_ID" \
  --language en \
  --no-whisper-fallback \
  --json
```

- 常规情况下使用 `--no-whisper-fallback`，避免在无字幕视频上隐式下载/加载 Whisper 模型。
- 仅当明确需要本地 ASR 时，移除该参数，并确保已安装 `video-asr` extra、配置了模型缓存与足够计算资源。
- `--extract-keyframes`、`--summarize-with-vlm`、`--ingest` 为兼容旧调用的无操作参数；完整流程默认启用。
- 用 `--keyframes-dir <目录>` 为测试或隔离运行指定帧保存根目录。

## 证据与入库约定

- VLM 只接收真实字幕/ASR 和内存中的 `data:image/jpeg;base64,...` 关键帧；不得发送临时签名媒体 URL、本机路径或公网视频 URL。
- `content_items.cleaned_text` 保存 VLM 摘要；`raw_pages.raw_text` 与 `content_items.raw_text` 保存真实转写；`summary_text` 保持 `NULL`，避免重复存储摘要。
- 摘要块使用 `video_summary` 类型；相邻字幕按最多 800 字符或 60 秒合并为带起止时间的 `transcript_segment` 块。两类块均须嵌入 Qdrant。

## 环境变量

| 变量 | 说明 |
|---|---|
| `VLM_API_KEY` | VLM API Key，必填。 |
| `VLM_BASE_URL` | OpenAI 兼容 VLM 地址；未设置时使用脚本默认值。 |
| `VLM_MODEL` | VLM 模型；未设置时使用脚本默认值。 |
| `EMBEDDING_PROVIDER` | 向量模型提供方，通常为 `sentence-transformers`。 |
| `EMBEDDING_MODEL` | 本地或远程 embedding 模型名称。 |

## 完成核验

1. CLI JSON 的顶层 `status` 为 `success`，且 `ingestion.status` 为 `success`。
2. `keyframes` 非空，所有 `local_path` 位于 `downloads/<video_id>/keyframes/`。
3. 数据库中存在返回的 `content_id`，所有块的 `embed_status` 为 `success`。
4. Qdrant 中该 `content_id` 的点数等于数据库块数。

## 常见问题

- YouTube 翻译字幕轨可能返回 HTTP 429。可先改用可用的原始字幕语言（例如 `--language en`）；不要把无字幕视频直接标记为已入库。
- 单个关键帧提取失败会被跳过；只有全部候选帧失败时才终止该视频。
- 预先存在关键帧目录不代表视频已成功入库，必须以数据库和 Qdrant 核验为准。
