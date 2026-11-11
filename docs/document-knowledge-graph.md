# 官方技术手册多模态知识图谱

试点采用 DJI Air 2S 中文用户手册 v1.0 和 Parrot ANAFI Ai 英文用户指南 v7.7.1.7，固定 PDF 哈希、版本及 13 个物理页。来源与选页原因见 [选材记录](document-graph-pilot-selection-2026-11-10.md)。代码在 `backend/app/services/document_graph/`，入口在 `tools/document-knowledge-graph/`。本轮结果见[试点验证报告](document-graph-pilot-results-2026-11-10.md)。

## 流程和证据

1. `prepare` 校验 PDF 哈希，提取原生文字及坐标，渲染整页、栅格图和矢量示意图。保留原始 PDF，不使用旧模型图片描述作为事实依据。
2. 按物理页构建窗口。参数页用配置的文本模型读取原生文字；其他页先用配置的视觉模型生成候选观察，再由文本模型整理实体与事实。视觉观察只是中间结果。
3. 部件编号由原文清单定义名称，视觉模型只抄录图中编号，不按外观重猜名称。LED 四灯表可从 PDF 圆点矢量、闪烁射线或绿色/红色图标像素直接解码；不预置电量区间或每行答案，未知符号仍进入视觉审核。独立审核时先盲读候选实际引用的图片，盲读输入没有待验证答案；文本模型再结合原文、图片观察逐项判断。参数以 PDF 原生文字为准，不用视觉 OCR 覆盖清晰的原文值。
4. 程序校验实体端点、原文行、图片区域、页码、哈希及审核覆盖。原页有可提取的数字标注时，编号坐标必须落在引用图片内；LED 原文值必须属于所引表格，防止同页上下表错绑。单栏操作页按原始字号和标题划分章节，阻止 microSD 说明配到 nano SIM 章节图片；多栏或无法识别标题时不强行推断边界。测量与章节范围在发布、查询时从原 PDF 重新计算。模型拒绝与程序拒绝均保留理由。
5. 每个手册版本独立领取任务，逐页保存检查点，记录精确请求缓存与 provider usage。超时、截断、JSON 格式错误均有界重试；不把失败响应当作成功缓存。思考模式在文本抽取/审核时关闭。
6. 全部窗口通过来源及审核绑定后，在一个 Neo4j 事务中写入投影并切换有效构建。失败保留旧有效构建；过期任务可接管，旧工作者不能继续发布。撤回手册后查询与图片接口同时不可见。
7. 从 Neo4j 独立回读实际节点、端点和证据边，与已审核输出及磁盘来源包逐项比对；检索返回原文、物理页码、版本、图片 URL、裁剪坐标和 SHA-256。

`VGEntity` 采用与视频相同的全局身份规则和 Neo4j 连接层，标准名一致时共享节点。部件限定在所属机型范围；文档和视频证据分别管理。当前没有自动合并跨来源的所有产品或部件别名，也没有整本文档自动解析保证。

图中参数、LED 状态和操作以 `DGFact` 保存，数字不建立为实体。事实连接 `SUBJECT/OBJECT`，文字和图片通过 `DGEvidence`、`DGLine`、`DGRegion` 定位；实体提及使用 `DGMention` 保存自己的证据。来源版本和构建分别为 `DGManual`、`DGBuild`，有效指针防止新旧版本混读。PostgreSQL 继续保留业务原文与切片；本文档流程的图谱只写 Neo4j。

## 运行

在项目根目录安装 `uv sync --extra document-graph --extra dev`，配置 `LLM_*`、`VLM_*`、`NEO4J_*`。文档视觉模型使用独立的 `DOCUMENT_GRAPH_VLM_MODEL`，默认 `Qwen/Qwen3-VL-32B-Instruct`，复用 `VLM_BASE_URL/API_KEY`；不会修改视频或其他任务的视觉模型。模型能力见 [官方模型卡](https://huggingface.co/Qwen/Qwen3-VL-32B-Instruct)。`DOCUMENT_GRAPH_ARTIFACTS_DIR` 默认为 `data/document_graph_runs`；构建包必须位于该目录内。API 服务也需要读取相同的来源目录或挂载卷。

```bash
uv run python tools/document-knowledge-graph/build_graph.py prepare \
  --selection tools/document-knowledge-graph/pilot_selection.json \
  --output data/document_graph_runs/my-pilot/source

uv run python tools/document-knowledge-graph/review_sources.py \
  --source-bundle data/document_graph_runs/my-pilot/source \
  --output data/document_graph_runs/my-pilot/source-preview.html

# 只检查来源与范围，不调用模型、不写图谱。
uv run python tools/document-knowledge-graph/build_graph.py pipeline \
  --source-bundle data/document_graph_runs/my-pilot/source \
  --report data/document_graph_runs/my-pilot/build.json \
  --response-cache data/document_graph_runs/my-pilot/responses --dry-run

# 实际构建：会调用配置的模型并写入配置的 Neo4j。
uv run python tools/document-knowledge-graph/build_graph.py pipeline \
  --source-bundle data/document_graph_runs/my-pilot/source \
  --report data/document_graph_runs/my-pilot/build.json \
  --response-cache data/document_graph_runs/my-pilot/responses
```

重启相同命令会读取逐页检查点。已失败任务需加 `--retry-failed`；`--cache-only` 严禁缓存缺失时调用模型。源文件、解析规则或抽取/审核代码改变后使用新的输出报告名，重新校验审核凭据；精确相同的请求仍可重放原始响应。规则升级可能使旧审核失效，需要重建后再提供图谱。JSON 仅在服务明确正常结束、记录值完整时补齐末尾结构括号，绝不补写字段值或接受输出额度截断。usage 将真实调用与重放分开，缺少 provider usage 的请求标记未知，不推算为零费用。

```bash
uv run python tools/document-knowledge-graph/build_graph.py verify --source-id <PDF_SHA256>
uv run python tools/document-knowledge-graph/build_graph.py query --source-id <PDF_SHA256> --q microSD
uv run python tools/document-knowledge-graph/build_graph.py withdraw --source-id <PDF_SHA256>
uv run python tools/document-knowledge-graph/build_graph.py restore --source-id <PDF_SHA256>
```

只读 API 为 `/document-graph/retrieve`、`/document-graph/status`、`/document-graph/assets/{source_id}/{asset_id}`。资源接口只提供有效手册的内容寻址资源，拒绝跨手册访问。损坏的文本/图片或图谱证据校验失败时返回不可用，不返回失配证据。

## Golden 与验证

[`document_multimodal_gold_v1.json`](../scripts/evaluation_data/document_multimodal_gold_v1.json) 包含 19 个目标和 4 个错误关联负例，字段解释见 [复核指南](../scripts/evaluation_data/document_multimodal_gold_guidelines_v1.md)。初标来自原始 PDF 和样页，标注先于预测，尚待人工复核；不根据预测补写答案。

```bash
uv run python tools/document-knowledge-graph/evaluate_pilot.py \
  --source-bundle data/document_graph_runs/my-pilot/source \
  --output data/document_graph_runs/my-pilot/extraction.json \
  --response-cache data/document_graph_runs/my-pilot/responses --workers 2

uv run python scripts/evaluate_document_graph_gold.py \
  --source-bundle data/document_graph_runs/my-pilot/source \
  --results data/document_graph_runs/my-pilot/extraction.json \
  --output data/document_graph_runs/my-pilot/comparison.json \
  --run-negatives --negative-results data/document_graph_runs/my-pilot/negatives.json \
  --response-cache data/document_graph_runs/my-pilot/responses
```

生成的同名 HTML 并排显示目标、实际事实、引用原文和原图。指标区分目标事实召回、正确关联目标召回、已匹配预测的证据关联精确率、模型及程序负例拒绝。未标注事实不计全量精确率或 F1；相同目标的错误重复预测也计入关联错误，不能只挑正确一条。

单元测试与隔离 Neo4j 测试使用合成模型响应验证存储、恢复、回读和篡改检测，不用于宣称模型质量。真实模型评测另存请求缓存、结果、对照报告与 usage。Neo4j 测试仅接受 `DOCUMENT_GRAPH_TEST_NEO4J_URI=bolt://localhost:45969`，会重置该隔离数据库，不能指向业务实例。

首轮范围限于两本固定手册的选定页，不包括扫描 PDF OCR、完整跨页表格恢复、所有资料自动归一、CAD/3D 或前端聊天接入。未标注事实仍需要抽样人工审查。
