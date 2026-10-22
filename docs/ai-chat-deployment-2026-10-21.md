# AI 问答部署验收（2026-10-21）

## 发布结果

- Compose 项目：`rag_drift`。
- 配置：`docker-compose.deploy.yml` + `docker-compose.neo4j.yml`。
- 新镜像：`rag-ai-chat:20261021-v1`。
- 镜像 ID：`sha256:291d7b2ce6ea3bd0a49b1b9f87afe589be332e027db8d2e1518741dea3d318a2`。
- 原运行镜像：`rag-video-graph-neo4j:20261021`，已保留。
- `.env` 的 `INTELLIGENCE_RAG_IMAGE` 已更新，app 容器已重建且健康。
- 数据库已从 `0006_video_graph_partial_success` 升级到 `0007_chat_sessions`。

构建使用 `docker/Dockerfile.app-update`，以原运行镜像为依赖基础，重新安装当前后端包并重新构建前端。`pip check` 通过。PostgreSQL、Qdrant 和 Neo4j 容器未重启。

## 迁移前备份与验证

- PostgreSQL 自定义格式备份：`data/backups/pre-ai-chat-20261021-2210.dump`，48,165,000 字节，文件权限 `600`。
- 部署配置备份：`data/backups/pre-ai-chat-20261021-2210.env`，文件权限 `600`，包含敏感配置，不应提交或公开。
- 备份已恢复到独立数据库 `intelligence_rag_chat_verify_20261021_2210`，先在副本上成功执行迁移，再升级业务库。
- 迁移只新增 `chat_sessions`、`chat_turns` 及相关索引，不改动已有内容和向量数据。

## 线上验收

- `/health` 返回 `ok`，Docker 健康检查为 `healthy`。
- OpenAPI 已包含流式问答和会话管理接口。
- 当前前端资源为 `/assets/index-DjoBsp_A.js`，包含新流式接口及停止生成控件。
- 两轮真实请求调用 `deepseek-flash`，状态均为 `generated`，向量检索均成功召回 48 条候选，无基础设施故障降级。
- 第一轮收到 102 个增量事件，首个文本约 3.68 秒，完整响应约 4.14 秒；使用 8 条证据、9,335 字符上下文。
- 第二轮收到 80 个增量事件，首个文本约 5.54 秒，完整响应约 5.91 秒；使用 8 条证据、7,708 字符上下文，并成功结合历史改写检索问题。
- 第二轮对静态建筑拍摄的具体建议因本轮证据不足而明确拒答；此次验收确认流式协议、追问改写和保存行为，不代表全部检索场景的答案质量均已达标。
- 验收会话 `18baee2f-c4ed-4072-8d1b-acf626d992df` 已保存 2 条已完成问答。
- `/video-graph/overview` 正常返回 Neo4j 图谱数据。

查询优化器沿用原 app 的 `LLM_PROVIDER=fake` 配置；答案生成和追问改写独立使用已配置的真实 DeepSeek 客户端。

## 回退

如需回退应用，将 `.env` 中 `INTELLIGENCE_RAG_IMAGE` 改回 `rag-video-graph-neo4j:20261021`，然后执行：

```bash
docker compose -p rag_drift \
  -f docker-compose.deploy.yml -f docker-compose.neo4j.yml \
  up -d --no-deps --no-build --wait --wait-timeout 180 app
```

本次新增表与旧应用兼容，应用回退无需删除会话表。不要执行 `down -v` 或将整个备份直接覆盖到正在使用的业务库。
