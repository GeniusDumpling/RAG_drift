# 通用网页采集

独立于供应商分析流程，调用同一百度千帆搜索 API，按关键词获取候选网页，再下载页面并用 trafilatura 提取正文。当前只产出文件，不连接数据库，不执行切片、索引或实体抽取。

## 运行

在仓库根目录执行（Python 3.11+）：

```bash
python -m pip install -r tools/web-crawler/requirements.txt
python tools/web-crawler/crawl.py --dry-run
python tools/web-crawler/crawl.py
# 使用另一份配置
python tools/web-crawler/crawl.py --config /path/to/conf.yaml
```

密钥与已有供应商模块共用：在进程环境变量或**仓库根目录 `.env`** 设置 `BAIDU_QIANFAN_API_KEY`，环境变量优先。密钥不写进配置、请求快照或运行摘要。

修改同目录 `conf.yaml`：

- `search.keywords`：关键词列表，每轮执行全部关键词，重复关键词合并。
- `search.top_k`：**每个关键词的候选网页上限**（1–50）。同时控制 API 的网页 `top_k` 和本地截断，不保证成功下载数量；不会为了凑够成功数反复搜索。不同关键词相同 URL 本轮只下载一次，并保留全部命中记录。
- `search.recency_filter`、`domain_filter`：时间和站点过滤。
- `crawl.enabled`：是否下载正文；关闭时保存搜索结果，文档状态为 `search_only`。
- `crawl.timeout`、`retries`、`delay`：超时、临时错误额外重试次数、网页抓取间隔。搜索也使用该重试次数，授权失败等普通 4xx 不重试。
- `output.directory`：相对于配置文件的输出目录，也支持绝对路径。

API 返回的网页数可能少于配置数。网页 `top_k` 最大 50、仅返回摘要及链接的接口定义见[百度官方文档](https://ai.baidu.com/ai-doc/AppBuilder/pmaxd1hvy)。

退出码：`0` 表示流程完成（也可能零搜索结果），`1` 表示配置、密钥或文件等致命错误，`2` 表示部分搜索/页面失败、正文为空或类型不支持。具体数量见 `manifest.json`。中断后的 manifest 保留 `running`，已写入的 JSONL 行仍可读取；重跑会新建目录，不提供断点续传。

## 为什么不只保存原始搜索 JSON

百度响应是搜索候选和摘要，不能当作网页完整正文。单独保存它会让后续模块依赖百度字段，且缺少网页证据、正文版本和逐篇处理状态。因此采用原始证据层与标准化文档层：

```text
output/<UTC时间_随机ID>/
├── manifest.json             # 配置快照、请求参数、搜索状态、逐类计数
├── documents.jsonl           # 一行一个网页，后续流程的主要输入
└── raw/
    ├── search/001.json       # 每个关键词的完整 API 响应体，不改写字段
    └── pages/<URL哈希>.html  # 解压后的 HTTP 响应体字节；非 HTML 用 .bin
```

错误响应也原样保留；搜索服务返回非 JSON 错误内容时，搜索快照的 `.json` 扩展名不代表它能解析，需结合 manifest 中的错误判断。网页下载后即保存响应体，HTTP 错误页也能回溯。所有产物路径相对本轮目录，方便整体迁移。默认 `output/` 已加入 `.gitignore`，自定义输出位置需自行管理。

`documents.jsonl` 的关键字段：

| 字段 | 含义及后续用途 |
| --- | --- |
| `schema_version`, `run_id` | 格式版本、采集批次 |
| `document_id`, `canonical_url` | `web:` + 规范化搜索 URL 的 SHA-256；跨轮稳定，可用于幂等入库 |
| `source_url`, `final_url` | 搜索链接、HTTP 重定向后的实际地址，保留溯源 |
| `title`, `author`, `language`, `published_at` | 页面元数据；未知时为空。发布日期是提取器原始日期字符串，不补造时区或精度 |
| `discovered_at`, `fetched_at` | UTC ISO 8601 时间，区分发现与抓取 |
| `search_date` | 百度提供的原始日期，与页面提取日期分别保留 |
| `text` | 纯文本正文，保留段落换行，供后续切片或实体抽取使用 |
| `text_sha256` | 正文版本标识；未来可按 `(document_id, text_sha256)` 判断是否需要重新切片和索引 |
| `raw_page_path`, `raw_sha256` | 网页原始证据路径、响应体哈希；可重新清洗或升级提取器 |
| `search_matches` | 所有命中关键词、排名、摘要、日期、原始响应路径及 JSON Pointer |
| `status`, `error` | `success` / `failed` / `empty` / `unsupported` / `search_only`；不把摘要回填成正文 |
| `http_status`, `content_type`, `encoding` | HTTP 结果、响应类型及客户端判定的编码；HTML 正文由提取器从字节识别编码 |
| `extraction_method`, `extraction_version` | 正文提取方法和版本，便于排查及重处理 |

后续可以将原始证据和抓取状态映射到现有 `raw_pages`，将正文及来源映射到 `content_items`。切片关联 `document_id` 和正文版本，再建立片段及实体的证据位置。本次不预建空的 chunks、embeddings、entities 等加工结果字段。

URL 规范化仅统一协议/主机、去默认端口及 fragment，保留查询参数。不同搜索链接重定向到同一页面时仍可能产生两篇记录；不根据网页自行声明的 canonical URL 合并。当前每轮重新下载，保留各轮快照，便于以后识别内容变化。

当前支持静态 HTML 和纯文本。PDF 等类型保存原始响应体并标记 `unsupported`，不做 PDF 解析、JS 浏览器渲染或 OCR。`success` 表示提取到非空正文，内容质量仍需后续清洗判断；拦截页和登录页的识别也属于后续质量过滤范围。

离线验证：

```bash
.venv/bin/pytest -q tests/unit/test_web_crawler.py
```
