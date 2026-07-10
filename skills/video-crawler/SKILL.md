---
name: video-crawler
description: Use when a user asks to search YouTube for a specified number of drone videos, download them in WSL, expose stable public media URLs, generate Chinese VLM descriptions, and ingest the results into PostgreSQL and Qdrant. Trigger on requests such as "使用 video-crawler skill，获取视频数量：X", YouTube 视频采集, 无人机视频入库, or 批量视频 VLM 摘要.
---

# Video Crawler

仅搜索 YouTube。将用户要求的“获取视频数量：X”解释为成功入库数量：视频必须完成 VLM 描述并写入 PostgreSQL 和 Qdrant；搜索候选数、失败数或重复数均不计入 X。

## 输入契约

- 要求 `X` 为 1 到 20 的整数；缺少数量时询问用户。
- 仅处理时长可确认且不超过 5 分钟（300 秒）的视频；正好 300 秒允许。
- 接受用户附加的主题、型号、时间范围或语言限制；未提供时使用下方默认主题。
- 默认使用 WSL 项目 `/home/gaowei/projects/RAG_drift`。
- 仅处理公开、无需付费授权且允许当前账号访问的 YouTube 单视频页面。
- 不绕过登录、CAPTCHA、地区限制、年龄限制、付费内容或平台访问控制。

## 搜索主题

按以下优先级轮换搜索，避免结果集中在单一主题。组合中英文关键词，并优先选择标题、摘要或频道语境明确相关的视频。

1. 无人机型号与参数
   - `无人机 型号 对比 参数 评测`
   - `DJI drone specifications review`
   - `drone teardown hardware camera flight time`
2. 无人机安全脆弱性
   - `无人机 GPS 干扰 欺骗 安全`
   - `drone GPS spoofing jamming security`
   - `MAVLink interception telemetry security`
   - `drone remote control signal hijacking`
3. 无人机故障
   - `无人机 信号丢失 飞控故障 炸机`
   - `drone lost signal flyaway failure analysis`
   - `drone motor failure GPS loss behavior`

## 执行流程

### 1. 解析请求

提取目标成功数 `X` 和用户附加约束。建立以下运行计数：

- `target_count = X`
- `success_count = 0`
- `skipped_count = 0`
- `failed_count = 0`

### 2. 检查运行环境

进入项目并确认依赖、数据库、Qdrant、Cookie、文件服务和公网媒体地址均可用：

```bash
cd /home/gaowei/projects/RAG_drift
test -x .venv/bin/python
test -s .env
test -s skills/video-crawler/cookies_www.youtube.com.txt
docker ps --format '{{.Names}} {{.Status}}' \
  | grep -E 'intelligence-rag-(postgres|qdrant)'
curl -fsS http://127.0.0.1:18999/ >/dev/null
```

从用户输入、环境变量 `PUBLIC_MEDIA_BASE_URL` 或当前 cloudflared 日志取得 HTTPS 基础地址。不要把临时 `trycloudflare.com` 地址永久写入 Skill。验证地址后再处理视频：

```bash
curl -fsS "$PUBLIC_MEDIA_BASE_URL/" >/dev/null
```

若文件服务未启动，从 WSL 原生下载目录启动：

```bash
cd /home/gaowei/projects/RAG_drift/skills/video-crawler/downloads
nohup python3 -m http.server 18999 --bind 127.0.0.1 \
  >/tmp/video-file-server.log 2>&1 &
```

仅在系统已安装并授权使用 cloudflared 时启动临时 Tunnel。读取日志获取新地址，并更新本次运行的 `PUBLIC_MEDIA_BASE_URL`。

### 3. 搜索候选

使用联网搜索执行主题查询，并限定 YouTube 域名。先收集至少 `max(3 * X, X + 5)` 个候选；候选不足时扩展同义词，不放宽到其他平台。

只保留规范单视频 URL：

```text
https://www.youtube.com/watch?v=<video_id>
```

排除以下结果：

- Shorts、直播、首映等待页、播放列表和频道页；
- 私有、已删除、付费、地区受限或当前账号无权访问的视频；
- 与无人机主题无关、重复视频 ID、纯音乐或无有效画面的内容；
- 时长未知、超过 5 分钟或明显会超过脚本 200 MB 下载限制的视频。

记录每个候选的 `video_id`、页面 URL、搜索主题、标题和选择理由。处理前按 `video_id` 去重。

### 4. 串行下载、描述并入库

逐个处理候选，不并发调用 yt-dlp 或 VLM。始终使用下载后的公网媒体模式，避免把临时 YouTube 签名 URL 直接交给远端 VLM：

```bash
cd /home/gaowei/projects/RAG_drift
set -a
source .env
set +a

PYTHONPATH=.:backend:worker .venv/bin/python \
  skills/video-crawler/scripts/video_fetch.py \
  --download-for-vlm \
  --public-media-base-url "$PUBLIC_MEDIA_BASE_URL" \
  --video-url "$YOUTUBE_URL" \
  --title "$VIDEO_TITLE" \
  --source youtube \
  --cookies skills/video-crawler/cookies_www.youtube.com.txt \
  --json
```

脚本负责：

1. 使用 yt-dlp、Node 和 EJS challenge solver 解析 YouTube；
2. 下载兼容 VLM 的单文件 MP4 到 `skills/video-crawler/downloads/`；
3. 生成基于 `PUBLIC_MEDIA_BASE_URL` 的公网媒体 URL；
4. 调用配置的 VLM 生成中文结构化描述；
5. 保存 RawPage、ContentItem 和 ContentChunk 到 PostgreSQL；
6. 生成 embedding 并写入 Qdrant。

仅当 JSON 返回 `status=success` 且包含 `content_id` 时递增 `success_count`。`status=skipped` 计入 `skipped_count` 并继续补位。其他错误计入 `failed_count`，记录脱敏错误类型后继续下一个候选，直到：

- `success_count == target_count`；或
- 候选耗尽，扩展搜索一次后仍无可用候选。

### 5. 处理 Cookie 和认证失败

将 `Sign in to confirm you're not a bot`、Cookie 读取失败和持续的 YouTube 403 分类为 `auth_required`。不要持续重试，也不要自动绕过 CAPTCHA。

认证失败时：

1. 停止处理后续 YouTube 候选，避免触发更多限制；
2. 保留 Cookie 文件之外的已成功结果；
3. 从已登录的专用浏览器 Profile 刷新 Netscape 格式 Cookie；
4. 将文件安全写入 WSL 的 `skills/video-crawler/cookies_www.youtube.com.txt` 并执行 `chmod 600`；
5. 先用一个候选执行 `--analyze-only --download-for-vlm` 验证；
6. 验证成功后恢复批次，最多自动恢复一次。

浏览器登录失效或出现 CAPTCHA 时，要求人工登录并完成验证。Cookie、VLM Key 和数据库凭据不得写入输出、日志、Git 或 Skill 文件。

### 6. 验证结果

每个成功结果至少验证：

- 本地 MP4 位于 WSL 原生 `skills/video-crawler/downloads/`；
- `media_public_url` 返回 HTTP 200 和视频 Content-Type；
- PostgreSQL 中存在 ContentItem、RawPage 和非零 ContentChunk；
- ContentChunk 的 `embed_status` 为 `success`；
- Qdrant 中存在关联当前 `content_id` 的点。

不要仅凭进程退出码宣称入库成功。

## 最终输出

使用中文返回简洁批次报告：

```text
目标成功数：X
实际成功数：N
跳过数：N
失败数：N

成功：
- YouTube URL | 标题 | Content ID | 公网媒体 URL

失败或阻塞：
- YouTube URL | 阶段 | 脱敏错误 | 是否需要人工处理
```

若实际成功数小于 X，明确说明是候选耗尽、`auth_required`、公网文件服务不可用、VLM 失败还是数据库/Qdrant 失败，不得把部分成功描述为完整完成。

## 常见错误

| 症状 | 处理 |
|---|---|
| `Requested format is not available` 且只有 storyboard | 确认 Node 可用，并保留 yt-dlp 的 `ejs:github` remote component 配置 |
| `Sign in to confirm you're not a bot` | 进入 `auth_required`，刷新 Cookie 后仅重试一次 |
| 视频超过 5 分钟或时长未知 | 跳过且不计入目标成功数，继续补位 |
| 公网 URL 404 | 确认文件服务目录是 WSL 原生 downloads，且文件名与 URL 一致 |
| VLM HTTP 5xx | 记录为外部服务失败，退避后最多重试一次 |
| PostgreSQL 成功但 Qdrant 失败 | 报告部分入库，不计入目标成功数 |
| `status=skipped` | 视为重复，不计入目标成功数，继续补位 |
