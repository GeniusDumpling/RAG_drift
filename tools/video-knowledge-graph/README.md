# 视频知识图谱第一版

代码核对日期：2026 年 10 月 21 日。本文描述当前实现；历史部署记录不代表当前运行状态。

入口脚本：`tools/video-knowledge-graph/build_graph.py`。所有命令在项目根目录运行，读取现有 `.env` 配置。

第一版只处理 PostgreSQL 中 `item_type=video_description` 的记录，从 `content_chunks` 的 `transcript_segment` 抽取实体和关系，不依赖 Qdrant 是否索引成功。没有字幕时，仅从 `video_summary` 提取实体提及；没有这两种切片时记录空结果，不从未知文本类型推断关系。原始视频与切片保持原样。

## 图谱存储后端

默认 `VIDEO_GRAPH_BACKEND=postgres`；迁移后的 `neo4j` 后端将图谱、构图任务、租约及检查点全部保存在 Neo4j，PostgreSQL 仅提供源视频与字幕。下文 PostgreSQL 图谱表、SQL 领取锁及级联删除描述适用于旧后端；Neo4j 的部署、迁移校验、视频锁和删除对账语义见 [Neo4j 迁移指南](../../docs/neo4j-migration.md)。两个后端复用抽取和校验规则，命令及 API 保持兼容。

## 开始使用

先只读盘点，未迁移时也可运行，不调用模型、不写图谱表：

```bash
.venv/bin/python tools/video-knowledge-graph/build_graph.py inventory --limit 20
# run --dry-run 同样只做盘点
.venv/bin/python tools/video-knowledge-graph/build_graph.py run --dry-run --limit 20
```

确认连接指向目标 PostgreSQL 后，初始化新表：

```bash
.venv/bin/alembic upgrade head
```

真实抽取复用 `DEEPSEEK_API_KEY`、`DEEPSEEK_BASE_URL`、`DEEPSEEK_MODEL` 和 `AGENT_TIMEOUT_SECONDS`。每个有材料的视频需要一次主题判断；相关视频每个待抽取切片再调用一次模型（无字幕时为摘要切片）；有效检查点会复用。`--limit` 限制每轮处理的视频数，不限制单条视频的模型调用数。没有密钥时直接报错，不生成模拟关系。

视频主题判断和实体、关系抽取的 DeepSeek 请求均显式携带 `"thinking": {"type": "enabled"}` 和 `"reasoning_effort": "low"`，使用低强度思考模式，无需额外运行参数。继续执行或重试时，已有成功结果和切片检查点仍会复用。

先处理小批量：

```bash
.venv/bin/python tools/video-knowledge-graph/build_graph.py run --limit 20
# 或指定一个实际的视频 content_item_id；参数可重复传入
.venv/bin/python tools/video-knowledge-graph/build_graph.py run --content-id VIDEO_UUID --limit 1
```

`run` 会扫描符合范围的所有视频并排队，再最多处理 `--limit` 个任务。重复运行会跳过材料及抽取版本均未变化的成功结果。只排队、不调用模型：

```bash
.venv/bin/python tools/video-knowledge-graph/build_graph.py scan
```

失败和部分完成任务可显式重试；成功切片检查点经当前规则再次校验后复用，校验不通过的切片重新抽取。当前没有模型请求自动退避重试或校验反馈纠错循环。

失败输出保留原来的 `error` 类型，并增加 `error_detail`：包括失败阶段、具体规则代码、切片 ID 与从 1 开始的处理序号，以及有长度限制的相关实体或关系字段。数据库 `video_graph_builds.error_message` 保存可读原因，`output_json.last_error` 保存结构化详情；成功重试后移除旧错误详情。模型接口错误仅记录底层异常类型和 HTTP 状态，不保存响应全文、请求密钥或服务地址。此诊断记录不改变抽取及原文校验规则。

```bash
.venv/bin/python tools/video-knowledge-graph/build_graph.py run --retry-failed --limit 5
# 重新排队部分完成任务，补跑未通过切片
.venv/bin/python tools/video-knowledge-graph/build_graph.py run --retry-partial --limit 5
```

从迁移 `0006_video_graph_partial_success` 起，片段的结构、原文及语义校验失败会写入 `output_json.chunk_errors` 并跳过，继续处理后续片段。每条记录包含切片 ID、从 1 开始的序号、内容指纹、错误代码、尝试次数和时间。通过的片段保存在 `chunks`，`coverage` 记录总数、通过、跳过、待处理及覆盖率。

- 所有适用片段通过：`success`；部分通过且部分跳过：`partial_success`；所有适用片段校验失败：`failed`。合法的空抽取、不相关视频和无材料视频仍可正常完成。
- 首次构图的部分结果会激活，只有通过校验的片段贡献实体和关系。已有有效图谱时，新版本部分完成先保留旧版本，待全部通过后切换，避免覆盖更完整的图谱。
- 材料变化后，仅在抽取版本相同且片段文本、类型和时间戳指纹相同时复用旧成功片段，并再次校验。缺少旧指纹的历史结果不会跨材料版本自动复用；同一任务内的历史成功检查点仍可继续使用。
- 网络、模型接口／JSON 解析、数据库及租约故障不按内容校验失败忽略；运行失败时保留已有检查点及有效图谱。
- 默认 `run` 和 `watch` 不反复处理部分完成任务。`--retry-failed` 包含失败及部分完成任务，`--retry-partial` 仅重新排队部分完成任务；二者都复用仍通过当前校验的成功片段，只对其余片段调用模型。恢复后没有有效图谱的部分完成任务也会重新排队。
- 重试参数控制哪些已结束任务重新排队，领取任务时仍按队列顺序执行范围内的待处理任务。若只想处理选定视频，需同时指定 `--content-id`。
- `partial_success` 表示本轮处理已结束但材料覆盖不完整；`run` 的批次结果中有 `failed` 时返回退出码 1，部分完成本身不会导致非零退出码；命令初始化等异常也可返回 1，中断返回 130。自动化调用应同时检查输出状态和覆盖率。

前端总览显示“部分完成”数量，以及已记录覆盖率视频的切片通过、跳过和待处理数。`GET /video-graph/videos/{id}/status` 返回任务覆盖率、已发布覆盖率、跳过明细及是否保留旧图谱。旧任务没有覆盖率记录时不计入切片汇总。

部署需先应用迁移 `alembic upgrade head`。如需回退到不含本迁移的旧镜像，先使用新镜像执行 `alembic downgrade 0005_video_knowledge_graph`，将部分完成状态转换为失败并保留图谱、检查点与错误明细，再切换旧镜像。

持续增量处理：

```bash
.venv/bin/python tools/video-knowledge-graph/build_graph.py watch --interval 120 --limit 5
```

`watch` 不重复重试失败或部分完成任务；`--retry-failed` 和 `--retry-partial` 只在首次扫描应用。进程意外退出后，未完成任务在 5 分钟租约过期后可被下一次运行接管。相同命令可以由 cron 或 systemd 周期触发，无需引入额外队列服务；本次未安装定时任务。

改动实体规则、别名、提示词或抽取逻辑后递增版本：

```bash
.venv/bin/python tools/video-knowledge-graph/build_graph.py run --extractor-version video-graph-v2 --limit 20
```

当前默认规则版本仍为 `video-graph-v1`，新增语义规则没有自动升级该版本，因此不会自动重建历史成功任务。若需让历史图谱应用新规则，应显式提高版本，并用 `--content-id` 限定试跑范围；只设置 `--limit` 仍会扫描并排队整个范围的新版本任务。

模型名称也参与任务版本；切换同名模型背后的服务或配置时应手动递增 `--extractor-version`。

撤回一个视频在图谱中的贡献，保留源视频及历史构图记录：

```bash
.venv/bin/python tools/video-knowledge-graph/build_graph.py withdraw --content-id VIDEO_UUID
.venv/bin/python tools/video-knowledge-graph/build_graph.py restore --content-id VIDEO_UUID
.venv/bin/python tools/video-knowledge-graph/build_graph.py run --content-id VIDEO_UUID --limit 1
```

恢复后可复用相同材料和版本的检查点。物理删除源内容时，外键会删除其构图状态、任务和关系证据；其他视频对同一关系的证据继续有效。无有效证据的关系和别名可保留在库中，但不进入浏览与检索。

## 数据与增量语义

- 复用 `entities` 和 `content_entity_mentions`，新增 `video_graph_aliases`、`video_graph_relations`、`video_graph_evidence`、`video_graph_builds`、`video_graph_states` 五张表。
- 一个视频对应一个当前有效构图版本。模型调用不占用长数据库事务，所有适用切片处理结束后，按完整／部分完成策略在事务中发布结果；首次部分结果可激活，新版本部分完成保留已有有效图谱，失败也保留旧图谱。同一部分完成任务补跑发布时，不允许丢失此前已发布的切片。
- 材料指纹包含视频标识、标题、链接、摘要、字幕来源与语言、切片 ID、文本及时间戳，不依赖现有的摘要 `content_hash`。切片重新生成或材料变化都会触发更新。
- worker 使用 `FOR UPDATE SKIP LOCKED` 领取任务、租约 token 防止旧 worker 提交。提交时再次校验材料和期望构图版本，拒绝过期结果。
- 实体按类型、名称归一，使用稳定 ID；内置少量明确别名（如企业类型的“大疆”和 DJI）。不同型号、母子公司不自动归并，开放式实体消歧和人工归并尚未实现。
- 关系支持 `uses`、`contains`、`compatible_with`、`applied_to`、`reports_fault`、`mitigates`、`used_in`、`located_in`，保留版本、条件等限定字段。实体类型共 10 种：组织（`organization`）、产品、组件、软件、协议、技术、方法、故障、国家（`country`）、地区（`region`）。
- 每条关系证据保存原文、字符偏移、视频时间、字幕来源、抽取版本和 `asserted / uncertain / negated`。证据必须能在当前字幕切片中定位；这验证原文定位，不证明关系语义或现实事实正确。模型置信度表示抽取把握程度。
- 不相关视频记录主题判断结果并生成空投影。摘要提及置信度最多为 0.5，不生成任何关系。关键帧不参与第一版直接构图。

语义规则位于 [`video_graph_semantics.py`](../../backend/app/services/video_graph_semantics.py)：按关系类型限制主语和宾语类型，使用有限的明确地名词表防止把国家、地区归为产品或组织，不做地名子串推断。`used_in` 表示技术实体在国家／地区使用或部署，`located_in` 表示组织或地理实体的位置归属（不支持国家到国家）；两者的证据必须包含地理宾语原名。实体提及和关系证据定位只容忍空白差异，语义校验不代替事实核实。

轮询检测的是**数据库中已保存材料的变化**。现有视频采集器对相同 URL 会跳过，不会自动刷新远端字幕；若需跟踪 YouTube 内容变化，还需另外调整采集更新策略，并同步更新切片。只修改原始字幕而不更新构图输入切片，不会得到新的字幕关系。

## API 与前端

后端新增以下只读或检索接口：

| 接口 | 用途 |
|---|---|
| `GET /video-graph/overview` | 数据规模与任务状态 |
| `GET /video-graph/entities` | 活跃实体搜索、类型与视频筛选、分页 |
| `GET /video-graph/graph` | 全局或指定视频／实体的图谱，支持 1～2 跳；`full=true` 返回筛选范围内完整结果，否则应用节点／边上限 |
| `GET /video-graph/relations/{id}/evidence` | 当前有效字幕证据、视频时间链接、分页 |
| `GET /video-graph/videos/{id}/status` | 当前任务、主题判断、字幕完整度和来源变化状态 |
| `POST /video-graph/retrieve` | 实体别名匹配、一跳扩展、带编号的字幕证据上下文 |

`/retrieve` 请求示例：

```json
{"query":"PX4 使用哪些定位技术？","top_k":10,"max_chars":12000}
```

返回 `graph`、`evidence`、`context`。第一版只进行明确名称／别名匹配，没有向量实体链接、答案生成或社区摘要；现有 `/answer` 尚未自动融合图谱结果。没有匹配实体时返回空上下文，后续问答编排可继续原有向量与关键词检索。

前端“工作空间 → 视频知识图谱”统一浏览全部视频图谱，支持实体搜索、节点聚焦、1～2 跳扩展、关系状态筛选、关系证据分页和时间定位。图谱工作区使用页面全部内容宽度，不提供按视频浏览侧栏。使用现有 React 和 SVG 实现局部图谱，无新增前端依赖。开发时可通过前端开发服务器查看；生产部署需构建 `frontend/dist` 并同时加载后端图谱接口。

正式前端默认采用限量概览：桌面最多加载 300 个节点、500 条关系，手动展开并合并后的画布最多 500 个节点、1,000 条关系；移动端加载和展开均最多 150 个节点、300 条关系。搜索、筛选与聚焦遵守当前默认预算；返回全局视图恢复概览。Neo4j 保留完整图谱，其他内容可继续通过搜索查看，详见 [当前性能配置](../../docs/graph-ui-performance-2026-10-23.md)。

不传 `full=true` 时，API 默认最多 300 个节点、500 条连接，显式上限最大为 500 个节点、1,000 条连接。查询优先按字幕证据数量降序选取关系并成对保留端点，再用剩余额度展示孤立实体；画布数量与右侧全局统计采用不同范围。`full=true` 仍可供明确需要全量结果的接口调用使用，正式页面不发送该参数。

生产构建使用 `frontend/.env.production` 中的同源 API 地址，由 FastAPI 同时提供页面和接口，避免开发配置 `http://localhost:8000` 在其他访问地址下产生跨域或请求到错误服务。开发服务器仍可使用 `frontend/.env`。

历史部署记录：2026 年 10 月 13 日曾使用 `rag-video-graph-ui:20261013`，10 月 14 日记录的镜像为 `rag-video-graph-ui:20261014-all-videos`。当时的浏览器验证覆盖实体搜索、关系证据及视频时间链接；当前运行镜像和服务状态需另行核对。回退含部分完成状态的部署时，应遵循上文迁移兼容说明。

## 验证

```bash
# 本地纯逻辑测试；未指定隔离数据库时集成测试跳过
.venv/bin/pytest -q backend/tests/test_video_graph.py \
  backend/tests/test_video_graph_semantics.py backend/tests/test_video_graph_diagnostics.py

# 可选 PostgreSQL 集成测试：显式提供 VIDEO_GRAPH_TEST_DATABASE_URL
# 每项测试创建并清理独立的 video_graph_test_* schema，不重置已有业务表

cd frontend
npm test -- tests/video-graph-page.test.tsx
node node_modules/typescript/bin/tsc --noEmit --incremental false
```

测试使用模拟抽取结果，覆盖幂等、证据定位、版本变化、失败保留、检查点重试、租约接管、撤回恢复、多来源关系、部分完成发布与补跑、地理语义约束及脱敏诊断。本文更新时未重新执行测试或真实构图。真实模型抽取质量需在少量实际视频上检查后再扩大批次。
