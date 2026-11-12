# iFixit 无人机维修指南导入工具

根据 [数据处理方案](../../docs/ifixit-rag-data-processing-plan.md) 实现本地指南解析、质量检查、步骤分块、PostgreSQL 导入及 Qdrant 索引。代码与测试全部放在本目录，通过现有内容表接入项目检索。

全量处理与集成测试结果见 [验证记录](VALIDATION.md)。

正式环境已完成 391 篇指南导入及 3,502 个 BGE-M3 向量索引，见 [正式导入结果](../../docs/ifixit-production-import-2026-10-21.md)。

图片扩展已完成 4,129 次来源绑定、2,534 张图片观察及 4,006 个视觉文字检索块入库；全量核验与正式回答配图验收通过，见 [图片接入结果](../../docs/ifixit-image-rag-2026-11-11.md)。

从仓库根目录运行，Python 3.11 及以上。使用项目虚拟环境；额外依赖见 [requirements.txt](requirements.txt)。离线处理需要 Pillow；使用实际 tokenizer 时需要 Transformers。数据库沿用项目的模型和迁移，导入工具不创建业务表。

```bash
.venv/bin/python -m pip install -r tools/ifixit_rag/requirements.txt
.venv/bin/python -m tools.ifixit_rag --help
```

也可使用 `.venv/bin/python tools/ifixit_rag/ingest.py <command> ...`。命令输出 JSON；退出码 `0` 表示完成，`2` 表示有逐条失败的部分完成，`1` 表示命令失败。数据质量警告单独记录，不会把正常解析的指南当成导入失败。

**1. 生成和检查数据包**

```bash
.venv/bin/python -m tools.ifixit_rag prepare \
  --input data/ifixit数据 \
  --output data/processed/ifixit/run-001

.venv/bin/python -m tools.ifixit_rag validate \
  --bundle data/processed/ifixit/run-001

.venv/bin/python -m tools.ifixit_rag import \
  --bundle data/processed/ifixit/run-001 --dry-run
```

输出目录必须尚不存在，原始文件不会改写。解析失败逐文件写入质量报告，其余指南继续处理。`validate` 和 `import --dry-run` 不读取数据库设置、不连接数据库、不调用模型。

| 产物 | 用途 |
|---|---|
| `manifest.json` | 输入清单、版本、配置、计数和输出文件校验和 |
| `guides.jsonl` | 规范化指南、稳定文档 ID、检索标签、正文与章节定位 |
| `steps.jsonl` | 步骤出现记录、原始步骤 ID、来源指南、顺序、警告和注意事项 |
| `assets.jsonl` | 按 SHA-256 去重的本地图片，包含所有路径、尺寸、完整解码结果 |
| `media_refs.jsonl` | 封面和步骤图片的远端引用与映射状态 |
| `relations.jsonl` | 前置指南引用、是否存在、步骤是否已包含 |
| `chunks.jsonl` | 概览、简介、工具、零件、前置条件和操作步骤检索块 |
| `raw_guides.jsonl` | 原始 Markdown 与 JSON 的完整副本，导入时无需重新读取源目录 |
| `quality_report.json` | 逐指南质量标记、解析错误、内容分流和总体计数 |

导入前校验全部产物哈希、文档和步骤关联、原始快照哈希、块定义哈希、连续块序号及正文偏移。数据包可移动；原始路径用于溯源，图片二进制仍需保留在原位置或另行迁移。

默认按 UTF-8 字节数估算分块预算，`token_count` 为 null，估计值单独记录；这不代表模型真实 token 数。用于正式向量索引的数据建议从准备阶段就提供与嵌入模型一致的本地 tokenizer：

```bash
.venv/bin/python -m tools.ifixit_rag prepare \
  --input data/ifixit数据 \
  --output data/processed/ifixit/run-002 \
  --tokenizer /path/to/local/embedding-tokenizer \
  --max-tokens 480 --max-chars 1600
```

tokenizer 仅从本地目录或已有缓存加载。预算包含设备、标题和步骤前缀，并受 tokenizer 声明的最大长度限制。长正文只沿换行或句子边界切分；带安全提示的步骤整体保留。仍超长的原子内容记录为 `oversized_atomic_content`，保留完整正文供关键词检索，索引前再按真实 tokenizer 检查；超限时失败并报告，不截断、不丢弃维修操作。可调整预算后重新生成数据包。

**2. 导入 PostgreSQL**

```bash
.venv/bin/python -m tools.ifixit_rag import \
  --bundle data/processed/ifixit/run-002
```

默认读取项目配置中的 `SYNC_DATABASE_URL`；可通过 `--database-url` 指定其他 PostgreSQL 数据库。命令创建或复用专用 SourceSite、禁用调度的本地导入 CrawlJob，并记录 CrawlRun 和逐指南事件。RawPage 保存原始 JSON、Markdown 与本地导入时间；该时间不表示源站抓取时间。

一篇指南写入一个 `item_type=repair_guide` 内容项。`dedup_key=ifixit:<guideid>:<locale>`，重复导入保持同一内容项；内容或原始快照变化时更新指南并保留新的 RawPage。步骤结构、前置关系、媒体引用和质量标记保存在 `metadata_json`，专用切片写入 `content_chunks`。同时更新 `search_tsv`；现有关键词检索也可通过展示正文与标签召回待索引内容。

切片通过逻辑键派生稳定 UUID。步骤重排保留相同逻辑块的 UUID；删除的切片标为 `obsolete`，停止参与现有检索。旧向量删除任务记录在切片元数据中，等待索引阶段处理。每篇独立事务，单篇失败后继续；重跑同一个数据包即可重试。全流程使用数据库 advisory lock，避免本工具并发修改同一批数据。

源 API 的步骤数组已按执行顺序展开前置指南；其中 `orderby` 会在每个来源指南内重新从 1 开始。本工具保留数组顺序生成全局 `step_order`，同时保存 `source_order` 和 `source_guide_id`，不按局部编号全局排序。

输入指南减少不会自动删除已入库的其他指南。切片更新和索引由本工具管理，`chunk_owner=ifixit_rag` 标识归属。请使用本工具重新导入／索引维修指南；现有 worker 的通用 `chunk_and_index_content_items` 不支持结构化维修分块，不应对这些内容调用。现有全局 embedding reindex 只读取已存切片，可重建向量，但不负责本工具的旧向量清理队列。

**3. 写入或重试向量索引**

```bash
.venv/bin/python -m tools.ifixit_rag index \
  --bundle data/processed/ifixit/run-002 \
  --tokenizer /path/to/local/embedding-tokenizer \
  --max-tokens 480 --max-chars 1600
```

也可以在 `import` 命令上增加 `--index --tokenizer ...` 连续执行两个阶段。仅执行 `import` 不调用嵌入服务。实际服务由项目的 `EMBEDDING_*`、`QDRANT_URL` 和 `QDRANT_COLLECTION` 设置决定；tokenizer 路径应与该模型匹配。命令不自动下载模型。

索引流程先处理已持久化的旧点清理，再写新向量，每次成功后提交状态。成功且目标配置未变的块跳过；失败块重跑即重试。真实 tokenizer 再次检查长度。数据包与数据库内容版本不一致时拒绝索引该指南。运行报告写入数据包目录，也可通过 `--report` 指定路径。

同一向量集合内若已有数据库记录表明使用了其他嵌入模型，会拒绝混写，并在写入前检查集合维度。更换模型应使用新 collection；工具不重置或删除整个集合。数据库以外创建且没有模型记录的集合不在模型身份检查的识别范围内。

若切换 Qdrant 地址，第一次 `index` 会把旧地址的删除任务持久化并报告失败。使用独立清理命令处理旧地址，再重跑面向新地址的 `index`：

```bash
.venv/bin/python -m tools.ifixit_rag cleanup \
  --bundle data/processed/ifixit/run-002 \
  --qdrant-url http://old-qdrant-host:6333
```

`cleanup` 只处理已有删除任务，不嵌入、不创建向量。其他地址的任务保留并报告数量。向量写入前也持久化目标点 ID，即使远端写入成功后响应超时，后续替换仍可清理可能存在的旧点。

导入只更新数据库时，旧向量可能暂时存在；更新后的内容仍可关键词检索，`obsolete` 块会被现有检索排除。完成 `index` 并检查失败计数后，才算完成向量同步。

**4. 图片映射与内容边界**

`image_001.jpg` 不作为步骤对应关系的依据。默认图片引用标为 `unresolved`，本地文件完整入清单。若有经过核验的下载日志或人工映射，使用 `prepare --media-map mapping.json`：

```json
[
  {
    "document_id": "ifixit:114388:en",
    "source_guid": "源图片的 guid",
    "local_path": "data/ifixit数据/114388_DJI_Mavic_Pro_GPS_Module_Replacement/image_002.jpg",
    "sha256": "可选的预期文件哈希",
    "evidence": "经核验的下载日志或人工比对说明"
  }
]
```

`local_path` 使用产物清单中的路径。文件必须属于该指南且完整解码成功；映射歧义、路径不匹配、哈希不符或无匹配引用都会使准备命令失败。只有映射被确认的图片才会出现在上下文预览中。

空步骤指南保留简介与视频引用；只有链接而没有有效简介时标为 `insufficient_content`，不生成空检索块。工具不下载视频、不生成转录，也不根据视频链接补造操作。重复图片按文件内容去重；共享维修步骤保留各指南中的位置，不跨设备合并证据。

**5. 接入检索与检查上下文**

现有搜索接口可使用以下过滤条件，两路检索都沿用已有 tags 过滤能力：

```json
{
  "query": "DJI Mini 2 电池更换",
  "filters": {
    "item_type": "repair_guide",
    "tags": ["ifixit", "device:dji mini 2"]
  },
  "top_k": 10
}
```

中文部件与操作词通过人工术语表加入检索标签，原文保持英文。术语表位于 `cleaning.py`，不代表中文维修内容的人工翻译。设备键只做空白和大小写规范化，不合并相近型号。

离线上下文检查可读取完整指南，或指定步骤及前后各一步：

```bash
.venv/bin/python -m tools.ifixit_rag context \
  --bundle data/processed/ifixit/run-002 \
  --guide-id 114388 --step 10 --max-chars 12000
```

输出包含来源、前置依赖、质量标记以及因预算省略的章节。前置指南只返回关系，不递归拼接，因此不会重复附加已包含的步骤或进入依赖循环。此命令用于检查导入产物。回答 API 的上下文补全由 `answer_context.py` 独立完成；图片映射与显示按下述流程启用。

**6. 测试**

```bash
.venv/bin/pytest -q tools/ifixit_rag/tests
.venv/bin/ruff check tools/ifixit_rag
```

没有测试数据库配置时，离线测试执行，数据库集成测试显式跳过。要运行集成测试，设置 `IFIXIT_TEST_DATABASE_URL` 指向名字以 `_test` 结尾的 PostgreSQL 数据库。每项测试创建独立随机 schema，并在结束后只删除该 schema，不重建整个数据库。测试向量服务使用内存后端与固定测试嵌入，不调用付费模型。

设置 `IFIXIT_TEST_BUNDLE` 为准备好的数据包目录，可附加全量两次导入测试。设置 `IFIXIT_TEST_QDRANT_URL` 可附加真实 Qdrant 测试，使用随机测试集合与固定测试向量，结束后删除该集合。

测试覆盖清洗保真、步骤身份和顺序、空步骤分流、图片去重与映射、解码失败、前置关系、块偏移、超长内容保留、校验和、幂等导入、步骤更新清理、模型空间冲突和失败重试。真实中文维修问答集、Recall 指标与真实嵌入效果仍需按方案另外验收，本工具不生成或宣称已人工核验的评估结论。


**7. 图片绑定、视觉文字与回答配图**

先完成原始指南导入，再按顺序执行以下命令。`media` 从指南记录的图片 GUID 下载源图，验证解码并按 SHA-256 存储；不会按 `image_001.jpg` 的序号猜测步骤。下载支持同一 GUID 的尺寸变体回退，实际下载地址另行记录。

```bash
.venv/bin/python -m tools.ifixit_rag.media \
  --bundle data/processed/ifixit/20261021-production-bge-m3
.venv/bin/python -m tools.ifixit_rag.media_enrichment sync \
  --bundle data/processed/ifixit/20261021-production-bge-m3
.venv/bin/python -m tools.ifixit_rag.media_enrichment analyze \
  --bundle data/processed/ifixit/20261021-production-bge-m3 --workers 6
.venv/bin/python -m tools.ifixit_rag.media_enrichment index \
  --bundle data/processed/ifixit/20261021-production-bge-m3
```

所有命令默认写入 `data/ifixit-media/`，可用 `--media-dir` 修改。分析使用项目配置的视觉模型、服务地址和密钥；索引使用现有 embedding、PostgreSQL 与 Qdrant 配置。`analyze --guide-id 161708` 可先验证单篇指南；`index` 索引当前目录下所有成功观察记录。成功记录与真实模型响应可复用，失败项在重跑时重试。全量完成须检查 `analysis-report.json` 的 `success + cached == total`、`failed == 0`，以及 `index-report.json` 的失败数。中断时只有进度文件，不能据此声称全量完成。

分析与索引启动前会校验观察、模型响应和向量缓存。损坏记录移到 `quarantine/<时间>/` 保留副本，写入 `cache-recovery-report.json`，随后按缺失项重建；有效记录继续复用。进行中的 `.tmp` 文件不会被移动。结构化产物写入时同步文件及父目录，减少异常中断后出现空文件的风险。判断完成数量须验证 JSON 内容，不能只统计文件名。

若密集终端截图因重复日志导致模型输出无法解析，可对 `analyze` 增加 `--focused-ocr`，仅提取维修命令、串口号、设备型号和关键标签。此选项只影响未命中成功缓存的图片，并在新记录中保存 `ocr_mode=focused`；原图和先前响应继续保留，不代表完整日志转录。

存储结构：

| 路径 | 内容 |
|---|---|
| `catalog/<GUID>.json` | 原始 URL、实际下载 URL、GUID、文件哈希、尺寸和绑定依据 |
| `blobs/<SHA256>.<ext>` | 去重后的源图，提供给浏览器查看 |
| `analysis-inputs/<SHA256>.jpg` | 限长边 2048 的模型输入副本，源图保持原样 |
| `observations/<SHA256>.json` | 模型描述、OCR 候选、不确定性、原始 OCR 行、模型与输入哈希 |
| `vision-responses/`、`embedding-cache/` | 精确请求响应缓存和真实向量缓存 |
| `download-report.json`、`sync-report.json`、`analysis-report.json`、`index-report.json` | 阶段结果及错误清单 |

视觉描述以 `chunk_owner=ifixit_media`、`chunk_type=image_observation` 写入现有内容切片表与向量集合，并保留对应指南、步骤和媒体引用。原始步骤文字及其 `Likely` 等限定和生成观察分开标注。基础指南重导入／重建索引会保留这类切片。封面只有未在本指南步骤中使用时才新增视觉切片，避免同图在同指南内重复索引。

服务运行时设置 `IFIXIT_MEDIA_DIR` 并只读挂载图片目录；部署文件已有对应环境变量与挂载。`GET /ifixit-media/assets/<SHA256>` 校验文件哈希后返回源图。检索结果包含步骤图片；回答只展示实际被 `[n]` 引用的证据图片，保留引用编号、设备、步骤和原始指南链接。简介／概览可展示明确标记的指南封面。不同机型名称保持独立，不能用 Mini 3 或旧 Mavic Pro 的图片代替 Mavic 3 Pro。

浏览器验收命令会实际调用问答服务，并验证图片加载和历史会话恢复：

```bash
.venv/bin/python -m tools.ifixit_rag.verify_images \
  --base-url http://127.0.0.1:8000 \
  --output data/ifixit-media/verification-production
```

本阶段通过文字、原始步骤、模型 OCR 和描述检索证据并返回图片。没有用户上传图片检索入口。

全量完成后运行只读核验：

```bash
.venv/bin/python -m tools.ifixit_rag.audit_media \
  --bundle data/processed/ifixit/20261021-production-bge-m3
```

该命令将全部图片引用与内容哈希、模型观察、正式库切片及 Qdrant 点逐一比对，检查原始块正文、视觉块正文、模型、维度和图片哈希，报告写入 `audit-report.json`。覆盖数以 `[实际数, 应有数]` 表示；缺少任意一层时 `complete=false`、退出码为 `2`。它不会调用生成模型，也不会修改数据库或向量库。分析与索引运行中可能产生暂时的覆盖差异，应在最终索引结束后再作完成判定。
