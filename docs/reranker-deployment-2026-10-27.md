# Reranker app 镜像部署（2026-10-27）

已将正式 app 从 `rag-graph-obsidian:20261026-v14` 更新为 `rag-reranker:20261027-v1`，健康检查通过。

- 镜像 ID：`sha256:aaba670f33bde632271e0772c460faf8a86111b1c2801ea5e1a0bc1aa57ff839`。
- 运行配置：SiliconFlow `BAAI/bge-reranker-v2-m3`，20 条候选全部返回排序，最多 8 条证据进入生成上下文，超时 5 秒。
- 构建使用 `docker/Dockerfile.app-update`，沿用 v14 运行依赖；重新安装项目包并构建前端，`pip check` 通过。保留 v14 图谱界面功能。
- 保留原 app 的端口、挂载、网络和既有环境变量，仅增加重排相关配置；`.env` 的镜像标签已更新。
- 主库版本为 `0008_literature_database_split`，文献库为 `0001_literature_schema`，部署前已与代码 head 一致，本次无新增迁移。

## 验证

先在绑定 `127.0.0.1:18081` 的独立临时容器验证，再通过 `up -d --no-deps --no-build --wait` 仅重建正式 app。临时容器和正式 app 均通过 `/health`、前端资源及真实 `/answer/stream` 测试：

- 状态按 `retrieving → reranking → generating` 返回，收到文本增量和 `done`。
- SiliconFlow 完整返回 20 条候选排序；选入 8 条证据，上下文 9,249 字符。
- 真实 `deepseek-flash` 生成完成，引用编号有效，没有触发降级。
- 正式验证一次重排耗时 732ms、全流程 6,812ms；这是单次连通性验证，不是性能基准。
- 验证创建的临时会话均已删除。临时容器验证后停止并移除。

部署前后逐一比对，PostgreSQL、Qdrant、Neo4j、pgAdmin 的容器 ID 与启动时间完全一致，均未重启。

宿主机文档批处理仍使用原主进程 PID `1221403`，状态为 `processing`。部署后检查时完成 427 项、图片处理中 2 项、待处理 424 项；任务继续推进。宿主机文档代码、Python 环境和数据目录未因部署被替换。

## 发布记录与回退

发布和验证记录保存在 `data/releases/reranker-20261027-v1/`：

- `preview-smoke.json`、`production-smoke.json`：真实问答验证结果。
- `verification.json`：镜像、健康、基础设施未重启和文档批处理检查。
- `runtime-compose.json`：本次正式运行配置。
- `rollback-compose.json`、`rollback.sh`：恢复 v14 的配置和脚本。
- `startup.log`：新 app 启动日志。

部署前备份保存在 `data/backups/reranker-20261027-v1/`。备份 `.env`、容器检查结果和 Compose JSON 含私有配置，文件权限为600，目录权限为700，不应公开。

需要回退时执行：

```bash
bash data/releases/reranker-20261027-v1/rollback.sh
```

回退只重建 app，并恢复 `.env` 中的镜像标签；保留其他最新环境配置。脚本会拒绝覆盖不同后续版本的镜像配置。

条数选择的测试依据见 [reranker 评测报告](reranker-evaluation-2026-10-27.md)。
