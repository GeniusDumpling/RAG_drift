# 对话意图与上下文修复部署（2026-10-29）

正式 app 已从 `rag-drone-brands:20261028-v1` 更新为 `rag-chat-context:20261029-v1`，健康检查通过。

- 镜像 ID：`sha256:f17e46ead0c5e0c5760911f0f90805265a1e80adf13c860ab59cdf89eccd7015`。
- 以原运行镜像为基础，使用 `docker/Dockerfile.app-update` 重新安装后端并构建前端；`pip check` 和前端构建通过。
- 保留原 app 的63项环境配置、5个挂载、端口和网络配置；`.env` 仅更新镜像标签。
- 主库、文献库已分别位于 `0009_supplier_multibrand`、`0001_literature_schema`，本次没有新增迁移。

## 上线行为

- 完整的寒暄、致谢等普通对话跳过历史读取、追问改写、Embedding、检索与 reranker；前端显示“对话回复”。
- 专业问题继续使用本轮相关证据，正常追问保留上下文，明确换话题时清除旧历史的影响。
- 模型故障时，普通对话返回简短本地回复，不再显示“证据不足”或“证据摘录”。

代码阶段已通过52项后端、15项前端相关测试及类型/格式检查。发布先在独立临时容器 `127.0.0.1:18082` 验证，再用 `up -d --no-deps --no-build --wait` 仅更新正式 app。

临时容器真实验证四项：

1. 问 Mavic 3 Pro 焦段，正常检索、重排并生成引用。
2. 接着说“你好”，仅返回 `generating`，证据数和上下文字符均为0，不使用历史，也不出现旧型号或引用。
3. 接着问“它的摄像头有哪些焦段”，能跳过寒暄承接 Mavic 话题，重新检索本轮证据。
4. 明确换到 PX4 话题，不带入 Mavic 历史，证据不足的具体内容会如实说明。

正式服务再次验证了知识问答和同会话寒暄；前端资源包含新的对话标签，流式输出和历史读取均正常。验证会话均已删除，临时容器已停止并移除。

部署前后比对 PostgreSQL、Qdrant、Neo4j、pgAdmin 的容器 ID 与启动时间，全部一致。部署前未发现正在运行的文档批处理主进程，本次未启动或停止宿主机文档任务。

## 记录与回退

发布记录位于 `data/releases/chat-context-20261029-v1/`：

- `preview-smoke.json`、`production-smoke.json`：真实模型与 SSE 验证。
- `verification.json`：镜像、健康及其他容器未重启的记录。
- `source-sha256.json`：相关源码指纹。
- `runtime-compose.json`、`rollback-compose.json`：上线与回退配置。
- `smoke.py`、`rollback.sh`：验证和回退脚本。

部署前备份位于 `data/backups/chat-context-20261029-v1/`。其中 `.env`、容器详情和 Compose JSON 含私有配置，目录权限700、配置文件权限600，不应公开。

回退命令：

```bash
bash data/releases/chat-context-20261029-v1/rollback.sh
```

脚本只恢复 app 及 `.env` 的镜像标签，保留其他最新配置；若已配置不同的后续镜像，会拒绝覆盖。
