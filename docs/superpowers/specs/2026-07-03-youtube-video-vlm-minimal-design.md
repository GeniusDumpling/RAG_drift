# YouTube 视频 VLM 入库最小闭环设计

## 目标

使用一条公开 YouTube 页面 URL（首个验收样例为
`https://www.youtube.com/watch?v=fAZZLPwbPyg`）打通以下闭环：

1. `yt-dlp` 读取视频元数据并获得 VLM 可访问的视频输入；
2. 调用 SiliconFlow 的 Qwen3-Omni 生成中文描述；
3. 将稳定的 YouTube 页面 URL 和完整描述写入 PostgreSQL；
4. 将描述分块并写入 Qdrant；
5. `/search` 和现有前端返回 YouTube 页面 URL、完整描述和匹配片段。

本阶段不实现 OpenClaw 搜索、定时调度、Bilibili、多视频批处理和管理界面。

## 方案

先尝试把 `yt-dlp` 提取的临时媒体直链立即交给 VLM。若 VLM 无法读取该直链，
则由 `yt-dlp` 下载受限的低清晰度文件，并通过已有公网 HTTPS 域名下的临时媒体路径
提供给 VLM。临时文件和临时 URL 只服务于分析过程，不作为检索结果持久化。

最小版本优先验证直链路径；临时媒体网关作为同一闭环的兜底，但不会继续使用当前
无鉴权、可列目录的 `file_server.py` 直接暴露公网。

## 数据语义

- `ContentItem.item_type`: `video_description`
- `ContentItem.canonical_url`: 稳定的原始 YouTube 页面 URL
- `ContentItem.source_url`: 同一原始页面 URL
- `ContentItem.cleaned_text`: 完整 VLM 中文描述
- `ContentItem.summary_text`: 描述摘要
- `metadata_json.video_page_url`: 原始 YouTube 页面 URL
- `metadata_json.vlm_input_kind`: `direct` 或 `temporary_media`
- `metadata_json.vlm_model`: 实际模型名

不持久化含签名参数的临时 CDN URL、Cookie、鉴权头或本地临时文件路径。
去重键由来源 ID、`video_description` 和规范化 YouTube 视频 ID 组成，不包含 VLM 描述。

## 处理与失败行为

1. 校验 URL 为公开的 HTTP(S) YouTube 视频页面。
2. 通过 `yt-dlp` 获取视频 ID、标题和不高于 480p 的媒体输入。
3. 调用 VLM，要求返回非空中文描述；请求失败时保留脱敏错误，不写入半成品内容。
4. PostgreSQL 内容与 chunks 成功提交后再写 Qdrant。
5. Qdrant 失败时 PostgreSQL chunks 标记为 `failed`，允许已有重建脚本补索引。
6. 无论成功或失败，都清理下载的临时文件。

日志不得输出 API Key、Cookie、完整签名媒体 URL或原始鉴权响应。

## 验收标准

- 对验收 URL 执行一次命令后产生且只产生一个 `video_description` 内容项；
- 内容项保存稳定 YouTube 页面 URL 和非空中文描述；
- 至少产生一个描述 chunk；
- Qdrant 可用时 chunk 的 `embed_status` 为 `success`；
- 使用与视频内容相关的中文查询并过滤 `video_description` 时，`/search` 返回
  `video_url`、`description_text` 和匹配片段；
- 再次导入相同 URL 时跳过重复 VLM 调用和重复内容写入；
- 临时文件在成功和失败路径均被删除。

