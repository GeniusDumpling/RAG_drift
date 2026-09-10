# Docker 部署

完整命令与预期输出见 [Linux / WSL 部署操作指南](linux-server-deployment.md)。

- 第 1 步：Docker Engine / Docker Desktop 与 WSL 检查。
- 第 4 步：数据库密码、代理、API Key 和持久目录。
- 第 5 步：构建镜像，或导出并传输镜像。
- 第 6—7 步：启动容器、检查数据库、页面与任务依赖。
- 第 9 步：安装 systemd 调度（含 Docker Desktop 分支）。

已有数据库的服务器先执行第 0 步，不能直接使用新安装步骤覆盖旧配置或数据卷。
