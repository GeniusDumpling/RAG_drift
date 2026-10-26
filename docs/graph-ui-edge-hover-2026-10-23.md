# 图谱连线悬停高亮更新（2026-10-23）

后续已发布全量展示 v3，当前镜像及回退方法见 [全量图谱记录](graph-ui-full-2026-10-23.md)。以下保留 v2 部署记录。

正式图谱现在支持悬停连线：当前连线变为亮紫色、加粗并显示关系名称，提示框继续显示证据数；其他节点和连线不淡化。移开光标后恢复原样，点击仍可打开字幕证据。节点悬停的一跳邻居高亮保持原有行为。

- 入口：`http://localhost:8000/?view=VideoGraph`。
- 当前镜像：`rag-graph-obsidian:20261023-v2`。
- 镜像 ID：`sha256:41f951dce7bef737cbdab09b6db7fbe1f608a067014fd8445bddd6e2969e3837`。
- 上一镜像：`rag-graph-obsidian:20261023-v1`，保留用于回退。

构建沿用 `docker/Dockerfile.frontend-update`，基于 v1 镜像仅替换生产前端资源。`.env` 镜像配置更新为 v2，只重建应用容器，数据库和采集配置保持原状。

10 项图谱测试与生产构建通过。正式页面浏览器验证了连线高亮、反复移入移出后恢复、其他节点不变暗、点击连线打开证据，以及节点悬停后亮度恢复。浏览器报告和截图位于 `data/releases/graph-ui-edge-hover-20261023/`，配置备份位于 `data/backups/graph-ui-edge-hover-20261023/before.env`（含敏感信息，权限 600）。

回退到 v1：

```bash
bash data/releases/graph-ui-edge-hover-20261023/rollback.sh
```

该命令修改 `.env` 镜像配置，并仅重建应用容器。若本地发布目录不可用，手动将 `INTELLIGENCE_RAG_IMAGE` 改为 `rag-graph-obsidian:20261023-v1` 后执行：

```bash
docker compose --project-name rag_drift -f docker-compose.deploy.yml \
  up -d --no-deps --no-build --wait --wait-timeout 180 app
```
