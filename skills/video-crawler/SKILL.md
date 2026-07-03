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

## 环境变量

| 变量 | 默认值 | 说明 |
|---|---|---|
| `VLM_BASE_URL` | `https://api.siliconflow.cn/v1` | VLM API 地址 |
| `VLM_API_KEY` | — | API Key（必填） |
| `VLM_MODEL` | `Qwen/Qwen3-Omni-30B-A3B-Instruct` | 模型名 |
| `EMBEDDING_PROVIDER` | `sentence-transformers` | embedding 类型 |
| `EMBEDDING_MODEL` | `BAAI/bge-small-zh-v1.5` | embedding 模型 |
