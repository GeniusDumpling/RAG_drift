---
name: video-crawler
description: 定时搜索各视频平台的无人机相关视频（型号参数、安全脆弱性、故障），获取URL→VLM摘要→入库RAG。
---

# Video Crawler — 无人机视频爬取

## 搜索主题

每次执行按以下关键词组合搜索各视频平台（YouTube/Bilibili 等），优先获取视频页面 URL。

### 优先级 1️⃣ 无人机型号与参数
- 无人机型号对比、参数讲解
- 拆机评测、硬件展示
- 无人机规格、性能测试

### 优先级 2️⃣ 无人机脆弱性安全
- 物理与硬件安全：USB/调试接口、天线、防篡改
- 传感器与导航安全：GPS 干扰/欺骗、电磁干扰、导航冗余
- 通信与地面站控制：遥控信号劫持、MAVLink 监听、明文遥测

### 优先级 3️⃣ 无人机故障
- 信号丢失、炸机
- 飞控故障、电机异常
- GPS 丢失后的行为

## 工作流程

```
cron（每8h）
  ↓
Agent 按关键词搜索视频平台 → 拿到页面 URL
  ↓
video_fetch.py --video-url <URL> --source <platform>
  ↓
resolve_video_url():
  ├── 直接 .mp4/.webm 可播放 → 直送 VLM ✅
  └── 平台页面 → yt-dlp 解析直链
  ↓
VLM（Qwen3-Omni）生成结构化中文摘要
  ↓
分块 → SentenceTransformer → PostgreSQL + Qdrant
```

## 使用方法

```bash
cd /home/admin/.openclaw/workspace/RAG_drift

# 从 YouTube 视频 URL 入库
.venv/bin/python skills/video-crawler/scripts/video_fetch.py \
  --video-url "https://www.youtube.com/watch?v=xxx" \
  --title "无人机型号参数介绍" \
  --source youtube

# 从 Bilibili 视频入库
.venv/bin/python skills/video-crawler/scripts/video_fetch.py \
  --video-url "https://www.bilibili.com/video/BV1xx411c7mD" \
  --source bilibili
```

### 仅验证 YouTube 解析与 VLM 描述（不入库）

```bash
.venv/bin/python skills/video-crawler/scripts/video_fetch.py \
  --analyze-only \
  --video-url "https://www.youtube.com/watch?v=fAZZLPwbPyg" \
  --source youtube \
  --json
```

该模式使用 `yt-dlp` 获取不高于 480p 的临时媒体输入，调用 VLM 后只输出稳定的
YouTube 页面 URL、视频元数据和中文描述，不连接 PostgreSQL 或 Qdrant，也不输出
临时签名媒体 URL。

### 下载 720p 内视频后交给 VLM（不入库）

当远端 VLM 无法稳定访问 YouTube 临时签名媒体 URL 时，先把视频下载到
`skills/video-crawler/downloads/`，再通过公网文件服务暴露为稳定 MP4 URL：

```bash
.venv/bin/python skills/video-crawler/scripts/video_fetch.py \
  --analyze-only \
  --download-for-vlm \
  --public-media-base-url "https://example.com/video-crawler" \
  --video-url "https://www.youtube.com/watch?v=xxx" \
  --source youtube \
  --cookies skills/video-crawler/cookies_www.youtube.com.txt \
  --json
```

该模式优先下载浏览器/VLM 兼容性最好的 `18` progressive MP4（通常为 360p），
再回退到不高于 480p/720p、音视频同文件的媒体。JSON 输出包含本地文件名、
公网媒体 URL、视频元数据和中文描述；仍然不连接 PostgreSQL 或 Qdrant。

## 无完整视频下载的 YouTube 字幕证据采集

`youtube_transcript_evidence.py` 是新的轻量第一阶段：读取公开 YouTube 元数据和公开字幕，不会保存完整视频或暴露公网媒体 URL，也不会写入 PostgreSQL/Qdrant。无公开字幕时，默认用 `yt-dlp` 解析临时音频流、由 `ffmpeg` 创建处理后自动删除的单声道音频，再交给本地 `faster-whisper` 转写；标题和简介绝不作为转写替代品。

```bash
.venv/bin/python skills/video-crawler/scripts/youtube_transcript_evidence.py \
  --video-url "https://www.youtube.com/watch?v=xxx" \
  --language zh \
  --json
```

输出包含稳定的 YouTube 页面 URL、视频 ID、标题、简介、频道、时长、人工/自动字幕或 `local_whisper` 来源、带时间戳字幕段，以及设计规定的关键帧数量上限。公开视频人工字幕优先于自动字幕。

脚本默认使用 `yt-dlp` 的临时不高于 480p 视频流，并让 `ffmpeg` 将均匀分布的帧先输出为**内存 JPEG**，再保存到 `skills/video-crawler/downloads/<video_id>/keyframes/`。每帧文件名包含抽帧序号和时间戳；JSON 返回时间戳、字节数和本地路径，不输出图像二进制或临时媒体 URL。可用 `--keyframes-dir <目录>` 覆盖保存根目录；`--extract-keyframes` 保留为兼容旧调用的无操作参数。

`--summarize-with-vlm` 会隐式提取关键帧，加载 `.env` 中的 `VLM_BASE_URL`、`VLM_API_KEY`、`VLM_MODEL`，以 OpenAI 兼容的 `/chat/completions` 请求将真实字幕/ASR 文本和内联 `data:image/jpeg;base64,...` 关键帧共同发送给 VLM。输出的 `video_summary` 只要求基于转写与画面直接支持的信息；不会发送本机或公网视频 URL。

## 环境变量

| 变量 | 默认值 | 说明 |
|---|---|---|
| `VLM_BASE_URL` | `https://api.siliconflow.cn/v1` | VLM API 地址 |
| `VLM_API_KEY` | — | API Key（必填） |
| `VLM_MODEL` | `Qwen/Qwen3-Omni-30B-A3B-Instruct` | 模型名 |
| `EMBEDDING_PROVIDER` | `sentence-transformers` | embedding 类型 |
| `EMBEDDING_MODEL` | `BAAI/bge-small-zh-v1.5` | embedding 模型 |
