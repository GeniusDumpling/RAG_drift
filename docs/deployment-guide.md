# 部署指南

**完全新装**：使用 [从零部署：Ubuntu 24.04 / WSL2](fresh-install.md)，从安装 Docker Engine 开始，每一步均给出命令与预期结果，不包含旧数据迁移。

按 [Linux / WSL 部署操作指南](linux-server-deployment.md) 逐步执行。每步包含命令与预期结果，覆盖：

1. 已有部署的只读检查（保护原数据）。
2. Linux / WSL 和 Docker 环境检查。
3. 部署账号、代码版本与配置。
4. HTTP_PROXY / HTTPS_PROXY 代理配置与 COLLECTOR_DATA_DIR 持久目录。
5. 镜像构建、应用启动及验收。
6. 四类采集任务与 systemd timer 安装。

正式任务统一由 `deploy/run-job.sh` 运行。底层使用 `docker compose -f docker-compose.deploy.yml run --rm jobs`，不要在 API 容器中长期运行采集。

已有数据的服务器不要套用新装命令初始化数据库；先执行操作指南第 0 步确认卷名、迁移版本与旧调度。
