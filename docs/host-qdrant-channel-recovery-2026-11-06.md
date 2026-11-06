# 宿主机 Qdrant 通道恢复

2026-11-06 21:38 已恢复现有视频采集任务的宿主机向量库连接。

原因：任务默认访问 `http://localhost:6333`，部署 Qdrant 未发布宿主机端口；根目录
`.env` 配置了 HTTP/HTTPS 代理但缺少本机地址绕过规则。任务在 `ensure_collection()`
阶段收到 502，绕过代理直接连接则被拒绝。Qdrant 容器内部健康。

## 修改

- Compose 增加 `127.0.0.1:${QDRANT_HOST_PORT:-6333}:6333`，仅供本机访问。
- 本机 `.env` 明确设置 `QDRANT_URL=http://127.0.0.1:6333` 和 `QDRANT_HOST_PORT=6333`。
- 合并已有代理绕过项，并同时设置 `NO_PROXY` / `no_proxy`，包含 localhost、127.0.0.1、::1 及项目容器服务名。
- 仅重建 Qdrant，保留 `rag_drift_qdrant-data` 原数据卷和原镜像
  `sha256:241edb9d7778327516ef218f8c74e1bd61b5ea42cd4f193cb8d0896199705636`。
  app、PostgreSQL 容器 ID 保持不变。

配置备份和恢复前后集合元数据位于私有目录
`data/backups/qdrant-host-channel-20261106_213655/`。其中 `env.before` 为修改前环境文件，权限 0600。

## 校验结果

Compose 校验及 5 项部署契约测试通过。重建前后精确 point 数量、向量配置一致，全部集合为 green：

| 集合 | point 数量 | 向量维度 |
| --- | ---: | ---: |
| content_chunks_bge_m3_v1 | 73727 | 1024 |
| content_chunks_v2 | 98 | 512 |
| drone_document_chunks_bge_m3_v1 | 16240 | 1024 |

在真实 systemd 环境中加载项目 `.env`，执行视频采集的 `apply_proxy()`，并通过实际
`QdrantIndexer` 完成临时集合创建、向量写入和读取。测试结束已删除该临时集合。
生产集合可访问，Qdrant `/readyz`、应用 `/health` 和 `/supplier/brands` 均返回 200。

视频定时器保持启用，下次计划 2026-11-06 22:00（北京时间）。此次验证了实际客户端的
向量读写通道，未额外启动完整的视频搜索、下载、模型分析与入库批次。

后续若修改 `QDRANT_HOST_PORT`，需同步宿主机 `QDRANT_URL`，并重新应用 Compose 配置。
容器内 app/jobs 仍通过 `qdrant:6333` 访问向量库。
