# 无人机中文文档处理工具

按下载索引处理任意已收集 DJI 机型的关联 PDF（包括其他机型目录中的共用文档），并处理技术参数 HTML。支持现有参数页、旧版 Inspire 参数行和 M200 等多子型号页面，也支持只有参数、没有 PDF 索引记录的型号。脚本不修改原文件。所有型号共用独立 PostgreSQL 数据库 `drone_documents` 和 Qdrant collection `drone_document_chunks_bge_m3_v1`，通过机型与有效处理版本过滤检索。

当前处理版本为 `2026-10-27-dji-bulk-v5`。旧 Air 2S v2/v4 和通用化 v3 数据包仍可校验、报告、导入和查询；新解析需要使用新输出目录。

## 其他 DJI 型号全量批处理（v5）

当前批次为 `data/processed/drone-documents/dji-full-20261027-v5/`：排除已完成样例的 Air 2S，覆盖其他 81 个型号、772 份去重 PDF、19,221 个原始页和 81 份技术参数，共 853 个唯一任务。共用文档按文件哈希处理一次，并保留全部关联型号。Air 2S 共用的 LUT 仍可通过共用资料关联使用，旧版本保留。

```bash
.venv/bin/python -m tools.drone_documents batch-plan --batch-root data/processed/drone-documents/dji-full-20261027-v5
.venv/bin/python -m tools.drone_documents batch-start --batch-root data/processed/drone-documents/dji-full-20261027-v5
```

`batch-start` 启动独立协调进程，退出当前终端后仍可运行。默认 2 个解析进程、2 个图片处理进程，每个图片进程最多 3 个并发请求；没有待描述图片的文档使用独立处理队列，入库/索引也使用单独队列。任务按解析→图片处理→入库/向量化执行，单个任务失败不阻断其他文档，重试后仍失败会单独记录。进程被中断时，重新执行同一 `batch-start` 可从检查点恢复；不会重复处理已完成任务。机器重启后需重新启动协调进程。

- 进度查看：`STATUS.md`、`status.json`、`index.html`（需通过本机 HTTP 服务访问）。
- 固定任务计划与输入索引校验：`plan.json`；独立任务状态：`jobs/`。
- 单阶段日志：`logs/`；成功缓存：`cache/`；共享模型限速状态：`rate_limits/`。
- 结束后自动生成每个型号的 `models/<slug>/preview.html`、汇总 `REPORT.md` 和严格点 ID 核对 `verification-report.json`。
- 最终每个型号会执行真实检索检查，未批量生成模型答案，不能据此认定所有型号的答案准确率已经验收。

只有完成所有任务并通过最终核对，批次才报告全量完成。单纯启动进程、解析结束或已有部分向量都不代表全量完成。`batch-finalize --batch-root ...` 可重跑汇总核对，`_batch-stage` 为协调器内部接口。

批量参数差异比较限定同一且明确的机型；多型号共用资料、同页多配置数值以及“低于/约”等限定值保留原文，不自动合并成统一标量。空的非中文文档仍保存原文件与排除理由，零切片无需调用嵌入模型。密集矢量页面采用有标记的整页图示回退，避免几何聚类占用无限时间。

实际运行遇到了 Qwen 模型 TPM 限流，已加入跨进程、按模型分开的请求预算，以及 HTTP 429 冷却重试。可通过环境变量 `DOCUMENT_VLM_TPM`（默认 30000）、`DOCUMENT_EMBEDDING_TPM`（默认 350000）调整本批次客户端预算，预算不是账号的真实配额。遵循[服务方的限流与退避说明](https://docs.siliconflow.cn/docs/userguide/faqs/rate-limit-and-upgradation)，遇到限流自动降低预算，稳定后缓慢恢复；不会自动充值或切换模型。401/402/403 则保留检查点并停止新图片请求。限流失败不作为图片质量隔离；返回格式持续无效的说明会隔离，保留原图和正文。

## 运行环境

从仓库根目录运行，使用已有项目 Python 环境：

```bash
UV_CACHE_DIR=/tmp/rag-document-uv-cache uv pip install --python .venv/bin/python -r tools/drone_documents/requirements.txt
.venv/bin/python -m tools.drone_documents --help
```

原生 PDF 使用 PyMuPDF 的文本坐标、字体、表格识别和矢量图聚类；扫描页回退到 Tesseract OCR，需要系统具备 `chi_sim` 和 `eng` 语言数据。图示说明和演示回答使用项目 `.env` 的 `VLM_*`，文本向量使用 `EMBEDDING_*`，均调用真实服务，不使用模拟向量。图片和向量响应分别缓存，重试不会再次调用已成功缓存的相同内容。

文档库默认沿用 `SYNC_DATABASE_URL` 的服务器、账号和密码，将数据库名改为 `drone_documents`。可以通过 `.env` 或环境变量设置：

```dotenv
# 可选，使用单独连接串时配置；不要指向原业务数据库。
DOCUMENT_DATABASE_URL=postgresql://USER:PASSWORD@127.0.0.1:54329/drone_documents
DOCUMENT_QDRANT_URL=http://127.0.0.1:6333
DOCUMENT_QDRANT_COLLECTION=drone_document_chunks_bge_m3_v1
```

也支持 `--database-url`、`--qdrant-url`、`--collection`。为避免凭据进入 shell 历史，连接串优先放在 `.env`。工具会拒绝目标数据库与现有业务数据库同名，或目标 collection 与现有业务 collection 同名。`bootstrap` 需要数据库创建权限；已有文档库时只创建缺失表，不清空现有表。

## 分阶段执行

先查看下载索引中可用的机型 slug，再选择一个型号。`--model` 必填，不再默认使用 Air 2S。`prepare` 输出目录必须尚不存在；已经生成的数据包从后续阶段继续。

```bash
.venv/bin/python -m tools.drone_documents list-models
.venv/bin/python -m tools.drone_documents prepare --model mini-4-pro --bundle data/processed/drone-documents/mini-4-pro-v3
.venv/bin/python -m tools.drone_documents report --bundle data/processed/drone-documents/mini-4-pro-v3
.venv/bin/python -m tools.drone_documents enrich --bundle data/processed/drone-documents/mini-4-pro-v3
.venv/bin/python -m tools.drone_documents validate --bundle data/processed/drone-documents/mini-4-pro-v3
.venv/bin/python -m tools.drone_documents bootstrap
.venv/bin/python -m tools.drone_documents import --bundle data/processed/drone-documents/mini-4-pro-v3
.venv/bin/python -m tools.drone_documents index --bundle data/processed/drone-documents/mini-4-pro-v3
.venv/bin/python -m tools.drone_documents evaluate --bundle data/processed/drone-documents/mini-4-pro-v3
```

`prepare` 和 `report` 使用本地文件，不调用 VLM 或嵌入接口，可先打开 `preview.html` 检查效果；此时图片说明尚未生成。`enrich`、`index`、`query`、`evaluate` 使用真实模型服务。

`evaluate` 核对 PostgreSQL 的发布状态和 Qdrant 中的全部片段 ID，默认根据当前型号的实际中文内容和参数名称生成最多五个检索冒烟问题，不再固定询问 Air 2S 或 LUT。回答时实际传入最多三张关联原图，同时提供正文证据，保存回答、模型、引用、原页码和使用的图片。这是流程演示，不等同于人工确认的正确率。

固定评估问题可使用 `--questions-file questions.json`，文件格式为非空 JSON 字符串数组，例如 `["该型号的最大起飞重量是多少？", "飞行前需要检查什么？"]`。`run` 同样支持这个选项。原 Air 2S 五个试点问题保存在 `tools/drone_documents/examples/air-2s-questions.json`，可用来复现之前的问答主题。

也可以对新的输出目录执行 `run`，依次完成准备、图片描述、建库、导入、索引、检索和报告。退出码 `0` 为完成，`1` 为命令失败，`2` 为模型描述或向量写入部分失败；部分失败不会发布对应的新解析版本。模型调用失败时可重跑 `enrich`，索引失败时可重跑 `index`。

```bash
.venv/bin/python -m tools.drone_documents run --model mini-4-pro --bundle data/processed/drone-documents/mini-4-pro-new-run
.venv/bin/python -m tools.drone_documents query --bundle data/processed/drone-documents/air-2s-pilot-20261026-v2 --question '带标记的螺旋桨应安装在哪个电机上？'
.venv/bin/python -m tools.drone_documents report --bundle data/processed/drone-documents/air-2s-pilot-20261026-v2
```

`query` 输出检索证据；`evaluate` 才生成图文问答。运行 `query` 会重写当前数据包的 `retrieval-report.json`，需要恢复完整验收结果时重新运行 `evaluate`。

`prepare/run` 可通过 `--input` 指定另一份具有相同 CSV 索引结构的 DJI 数据目录，`--skip-specs` 跳过技术参数，`--ocr off` 关闭扫描页 OCR。也可重复指定 `--document-type`，只处理所选 PDF 类型，参数页仍由 `--skip-specs` 控制。例如先检查快速入门和安全文档：

```bash
.venv/bin/python -m tools.drone_documents prepare --model t30 --document-type quick_start --document-type safety_guidelines --bundle data/processed/drone-documents/t30-selected-v3
```

可选 PDF 类型为 `user_manual`、`quick_start`、`release_notes`、`safety_guidelines`、`shared_reference`、`reference`。未提供筛选时处理该型号的全部索引 PDF。

## 数据包与预览

| 文件 | 内容 |
|---|---|
| `manifest.json` | 处理配置、阶段、版本、数据文件校验和 |
| `documents.jsonl` / `models.jsonl` | 文档版本、源地址、共享机型和适用范围 |
| `pages.jsonl` | 所有原始页码、保留/排除原因、语言标记与渲染资源 |
| `elements.jsonl` | 中文文本、标题、表格、原图与模型生成说明 |
| `links.jsonl` | 图片与相邻正文、说明与原图的关联 |
| `assets.jsonl` / `assets/` | 按 SHA-256 去重的原文件、页面图和图示裁剪 |
| `chunks.jsonl` | 检索文本、片段 UUID、元素和原页码来源 |
| `quality_report.json` | 数量、失败项及已知限制 |
| `documents/*.md` | 逐文档中文阅读结果，包含图示 |
| `documents/*-zh-pages.pdf` | 中文/共用图示保留页的阅读副本 |
| `preview.html` | 可搜索的中文内容、图示对照、页面框选与检索实验 |
| `verification-report.json` | 数据库、向量点一致性和验收结果 |
| `REPORT.md` | 处理概况与问答证据报告 |

```bash
.venv/bin/python -m http.server 18770 --bind 127.0.0.1 --directory data/processed/drone-documents/air-2s-pilot-20261026-v2
```

浏览器打开 `http://127.0.0.1:18770/preview.html`。文件路径在数据库内登记为本机绝对路径；数据包整体可移动，但移动后需要重新导入以更新路径。应用未来读取这些文件时，需要将该目录挂载到应用可访问的持久位置。

## 解析与保存规则

- 简体中文按 `CHS/CHT` 等明确页段标记优先判断；同页混排细化至文字块/行。排除日文、韩文、繁体重复说明和其他语言段落，保留型号、单位、必要软件标识。
- 识别 CHS/CHT、中文语言名及 EN/JP/KR/DE/FR 等语言页标记。快速入门和中文为主手册中的纯图步骤，仅在具有图像/矢量内容且文字为空或全部为技术标识时作为共用图示保留；未标语言的外文说明页和空白页不会自动保留。扫描版快速入门同样支持 OCR。
- 中文阅读 PDF 采用整页复制，因此混排原页仍可见外文。语言筛选使用规则与语言检测，复杂混排和没有明确标记的短句仍需抽查。
- 保存原 PDF 实际页序；`printed_page_label` 是 PDF 页面标签，可能与页面中印刷的页码不同。引用以 `source_page_no` 为准。坐标以未旋转页面左上角为原点、单位为 PDF point。
- 通过渲染页面区域保留矢量线条、箭头及文字标号，而非只导出内嵌位图。图文关系首先是同页几何邻近关系，不作为已经确认的语义关系。
- 原图与派生说明分开保存。描述有 `model_generated`、模型和提示版本等元数据，不覆盖原文。Logo、封面照片等装饰图不生成检索片段。
- 文档按原文件哈希去重，共享手册使用多对多机型关联。同一原文件采用全局索引中的一致记录，避免因处理型号顺序不同而改变共享文档的标题、切片和 ID。共用资料保留通用适用范围提示：索引关联不代表所有参数和步骤适用于全部型号，仍以原文明确条件为准。
- 技术参数保留子型号分组、单位、换行和测试条件；校验原文件哈希及参数数量。采集时已缺失的子型号、空参数值会在 `quality_report.json` 中列出，不补造数值。
- LUT 手册内 Resolve、Premiere、Final Cut 的原生章节标题生成 `software_context`。用户明确询问某软件时先按该字段过滤候选，避免把不同软件的步骤拼接。该字段同时写入 PostgreSQL 元素/切片元数据和 Qdrant payload。
- PostgreSQL 是有效版本与正文的依据；Qdrant 只保存向量和过滤字段。Point ID 与片段 UUID 相同。写入成功后才发布该文档的 `active_processing_id`。
- 重复导入保持相同 ID。已发布解析产物的片段内容不可覆盖；更换解析规则、语言政策、模型或切片策略时应提升处理版本并生成新数据包。旧产物保留供回溯，检索通过 PostgreSQL 的有效版本限制候选。
- 新库中 `content_chunks` 是独立文档切片表，不依赖业务库的论坛/视频表。这次试点没有将新文档自动接入现有网页问答入口。

## 已知限制与检查

### Air 2S 证据质量改进（v4）

已完成样例位于 `data/processed/drone-documents/air-2s-quality-20261027-v4/`。原始 v2 数据包保留。

- **图形符号表格**：从原生 PDF 圆形矢量、径向短线和同页明确图例恢复常亮/闪烁/熄灭，不按电量区间猜测状态。保留每格符号坐标、图例和原图。Air 2S 原 PDF 第 28 页的 8 行电量表已恢复。目前只支持这种符号布局；缺少图例或形状不明时保留原图，不生成猜测表格。
- **图片描述检查**：字段类型、长度、数组上限、重复项和异常连续编号均执行检查。软件章节和截图还由 VLM 对照原图核验。候选不合格时重试，持续不合格的说明隔离且不进入向量索引，原图保留。结构检查不能证明语义正确，模型复核也可能出错，最终回答仍以原文和原图为事实依据。
- **参数脚注与差异**：保存 `footnote` 元素、`qualifies_parameter` 连接和引用脚注的参数片段。同章节相邻参数共享明确脚注标记时关联同一脚注。当前标准化起飞重量、最长飞行/悬停时间，保存每个来源的数值、条件、版本、页码和采集时间。Air 2S 的 19.4 / 32.4 km/h 测试条件差异标记为 `unresolved`，两边来源均保留；检索续航问题时强制补齐两边结构化证据。
- **回答关键条件核验**：从召回原文抽取本试点的关键条件，包括返航额外上升距离、来源测试速度差异和明确灯态对应的区间边界。生成后执行规则检查与模型事实/引用复核，失败时修订，最多三次。仍未通过时，对于本试点已支持的返航操作和续航差异，可回退为带精确原文位置/结构化参数证明的来源记录，标记 `source_verified` 且 `answer_is_model_generated=false`；没有可验证回退则输出 `needs_review`，`evaluate` 退出码为 2。两种情况均保留失败草稿与检查历史，不把失败模型回答报告为已通过。它是有限范围的检查，不是一般答案正确性的证明。

```bash
.venv/bin/python -m tools.drone_documents prepare --model air-2s --bundle data/processed/drone-documents/air-2s-quality-new
.venv/bin/python -m tools.drone_documents enrich --bundle data/processed/drone-documents/air-2s-quality-new --reuse-descriptions-from data/processed/drone-documents/air-2s-pilot-20261026-v2
.venv/bin/python -m tools.drone_documents import --bundle data/processed/drone-documents/air-2s-quality-new
.venv/bin/python -m tools.drone_documents index --bundle data/processed/drone-documents/air-2s-quality-new
.venv/bin/python -m tools.drone_documents evaluate --bundle data/processed/drone-documents/air-2s-quality-new --questions-file tools/drone_documents/examples/air-2s-quality-questions.json --baseline-bundle data/processed/drone-documents/air-2s-pilot-20261026-v2
```

`--reuse-descriptions-from` 只复用候选，不跳过新检查。缓存命中也验证结构与截图核验状态。`parameter_claims.jsonl`、`source_differences.jsonl` 是可检查的来源记录，相同信息随参数元素/切片元数据保存到 PostgreSQL；无需为每种型号建库。Qdrant 保留派生索引，发布新处理版本后检索只读取有效版本。

新报告包括灯态表、图片隔离情况、参数来源差异及逐问题核验结果。详细记录分别为 `caption-quality-report.json`、`baseline-caption-quality.json`、`answer-quality-report.json`。旧答案对照只检查同项关键条件，不把旧引用编号当作新证据编号核验。并发图片处理按原图顺序生成稳定切片，已发布内容不可原地覆盖。

`verify-answers --bundle ...` 可对已保存的真实评估结果进行本地复核并应用可验证来源回退，无额外模型请求。它要求所有证据 ID 和文本与当前已校验的数据包一致，不接受其他版本的证据。报告区分“模型输出通过规则与模型检查”和“原文/结构化来源记录已核对”。本次 7 个问题中前者 5 题、后者 2 题，不能据此宣称模型答案准确率为 100%。

本版是针对原生 DJI PDF 的可检查试点，并非通用神经网络版面分析器。多栏顺序依靠原生坐标排序、章节依据 PDF 书签，复杂无边框表格、同页多个小节仍需人工抽查；没有检测出的表格保留为原生文本。图片描述可能误读细小标号、按钮或箭头，应通过预览中的原图核验。OCR 回退已实现，但这批文件主要使用原生文本，不代表扫描文档能力已完成批量评估。

通用化验证产物位于 `data/processed/drone-documents/generalization-validation-20261026-v3/`：包含 82 个型号参数页的数量/哈希核对，以及 Air 2S、Mini 4 Pro、Matrice 350 RTK、T30、Inspire 1 Pro/RAW 的快速入门 PDF 和参数预览。这批样本只验证本地准备、校验和报告，没有批量调用图片描述/向量服务，也没有发布新数据库版本。

```bash
.venv/bin/pytest -q tools/drone_documents/tests
.venv/bin/ruff check tools/drone_documents
```
