# Docker 部署指南

本文档说明如何把 Intelligence RAG 原型打包成一个应用镜像，并在另一台单机服务器上通过 Docker Compose 启动应用、PostgreSQL 和 Qdrant。

## 1. 本地构建镜像

在项目根目录执行：

```bash
./scripts/docker_build.sh intelligence-rag-app:latest
```

也可以通过环境变量指定默认镜像名：

```bash
INTELLIGENCE_RAG_IMAGE=registry.example.com/intelligence-rag-app:demo ./scripts/docker_build.sh
```

## 2. 导出镜像 tar

```bash
./scripts/docker_save.sh intelligence-rag-app:latest
```

默认输出路径类似：

```text
dist/docker/intelligence-rag-app_latest.tar
```

也可以显式指定输出文件：

```bash
./scripts/docker_save.sh intelligence-rag-app:latest /tmp/intelligence-rag-app_latest.tar
```

## 3. 拷贝到目标服务器

示例：

```bash
scp dist/docker/intelligence-rag-app_latest.tar user@target-server:/tmp/
scp docker-compose.deploy.yml user@target-server:/opt/intelligence-rag/
```

目标服务器需要已安装 Docker 和 Docker Compose 插件。

## 4. 在目标服务器加载镜像

```bash
cd /opt/intelligence-rag
docker load -i /tmp/intelligence-rag-app_latest.tar
```

如果镜像名不是 `intelligence-rag-app:latest`，启动时设置 `INTELLIGENCE_RAG_IMAGE`：

```bash
export INTELLIGENCE_RAG_IMAGE=registry.example.com/intelligence-rag-app:demo
```

## 5. 启动服务

```bash
docker compose -f docker-compose.deploy.yml up -d
```

默认端口：

- Web/API: `http://服务器IP:8000`
- API 健康检查: `http://服务器IP:8000/health`

如需改宿主机端口：

```bash
APP_PORT=18000 docker compose -f docker-compose.deploy.yml up -d
```

## 6. 查看状态和日志

```bash
docker compose -f docker-compose.deploy.yml ps
docker compose -f docker-compose.deploy.yml logs -f app
```

健康检查：

```bash
curl -fsS http://127.0.0.1:8000/health
```

预期输出：

```json
{"status":"ok","service":"intelligence-rag-api"}
```

## 7. 播种演示数据

如果需要在部署环境中播种演示数据：

```bash
docker compose -f docker-compose.deploy.yml run --rm app python scripts/seed_demo.py
```

该命令会连接 Compose 内部的 PostgreSQL。

## 8. 执行一次 worker

处理指定 run：

```bash
docker compose -f docker-compose.deploy.yml run --rm app \
  python scripts/run_worker_once.py --json --require-success --run-id <RUN_ID>
```

如果只想执行默认的一次 worker 轮询：

```bash
docker compose -f docker-compose.deploy.yml run --rm app python scripts/run_worker_once.py
```

## 9. 停止服务

保留数据库和 Qdrant 数据卷：

```bash
docker compose -f docker-compose.deploy.yml down
```

同时删除数据卷：

```bash
docker compose -f docker-compose.deploy.yml down -v
```

删除数据卷会清空 PostgreSQL 和 Qdrant 数据。

## 10. 配置说明

部署 Compose 默认使用：

```text
DATABASE_URL=postgresql+psycopg://intelligence:intelligence@postgres:5432/intelligence_rag
SYNC_DATABASE_URL=postgresql+psycopg://intelligence:intelligence@postgres:5432/intelligence_rag
QDRANT_URL=http://qdrant:6333
EMBEDDING_PROVIDER=deterministic
EMBEDDING_MODEL=deterministic-hash-v1
LLM_PROVIDER=fake
```

默认 embedding 是 deterministic，适合可移植演示。若启用真实本地 embedding，需要重新构建包含 `local-embeddings` 依赖的镜像，并重建 Qdrant collection。

## 11. 注意事项

- `.env` 不会被打进镜像。
- 当前部署是单机演示形态，不包含 TLS、Nginx、多副本和高可用。
- PostgreSQL 是事实来源；Qdrant 是向量索引。
- 前端和 API 同源部署，前端默认使用相对路径请求 API。
