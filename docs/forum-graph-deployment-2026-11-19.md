# 论坛独立图谱实例与前端切换

2026-11-19 已部署。现有图谱页面切换到论坛数据源，网页端口仍为 8000；数据库连接切换到论坛专用实例。没有修改四个原 Neo4j 实例的数据或安装新定时任务。

| 用途 | 当前配置 |
| --- | --- |
| 论坛图谱页面 | <http://localhost:8000/?view=VideoGraph>（保留既有页面路由，菜单为“论坛知识图谱”） |
| 论坛 Neo4j 容器 | `intelligence-rag-neo4j-forum` |
| Neo4j Browser | <http://localhost:7478> |
| 宿主机 Bolt | `bolt://localhost:7691` |
| Web 容器中的 Bolt | `bolt://neo4j-forum:7687`，独立 `rag-forum-graph_default` 网络 |
| 独立卷 | `rag-forum-graph_forum-graph-data`、`rag-forum-graph_forum-graph-logs` |
| 配置 | `.env.forum-graph`，权限 0600，已生成独立随机密码，不提交凭据 |
| 当前应用镜像 | `rag-forum-graph:20261119-v1` |

实例通过 [docker-compose.forum-graph.yml](../docker-compose.forum-graph.yml) 管理：

```bash
docker compose --env-file .env.forum-graph -f docker-compose.forum-graph.yml up -d
```

## 抽取固定 100 个论坛 item

在 `/home/gaowei/projects/RAG_drift` 目录执行：

```bash
.venv/bin/python tools/forum-knowledge-graph/build_graph.py \
  --env-file .env.forum-graph pipeline \
  --forums ardupilot px4 --limit 100 \
  --manifest data/forum_graph_runs/forum-20261119/batch-100.plan.json \
  --report data/forum_graph_runs/forum-20261119/batch-100.report.json \
  --response-cache data/forum_graph_runs/responses \
  --retry-failed
```

该命令已加 `--dry-run` 实际预检通过，固定计划包含 **PX4 60 个、ArduPilot 40 个**已完成文本加工的主题 item。一个 item 包含主帖及回复，不是单个楼层。计划有来源指纹、规则版本和目标实例绑定；重复执行该命令复用同一范围及有效检查点，不会自动扩大到另外 100 项。

本次只准备计划和预检，**没有执行这 100 项的模型抽取**。实际执行会按现有模型配置先判断相关性与价值，再抽取和审核；无价值、待补充或失败的主题不会被当成成功知识，因此不保证最终有 100 个主题入图。完成后点击页面“刷新图谱”查看新增结果。来源或规则变更使固定计划失效时，应创建新计划，不能手工篡改指纹。

通用 CLI 操作见 [论坛图谱使用说明](../tools/forum-knowledge-graph/README.md)。

## 当前展示的数据与实现

新实例先重放此前审核通过的两个公开主题：ArduPilot 132068、PX4 31268。没有再次调用模型。发布和独立回读均通过，现有 **2 个主题、6 个业务实体、5 条共享事实、31 个证据片段**；画布含业务实体及事实值节点，共 11 个知识节点、5 条连线。

前端使用 `/forum-graph/overview`、`/entities`、`/graph`、`/facts/{id}/evidence`。新增 [display.py](../backend/app/services/forum_graph/display.py) 只返回通过当前来源及原生/共享回读验证的发布结果，支持实体搜索、邻域、类型筛选、图谱数量限制和证据分页。状态、属性值、操作步骤以事实节点显示，不伪造为设备实体；论坛小案例不套用视频页面默认的“五节点主连通子图”限制。

证据面板显示作者、具体楼层、原文链接、案例条件、建议与验证反馈，替换视频字幕/时间点文案。旧视频 API 保留，四个原图谱库仍运行。

当前生产镜像早于论坛管线实现，后端以增量兼容方式部署：保留原视频模块，论坛依赖的纯辅助函数放在论坛私有兼容命名空间；增加论坛配置和路由，并允许画布事实节点。没有对现有视频模块进行整包覆盖。前端复用原工作台，构建时通过 `VITE_GRAPH_SOURCE=forum` 选择论坛；默认源码构建仍可用于视频展示。

## 验证和回滚

- 后端展示与客户端协议测试 **10 passed**，前端论坛/视频页面、工作台与二维画布测试 **23 passed**，TypeScript/Vite 构建与 Ruff 通过。
- 预览及正式服务均完成真实浏览器验证：11 个节点、5 条连线与 API 一致；具体楼层引用正确；1600 和 390 像素布局可操作，没有页面脚本错误；论坛页面仅请求论坛图谱接口。
- 构建过程中有视频采集任务持续更新，因此以原镜像临时对照服务进行同时刻验证，视频概览一致；五品牌目录响应哈希一致。没有暂停采集任务。
- 正式 Web 的原端口、原数据挂载及其他业务环境保持，新增论坛配置/网络；部署关闭自动数据库迁移。
- 临时预览及原镜像对照容器已清理；论坛实例健康，原应用回滚容器保留为停止状态。

发布快照、私有配置、回滚脚本、API/浏览器验证结果及截图保存在 `data/releases/forum-graph-20261119-v1/`。该目录包含凭据快照，权限 0700，不应公开。100 项计划和样例重放结果位于 `data/forum_graph_runs/forum-20261119/`。

原应用容器已停止并保留为 `intelligence-rag-app-before-forum-20261119`，可在项目根目录回滚应用：

```bash
.venv/bin/python data/releases/forum-graph-20261119-v1/deploy.py rollback
```

回滚恢复原镜像、网络和镜像配置项，不删除新论坛图谱的数据卷。数据库密码保存在本机 `.env.forum-graph`，不要通过发布记录或终端日志传播。
