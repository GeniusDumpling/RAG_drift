# 视频知识图谱第一版

入口脚本：`tools/video-knowledge-graph/build_graph.py`。所有命令在项目根目录运行，读取现有 `.env` 配置。

第一版只处理 PostgreSQL 中 `item_type=video_description` 的记录，从 `content_chunks` 的 `transcript_segment` 抽取实体和关系，不依赖 Qdrant 是否索引成功。没有字幕时，仅从 `video_summary` 提取实体提及；没有这两种切片时记录空结果，不从未知文本类型推断关系。原始视频与切片保持原样。

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

真实抽取复用 `DEEPSEEK_API_KEY`、`DEEPSEEK_BASE_URL`、`DEEPSEEK_MODEL` 和 `AGENT_TIMEOUT_SECONDS`。每个有材料的视频需要一次主题判断；相关视频每个字幕切片再调用一次模型。`--limit` 限制每轮处理的视频数，不限制单条视频的模型调用数。没有密钥时直接报错，不生成模拟关系。

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

失败任务显式重试；成功切片检查点会复用：

```bash
.venv/bin/python tools/video-knowledge-graph/build_graph.py run --retry-failed --limit 5
```

持续增量处理：

```bash
.venv/bin/python tools/video-knowledge-graph/build_graph.py watch --interval 120 --limit 5
```

`watch` 不重复重试失败任务；`--retry-failed` 只在首次扫描应用。进程意外退出后，未完成任务在 5 分钟租约过期后可被下一次运行接管。相同命令可以由 cron 或 systemd 周期触发，无需引入额外队列服务；本次未安装定时任务。

改动实体规则、别名、提示词或抽取逻辑后递增版本：

```bash
.venv/bin/python tools/video-knowledge-graph/build_graph.py run --extractor-version video-graph-v2 --limit 20
```

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
- 一个视频对应一个当前有效构图版本。模型调用不占用长数据库事务，全部切片完成后才在事务中切换有效版本；失败不撤回上次成功结果。
- 材料指纹包含视频标识、标题、链接、摘要、字幕来源与语言、切片 ID、文本及时间戳，不依赖现有的摘要 `content_hash`。切片重新生成或材料变化都会触发更新。
- worker 使用 `FOR UPDATE SKIP LOCKED` 领取任务、租约 token 防止旧 worker 提交。提交时再次校验材料和期望构图版本，拒绝过期结果。
- 实体按类型、名称归一，使用稳定 ID；内置少量明确别名（如企业类型的“大疆”和 DJI）。不同型号、母子公司不自动归并，开放式实体消歧和人工归并尚未实现。
- 关系支持 `uses`、`contains`、`compatible_with`、`applied_to`、`reports_fault`、`mitigates`，保留版本、条件等限定字段。实体类型为企业、产品、组件、软件、协议、技术、方法、故障。
- 每条关系证据保存原文、字符偏移、视频时间、字幕来源、抽取版本和 `asserted / uncertain / negated`。证据必须能在当前字幕切片中定位；这验证原文定位，不证明关系语义或现实事实正确。模型置信度表示抽取把握程度。
- 不相关视频记录主题判断结果并生成空投影。摘要提及置信度最多为 0.5，不生成技术关系。关键帧不参与第一版直接构图。

轮询检测的是**数据库中已保存材料的变化**。现有视频采集器对相同 URL 会跳过，不会自动刷新远端字幕；若需跟踪 YouTube 内容变化，还需另外调整采集更新策略，并同步更新切片。只修改原始字幕而不更新构图输入切片，不会得到新的字幕关系。

## API 与前端

后端新增以下只读或检索接口：

| 接口 | 用途 |
|---|---|
| `GET /video-graph/overview` | 数据规模与任务状态 |
| `GET /video-graph/entities` | 活跃实体搜索、类型与视频筛选、分页 |
| `GET /video-graph/graph` | 全部或指定视频／实体的局部图谱，支持 1～2 跳与节点／边上限 |
| `GET /video-graph/relations/{id}/evidence` | 当前有效字幕证据、视频时间链接、分页 |
| `GET /video-graph/videos/{id}/status` | 当前任务、主题判断、字幕完整度和来源变化状态 |
| `POST /video-graph/retrieve` | 实体别名匹配、一跳扩展、带编号的字幕证据上下文 |

`/retrieve` 请求示例：

```json
{"query":"PX4 使用哪些定位技术？","top_k":10,"max_chars":12000}
```

返回 `graph`、`evidence`、`context`。第一版只进行明确名称／别名匹配，没有向量实体链接、答案生成或社区摘要；现有 `/answer` 尚未自动融合图谱结果。没有匹配实体时返回空上下文，后续问答编排可继续原有向量与关键词检索。

前端新增“工作空间 → 视频知识图谱”，支持视频筛选、实体搜索、节点聚焦、1～2 跳扩展、关系状态筛选、关系证据分页和时间定位。使用现有 React 和 SVG 实现局部图谱，无新增前端依赖。开发时可通过前端开发服务器查看；生产部署需构建 `frontend/dist` 并同时加载后端图谱接口。

生产构建使用 `frontend/.env.production` 中的同源 API 地址，由 FastAPI 同时提供页面和接口，避免开发配置 `http://localhost:8000` 在其他访问地址下产生跨域或请求到错误服务。开发服务器仍可使用 `frontend/.env`。

2026 年 10 月 13 日已完成生产前端构建，并将当前应用切换为本地图谱镜像 `rag-video-graph-ui:20261013`。访问 `http://localhost:8000`，在“工作空间”选择“视频知识图谱”；已有页面需刷新。真实浏览器已验证实体搜索、关系证据及视频时间链接。旧应用镜像保留为 `rag-video-graph-base:20261013`，如需回退，将 `.env` 的 `INTELLIGENCE_RAG_IMAGE` 改为该标签后重新启动 app 服务。

## 验证

```bash
# 本地纯逻辑测试；未指定隔离数据库时集成测试跳过
.venv/bin/pytest -q backend/tests/test_video_graph.py

# 可选 PostgreSQL 集成测试：显式提供 VIDEO_GRAPH_TEST_DATABASE_URL
# 每项测试创建并清理独立的 video_graph_test_* schema，不重置已有业务表

cd frontend
npm test -- tests/video-graph-page.test.tsx
node node_modules/typescript/bin/tsc --noEmit --incremental false
```

测试使用模拟抽取结果，覆盖幂等、证据定位、版本变化、失败保留、检查点重试、租约接管、撤回恢复和多来源关系。真实模型抽取质量需在少量实际视频上检查后再扩大批次。
