# 新视频图谱容器与构建目标（2026-11-10）

新图谱使用独立Neo4j容器，源视频与字幕仍从原业务PostgreSQL读取。现有Web应用已切换到next图谱；原Neo4j容器与数据继续保留。

| 配置 | 原图谱 | 新图谱 |
|---|---|---|
| 容器 | intelligence-rag-neo4j | intelligence-rag-neo4j-next |
| 镜像 | neo4j:5.26-community | neo4j:5.26-community |
| Browser | http://127.0.0.1:7474 | http://127.0.0.1:7475 |
| Bolt | 127.0.0.1:7687 | 127.0.0.1:7688 |
| 业务数据库名 | neo4j | neo4j |
| 数据卷 | rag_drift_neo4j-data | rag-video-graph-next_neo4j-next-data |
| 日志卷 | rag_drift_neo4j-logs | rag-video-graph-next_neo4j-next-logs |

两个容器中的数据库虽然都叫neo4j，但端口、数据卷和内容互相独立。新库的连接配置为根目录 `.env.video-graph-next`，包含 `NEO4J_URI`、`NEO4J_USER`、`NEO4J_PASSWORD`、`NEO4J_DATABASE`。密码随机生成，文件权限600且被Git忽略；不在本文记录密码。

## 服务管理

```bash
docker compose --env-file .env.video-graph-next \
  -f docker-compose.neo4j-next.yml up -d neo4j-next

docker compose --env-file .env.video-graph-next \
  -f docker-compose.neo4j-next.yml ps
```

Compose使用独立项目名 `rag-video-graph-next`；不包含原应用或原图谱服务。新图谱内容只存新数据卷，容器重建后仍会保留。

## 构图与续跑

所有构图子命令增加 `--neo4j-env-file .env.video-graph-next`，从目标文件只读取Neo4j连接参数，不改全局Settings、PostgreSQL、模型或提示词。缺少目标文件或连接参数时直接报错，避免误写原库。

```bash
# 本次用户确认的单视频验证；相同命令可恢复同一任务
VIDEO_GRAPH_EXTRACTION_EFFORT=none VIDEO_GRAPH_REASONING_EFFORT=none \
.venv/bin/python tools/video-knowledge-graph/build_graph.py pipeline \
  --neo4j-env-file .env.video-graph-next \
  --content-id 2e66e70f-b4be-4036-8dc0-fbedd707af67 \
  --limit 1 --batch-size 1 \
  --manifest data/video_graph_runs/neo4j-next-pilot-20261110.plan.json \
  --report data/video_graph_runs/neo4j-next-pilot-20261110.report.json \
  --retry-failed
```

批量处理时去掉content-id，并使用新的清单与报告路径。例如 `--limit 100 --batch-size 5`。相同清单恢复时保持目标、来源和规则一致；成功视频及有效检查点可复用。批次大小表示排队批次，不是模型并发数。

不指定目标文件的构图命令仍连接 `.env` 配置的旧图谱；Web应用已单独切到next。因此继续构建Web正在展示的新图谱时，仍需显式传入 `--neo4j-env-file .env.video-graph-next`。新库Browser位于7475端口。

## 验证记录

已确认新旧容器数据卷不重叠；新库初始化了9个约束，构图前业务节点与连边均为0。相关CLI与管线检查10项通过，17项需独立数据库的集成检查跳过；随后已使用真实业务视频完成下述端到端验证。


真实验证视频：[Crashed? Here’s How to Replace DJI Mini Drone Propellers / 3 and 4 Pro](https://www.youtube.com/watch?v=XUoFNFt8Mng)，源ID `2e66e70f-b4be-4036-8dc0-fbedd707af67`，1个字幕切片。

- 首次构建：6次真实模型请求，7694个总tokens，推理none/none。
- 业务投影：4个实体、3条关系、4个原文提及、3条关系证据。
- 构建、完整回读、来源绑定和关系证据检索均通过；管线状态为success。
- 复用同一清单再次运行：already_current，0次模型请求，累计用量保持不变。
- 原库计数仍为21726个节点、31794条连线，原容器ID和原`.env`均保持不变。
- 新库物理计数为20个节点、28条连线，包含构建、别名、提及和证据等辅助节点；它不等于业务实体/关系数量。

[初次构建报告](../data/video_graph_runs/neo4j-next-pilot-initial-20261110.report.json) · [恢复后报告](../data/video_graph_runs/neo4j-next-pilot-20261110.report.json) · [目标与源视频盘点](../data/video_graph_runs/neo4j-next-setup-20261110.json) · [最终隔离与回读核验](../data/video_graph_runs/neo4j-next-verification-20261110.json)

可以在新库Browser执行下面的只读查询，查看当前有效视频贡献及关系原文：

```cypher
MATCH (v:VGVideo)-[:HAS_BUILD]->(b:VGBuild)
      -[:HAS_EVIDENCE]->(e:VGEvidence)-[:SUPPORTS]->(r:VGRelation)
WHERE v.active_build_id = b.id
  AND NOT coalesce(v.withdrawn, false)
  AND NOT coalesce(v.deleted, false)
MATCH (r)-[:SUBJECT]->(s:VGEntity), (r)-[:OBJECT]->(o:VGEntity)
RETURN s.name AS subject, r.predicate AS predicate, o.name AS object,
       e.quote AS quote, e.assertion AS assertion;
```

回读通过证明结果正确写入并可检索，不代表每条关系语义都经人工复核。


## Web应用已切换到next（2026-11-10）

Web容器 `intelligence-rag-app` 已重建并接入 `rag-video-graph-next_default` 网络，内部连接为 `bolt://neo4j-next:7687`。仍保留原 `rag_drift_default` 网络以访问PostgreSQL和Qdrant。

本次沿用 `rag-parrot-note:20261106-v1` 的原镜像，没有构建或发布工作区其他改动。除Neo4j URI和密码外，原应用的其他环境变量、数据挂载及8000端口均保持不变。根目录`.env`未改动。

实际生效配置：[当前Web运行快照](../data/releases/web-neo4j-next-20261110/runtime-compose.json)。该文件含连接凭据，权限600、父目录700，位于Git忽略的数据目录；不应公开或提交。

```bash
# 以后重建Web时继续使用这个运行快照，保持连接next
# 仅操作app；原PostgreSQL、Qdrant和Neo4j容器不重建
docker compose -p rag_drift \
  -f data/releases/web-neo4j-next-20261110/runtime-compose.json \
  up -d --no-deps --no-build --pull never app
```

如需回到原图谱，原发布配置仍保留在 `data/releases/parrot-note-20261106/runtime-compose.json`，以相同方式只重建app即可。

已验证 `/health`、图谱总览、完整图谱、视频状态、关系证据和图谱检索接口。总览显示1个有效构图视频、4个实体、3条关系及3条证据；图谱检索返回该视频的3条证据。总览中的视频总数仍来自原PostgreSQL，不代表全部视频已在next构图。

[切换计划](../data/releases/web-neo4j-next-20261110/switch-plan.json) · [Web切换验证结果](../data/releases/web-neo4j-next-20261110/switch-verification.json)

当前画布默认过滤小于5个节点的连通子图。本次新库仅4个实体，因此默认画布会隐藏该子图；已验证`min_component_size=0`可回读4节点、3连边，完整查询及证据检索正常。这是现有展示规则，不是切库失败；本次没有修改画布筛选策略。
