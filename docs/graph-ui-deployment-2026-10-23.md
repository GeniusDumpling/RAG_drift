# 知识图谱页面正式部署（2026-10-23）

后续已发布支持连线悬停高亮的 v2，当前镜像与回退方法见 [连线高亮更新记录](graph-ui-edge-hover-2026-10-23.md)。以下保留 v1 首次部署记录。

经预览确认的 Obsidian 风格 AntV G6 页面已替换正式应用的视频知识图谱页面，包含悬停后亮度恢复修正。

## 访问与行为

- 正式入口：`http://localhost:8000/?view=VideoGraph`，远程访问时替换 localhost。
- 默认使用深色 G6 工作台，支持节点拖拽、双向邻居高亮、展开邻居、搜索、顶部导航及右侧统计和字幕证据面板。
- 正式构建禁用预览标记及“返回现有版本”链接，保留“经典视图”按钮，可切回原 SVG 画布。
- 悬停节点时无关节点保留 55% 透明度；移开后完全恢复。悬停连线不淡化全图。
- 图谱数据继续由现有后端从 Neo4j 读取，PostgreSQL 继续保存源内容。
- 临时预览容器已停止，后续使用正式端口。

## 镜像与部署范围

- 当前镜像：`rag-graph-obsidian:20261023-v1`。
- 镜像 ID：`sha256:2a9f51714e1a9028e0ef4642cb4425f45336dcae07a97c948f281bc224e8b9f4`。
- 上一镜像：`rag-ui-redesign:20261022-v1`，保留用于回退。
- `.env` 的 `INTELLIGENCE_RAG_IMAGE` 已更新，Compose 项目为 `rag_drift`。

通过 `docker/Dockerfile.frontend-update` 基于上一生产镜像构建，只替换 `/app/frontend/dist`。镜像内 167 个后端、任务脚本和配置文件摘要与部署前一致；仅重建 `app` 容器，Neo4j、PostgreSQL、Qdrant 容器 ID 均未改变。采集镜像配置保持原值。

前端生产构建命令：

```bash
VITE_G6_PREVIEW=false VITE_API_BASE_URL='' npm --prefix frontend run build
```

将产物复制到独立构建上下文的 `dist/` 后构建镜像；本次上下文保存在 `data/releases/graph-ui-20261023/build-context`：

```bash
docker build --build-arg APP_RUNTIME_IMAGE=rag-ui-redesign:20261022-v1 \
  -f docker/Dockerfile.frontend-update \
  -t rag-graph-obsidian:20261023-v1 \
  data/releases/graph-ui-20261023/build-context
docker compose --project-name rag_drift -f docker-compose.deploy.yml \
  up -d --no-deps --no-build --wait --wait-timeout 180 app
```

## 验证与回退

图谱组件测试 10 项、TypeScript 与生产构建通过。正式端口的 Chromium 验证通过：拖拽、邻居高亮、入链/出链筛选、展开保留原有节点、字幕证据、显示参数、搜索快捷键、导航、经典视图切换与移动端面板，未出现脚本异常。像素对比确认反复悬停后透明度完全恢复，悬停连线时全图亮度不变。`/health` 返回正常，应用容器为 `healthy`。

报告、镜像摘要和截图保存在 `data/releases/graph-ui-20261023/`。部署前配置备份为 `data/backups/graph-ui-20261023/before.env`，权限为 600，含敏感信息，不应公开。

仅切换画布可直接点击“经典视图”。需要恢复整个旧应用镜像时，在本机项目根目录执行：

```bash
bash data/releases/graph-ui-20261023/rollback.sh
```

该脚本将 `.env` 镜像配置恢复为上一版本，并仅重建应用容器；不会删除数据或回退数据库。若本地发布目录不可用，手动将 `.env` 中的 `INTELLIGENCE_RAG_IMAGE` 改为 `rag-ui-redesign:20261022-v1`，再执行上述 Compose 命令。

`scripts/g6-preview.sh rollback` 仅用于停止临时预览容器，不能回退此次正式发布。
