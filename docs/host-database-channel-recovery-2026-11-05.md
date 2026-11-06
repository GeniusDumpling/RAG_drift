# 宿主机定时任务数据库通道恢复

2026-11-05 已恢复现有 WSL 用户级定时任务访问 PostgreSQL 的通道。

## 原因

当前 `rag-*.service` 仍直接执行宿主机 `.venv`，使用根目录 `.env` 中的
`DATABASE_URL` / `SYNC_DATABASE_URL`，目标为 `127.0.0.1:54329`。
部署 Compose 提供容器内 `postgres:5432`，但缺少宿主机端口映射，也没有独立转发服务。
因此数据库容器虽然健康，宿主机的供应商、视频和论坛任务仍会连接失败。
Parrot、Skydio 的部分抽取轮次因没有新增 URL，未进入数据库回写而成功退出。

## 修复

- `docker-compose.deploy.yml` 为 PostgreSQL 增加
  `127.0.0.1:${POSTGRES_HOST_PORT:-54329}:5432`。
- `.env.example` 增加 `POSTGRES_HOST_PORT=54329`，文档说明宿主机和容器的不同连接地址。
- 实际执行 `docker compose -f docker-compose.deploy.yml up -d --no-deps --no-build --pull never --wait --wait-timeout 60 postgres`。
- 仅重建 PostgreSQL 容器，复用原卷 `rag_drift_postgres-data`，数据库镜像 ID 保持
  `sha256:e013e867e712fec275706a6c51c966f0bb0c93cfa8f51000f85a15f9865a28cb`。
  app 容器 ID 保持不变。
- 映射仅绑定 loopback；容器内 app/jobs 继续连接 `postgres:5432`。

## 备份与数据核验

恢复前备份目录：

```text
data/backups/host-db-channel-20261105_212237/
```

目录权限 0700，数据库归档与角色备份权限 0600：

| 归档 | 大小（字节） |
| --- | ---: |
| intelligence_rag.dump | 336802976 |
| drone_documents.dump | 21073227 |
| literature_research.dump | 21281878 |

三个归档均通过 `pg_restore --list` 可读性检查；另有 `globals.sql`、恢复前后容器元数据。
供应商关系 99 条、验证审计 142 条，恢复前后数量一致。

## 实际验证结果

- Compose 配置校验通过，5 项部署契约测试通过。
- 在真实 systemd 用户环境中读取项目 `.env`，三个数据库均通过认证、SELECT、临时表 INSERT/UPDATE 与 ROLLBACK；未写入业务测试数据。
- 三个品牌的真实待验证目标加载函数执行成功，均为 0 条未验证记录。
- `/health`、`/supplier/brands` 返回 HTTP 200；app、PostgreSQL 健康检查通过。
- 21:26:29 补跑 DJI、Parrot、Skydio 的正式验证服务，三项均成功退出，日志为待验证 0/0、验证完成，未调用搜索或模型 API。
- 九个定时器均保持 `active`。视频、论坛、DJI 抽取未在此次额外完整补跑，其既有失败记录保留，后续按原计划运行。

若修改 `POSTGRES_HOST_PORT`，需同步宿主机数据库连接串并应用 Compose 配置；单独
`docker restart` 不会应用端口映射变化。
