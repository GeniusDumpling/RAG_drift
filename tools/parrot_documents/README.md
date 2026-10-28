# Parrot 文档处理

在仓库根目录执行，复用项目 `.venv`。依赖与 DJI 工具相同，见 `requirements.txt`；扫描页 OCR 需要 Tesseract 的 `eng`、`chi_sim` 语言数据。

读取 `data/Parrot无人机文档/文档索引.csv` 和 `网页采集记录.csv`，处理 PDF 与 HTML，输出与独立 `drone_documents` 数据库兼容的数据包。不修改源文件。英文等来源语言完整保留，不执行全量翻译。HTML 为主输入，TXT 不重复入库。

## 本地解析与检查

```bash
.venv/bin/python -m tools.parrot_documents list-scopes

# 先处理 ANAFI Ai 用户手册；不调用模型、不写数据库
.venv/bin/python -m tools.parrot_documents prepare \
  --scope anafi-ai --kind pdf --document-type user_manual --ocr off \
  --bundle data/processed/parrot-documents/anafi-ai-v1

# SDK 示例，包括代码、表格与章节锚点
.venv/bin/python -m tools.parrot_documents prepare \
  --scope sdk-olympe --kind html --limit 3 \
  --bundle data/processed/parrot-documents/olympe-sample-v1

# 全部本地资料；新输出目录必须不存在
.venv/bin/python -m tools.parrot_documents prepare \
  --bundle data/processed/parrot-documents/full-v1

.venv/bin/python -m tools.parrot_documents validate --bundle data/processed/parrot-documents/full-v1
.venv/bin/python -m tools.parrot_documents report --bundle data/processed/parrot-documents/full-v1
```

`--input` 可指定另一个 Parrot 采集目录。`--scope`、`--document-type` 可重复；默认选全部可用文档。`--no-figures` 关闭 PDF 图示裁剪，`--no-page-renders` 关闭页面预览图；原始 PDF、正文坐标仍保留。`--ocr off` 关闭 OCR。`--chunk-max-chars` 默认 2000。

解析中断后，在原命令添加 `--resume`，复用 `checkpoints/` 中已完成文档的结果与资源。恢复会核对输入索引、配置、文件与资源哈希；配置变化需新目录。已完成的数据包恢复时只校验，不覆盖后续 enrich 结果。图示模型/提示词纳入处理配置，切换后需新数据包。

输出包括 `manifest.json`、`documents.jsonl`、`models.jsonl`、`pages.jsonl`、`elements.jsonl`、`chunks.jsonl`、`links.jsonl`、`assets/`、`collection_failures.jsonl`、`quality_report.json`、`REPORT.md` 和 `preview.html`。校验覆盖文件哈希、资源、页面和元素引用。通过本地 HTTP 服务查看预览：

```bash
.venv/bin/python -m http.server 18771 --bind 127.0.0.1 \
  --directory data/processed/parrot-documents/anafi-ai-v1
```

访问 `http://127.0.0.1:18771/preview.html`，核对原始 PDF 页、图示及英文表格；文字坐标保存在 `elements.jsonl`，网页按章节锚点定位。预览转义所有正文和代码，不执行来源 HTML。

## 模型处理、入库与检索

以下命令使用真实模型或数据库服务。沿用项目 `.env` 的 `VLM_*`、`EMBEDDING_*`，及 `DOCUMENT_DATABASE_URL`、`DOCUMENT_QDRANT_URL`、`DOCUMENT_QDRANT_COLLECTION`；可使用命令参数覆盖数据库和向量库目标。默认使用独立数据库 `drone_documents` 和文档 collection，与 DJI 文档共存，拒绝写入原业务库或业务 collection。

```bash
.venv/bin/python -m tools.parrot_documents enrich --bundle data/processed/parrot-documents/anafi-ai-v1
.venv/bin/python -m tools.parrot_documents bootstrap
.venv/bin/python -m tools.parrot_documents import --bundle data/processed/parrot-documents/anafi-ai-v1
.venv/bin/python -m tools.parrot_documents index --bundle data/processed/parrot-documents/anafi-ai-v1
.venv/bin/python -m tools.parrot_documents query \
  --bundle data/processed/parrot-documents/anafi-ai-v1 \
  --question 'ANAFI Ai 飞行前需要检查什么？'
```

无图示的数据包同样执行 `enrich`，无需图片模型请求即可进入可导入状态。有图示时生成中文描述，保留原图，模型说明独立标记并通过现有质量隔离规则；不将其当作原文。失败后可重跑 `enrich`、`import`、`index`，复用成功缓存并保留旧的有效版本。`run` 对新数据包顺序执行 prepare → report → enrich → bootstrap → import → index；它会调用真实服务，不默认运行。

`evaluate --questions-file questions.json --bundle ...` 使用中文/英文问题数组检查真实召回，结果保存在 `retrieval-report.json`。它不生成答案，不代表答案准确率验收。`query` 同样只返回证据。退出码 0 表示命令成功，1 表示错误，2 表示图示/索引部分失败或测试问题未召回证据。

## 数据与适用范围

- PDF 按内容 SHA-256 去重；同文件的全部标题、URL、系列关联保存在 `archive_associations`。HTML 按 URL 和内容版本识别。每次选子集仍采用全局一致的别名和身份。
- 索引失败记录保留；成功记录必须有可校验文件。Windows 路径统一并防止越出输入目录。缺失日期保持空值；PDF 封面的版本及 Updated 日期单独提取，不用 URL 年月猜发布时间。
- `parrot:` 为 scope 命名空间。复用 `drone_models`/关联表保存产品系列和 SDK 项目归档范围；文档元数据的 `scope_kind` 区分二者，SDK scope 不是虚构机型，也不自动声明兼容机型。查询只取所选 scope 的数据库有效版本，不固定筛选中文。
- 英文 PDF 书签、表格、原始页序、旋转前坐标与原图保留。PDF 使用原生坐标/几何规则，并非通用版面模型；多栏、无边框表格需抽查。文字提取失败记录在质量报告，图片页可通过 OCR 处理。
- HTML 保留标题层级、代码缩进、列表、表格跨度、链接及现有锚点。同一章节、页面/锚点的相邻正文合并，代码、表格和参数独立切片；长代码按字符偏移拆分，完整代码仍在元素层保存。`source_spans` 记录每个片段在原元素及切片中的偏移。参数页按原文名称/值保存，不自动合并不同型号、配置或版本的数值。
- 原采集快照已经将网页图片替换成占位符，工具只报告缺失，不伪造原图或自动补抓。
- 新解析规则应提升 `PIPELINE_VERSION` 并创建新输出目录；已发布内容不可原地覆盖。文档库尚未接入业务前端。

```bash
.venv/bin/pytest -q tools/parrot_documents/tests tools/drone_documents/tests
.venv/bin/ruff check tools/parrot_documents tools/drone_documents
```
