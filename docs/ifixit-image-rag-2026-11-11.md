# iFixit 图片证据接入记录

2026-11-11 接入；2026-11-12 全量处理、正式服务验收和幂等复跑完成。

按顺序完成图片来源绑定，再将图片描述和 OCR 作为独立生成内容接入文字检索。正式问答已能为“DJI Mavic 3 pro的主板芯片是什么型号”返回带来源引用的回答及正确主板配图。

**全量结果**

| 层级 | 实际数／应有数 | 结果 |
|---|---:|---|
| 指南 | 391／391 | 原始内容保留 |
| 图片源 GUID | 2,696／2,696 | 下载确认，失败 0 |
| 去重图片与有效模型观察 | 2,534／2,534 | 全部完成，失败 0 |
| 步骤／封面图片引用 | 4,129／4,129 | 正式库同步完成，未解析 0 |
| 原始文本块 | 3,502／3,502 | 正文及归属核验通过 |
| 图片描述／OCR 检索块 | 4,006／4,006 | 正文、模型、归属和图片哈希核验通过 |
| 本次范围内 Qdrant 点 | 7,508／7,508 | 全部为 1,024 维有限、非零向量 |

`audit-report.json` 为 `complete=true`、`error_count=0`，核验时间为 `2026-11-12T02:47:25.313075+00:00`。最终入库新增或更新 371 块，复用 3,635 块，失败 0；随后全量复跑得到 `indexed=0, unchanged=4006, failed=0`。

**来源绑定与存储**

使用数据包 `data/processed/ifixit/20261021-production-bge-m3`。原始 Markdown、JSON 和旧缩略图保持原样。按指南中的图片 GUID 下载源图，以 SHA-256 去重；不会根据 `image_001.jpg` 的序号猜测所属步骤。5 个 `.full` 地址不可用时改用同一 GUID 的可用尺寸，原始 URL 与实际下载 URL 分开记录。

图片位于 `data/ifixit-media/blobs/<SHA256>.<ext>`，`catalog/<GUID>.json` 保存 URL、哈希、尺寸与绑定依据。正式服务只读挂载该目录，通过 `/ifixit-media/assets/<SHA256>` 校验文件哈希后返回图片。指南、步骤和封面引用分别保留。

**视觉处理与检索**

真实视觉模型为 `Qwen/Qwen3-VL-32B-Instruct`。当前流程默认将模型输入限长边至 2,048，源图保持原样。观察记录保存描述、可见文字候选、不确定性、模型、原图哈希；新处理记录还保存输入副本哈希及原始 OCR 行。超长 OCR 行无损拆分。

中断恢复时发现 7 份空观察及对应 7 份空响应缓存，已移入 `quarantine/` 保留副本并全部补齐。分析与索引启动前会检查生成缓存；结构化产物写入时同步文件及父目录。最后一张密集终端截图反复生成无效 JSON，使用 `--focused-ocr` 成功提取维修命令、串口号、型号和关键标签，记录 `ocr_mode=focused`；没有将重复安装日志冒充完整 OCR。原图、旧响应及处理报告均保留。

通过 `BAAI/bge-m3` 将描述与 OCR 转成真实 1,024 维文字向量，写入 PostgreSQL 和 `content_chunks_bge_m3_v1`。切片标记 `chunk_owner=ifixit_media`、`chunk_type=image_observation`，使用稳定 ID，保留所属指南、步骤、GUID 和图片哈希。原始步骤文字及 `Likely` 等限定与生成观察分开标注；基础导入器会保留这些视觉切片。导入时 Qdrant 写入等待时间为 60 秒；少量暂时失败已复用缓存重试成功。

**正式回答与配图验收**

实际提问“DJI Mavic 3 pro的主板芯片是什么型号”，回答列出 H6MN、E2B、P1 等原文标注，并保留 `Likely（疑似）` 限定及主板 Side A 的证据范围。回答区展示以下两张已核对的主板照片：

- 标注主板图：`HB5apup35uvAbdWi`。
- 主板 Side A 原图：`EgE4ofjWnL5oa4ib`。

两张图片均为 2,164 像素宽，HTTP 内容哈希与资产 ID 一致，GUID、URL 与原始指南 161708 的引用一致。验收规则限定到主板步骤和已核对的标注封面，不能仅凭同属该指南就通过。图片加载成功，浏览器无脚本错误，刷新历史会话后恢复相同图片。

前端只展示回答实际引用 `[n]` 的证据图片，保留原始引用编号、设备、步骤和指南链接；可打开源图并展开模型说明。检索按已知设备分类保留 Mavic 3 Pro、旧 Mavic Pro、Mini 3 等型号差异。机型隔离、比较问题及紧凑型号写法另有针对性检查。

本次功能先以 `rag-ifixit-images:20261111-v6` 发布，随后并行图谱发布继承该功能。最终验收实际运行镜像为 `rag-graph-browser-2d:20261111-v2`，端口 8000，健康接口 200，图片目录为只读挂载。

**验证与记录**

图片、引用、机型、封面、OCR 无损保留、缓存中断恢复及数据准备测试 23 项通过；异步 PostgreSQL 检索与回答上下文测试 1 项通过；前端图片、证据卡和问答页测试 19 项通过；Qdrant 行为回归测试 2 项通过。前端生产构建通过。

- [全量核验](../data/ifixit-media/audit-report.json)：各层实际数量与应有数量、全部向量核验结果。
- [分析结果](../data/ifixit-media/analysis-report.json)、[最终入库](../data/ifixit-media/index-final-first-report.json)、[幂等复跑](../data/ifixit-media/index-report.json)。
- [正式浏览器报告](../data/ifixit-media/verification-production-final/browser-report.json)、[截图](../data/ifixit-media/verification-production-final/chat.png)、[运行版本](../data/ifixit-media/verification-production-final/runtime.json)。
- `download-report.json`、`sync-report.json`、`cache-recovery-report.json` 及历史失败报告保存在 `data/ifixit-media/`。

运行与重试命令见 [工具说明](../tools/ifixit_rag/README.md)。全量核验使用 `python -m tools.ifixit_rag.audit_media --bundle <bundle>`；正式浏览器验收使用 `python -m tools.ifixit_rag.verify_images --base-url http://127.0.0.1:8000 --output <目录>`。

OCR 与图片说明是模型生成的候选信息，本次验证不等同于逐字人工核验。当前通过文字与视觉文字记录检索证据并返回配图；未增加用户上传照片检索或视频转录。
