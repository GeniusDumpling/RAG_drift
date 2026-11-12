# AI 问答提示词发布（2026-11-12）

本次发布工作区 `backend/app/services/answer_generation.py` 中的新系统提示词：先给结论、再给依据，按板卡/部件/用途组织短段落，并简洁说明证据范围和不确定性。

镜像为 `rag-ai-prompt:20261112-v1`，以发布前实际运行镜像 `sha256:4c8581714d4246792b53e4a3362414c2a3fa5d967e2bec379d8e6bb1f66aa18e` 为基础，仅更新已安装包及 `/app/backend` 下的回答生成模块。与运行版本的源码差异全部位于系统提示词中。工作区其他并行开发内容未打包入此次发布。

发布前 `test_answer_generation.py` 9 项测试通过，新镜像成功导入完整 app，并校验新提示词。运行容器再次校验模块 SHA256 为 `613a34f0beb8a5ea0bdf4e2a457f9ca42fbe2e8b738a1d4f26d95571d07fc8c8`。

实际运行配置保留 59 个环境变量、6 个挂载、端口绑定和两个网络。健康接口返回 200，7 个其他运行容器的 ID 和启动时间均未改变。`.env` 中 `INTELLIGENCE_RAG_IMAGE` 已更新。发布不增加数据库迁移。

正式 `/answer/stream` 验证问题“DJI Mavic 3 Pro 的主板芯片是什么型号？”成功，状态为 generated，返回 8 条证据和 593 字答案；首个文本片段约 24.49 秒，端到端约 27.22 秒。事件顺序为 retrieving → reranking → generating，图谱未接入本轮问答。此项为发布可用性检查，不构成延迟或答案质量对照评测。

发布快照、运行验证及回退配置保存在 `data/releases/ai-prompt-20261112-v1/`。其中配置文件包含原有连接凭据，目录权限为 700，配置权限为 600。

回退命令：

```bash
docker compose -p rag_drift \
  -f data/releases/ai-prompt-20261112-v1/rollback-compose.json \
  up -d --no-deps --no-build app
```

回退后应同步将 `.env` 的 `INTELLIGENCE_RAG_IMAGE` 改回原版本。
