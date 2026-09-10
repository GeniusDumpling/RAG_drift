# Linux / WSL 部署操作指南

按顺序执行。本文使用 `/opt/intelligence-rag`、运行用户 `rag`、Web 端口 `8000`。命令中的 `<...>` 必须替换后再执行。预期输出是验收条件或示意，版本号、ID、时间以实际输出为准；不是已在你的服务器执行的结果。

> **已有采集数据的服务器先执行第 0 步，不要直接执行第 2 步重新部署。** 以下第 2—10 步用于新部署目录和新数据卷，不是旧数据库的原地升级命令。

## 0. 已有部署：确认旧环境，不改动数据

在旧项目目录执行：

```bash
pwd
git rev-parse HEAD
git status --short
docker ps --format 'table {{.Names}}\t{{.Image}}\t{{.Status}}'
docker compose ls
crontab -l
systemctl --user list-timers --all --no-pager
systemctl list-timers --all --no-pager
```

预期结果：可以确认旧版本（例如 `7bcee4cb5b6259b2c01ea73485e1814366cef09a`）、本地修改、Compose 项目名、数据库容器和采集任务。没有 crontab 时可能显示 `no crontab for ...`。

设置实际数据库容器名，仅查看挂载和迁移版本：

```bash
read -r -p 'PostgreSQL 容器名: ' PG_CONTAINER
read -r -p 'Qdrant 容器名: ' QDRANT_CONTAINER
docker inspect "$PG_CONTAINER" "$QDRANT_CONTAINER" \
  --format '{{.Name}} {{range .Mounts}}{{.Type}}:{{.Name}}:{{.Source}} -> {{.Destination}}; {{end}}'
docker exec "$PG_CONTAINER" psql -U intelligence -d intelligence_rag \
  -c 'SELECT version_num FROM alembic_version;'
```

预期结果：得到真实数据卷/目录和 Alembic revision；如果使用了其他数据库用户名或库名，替换 `intelligence` / `intelligence_rag`。

**到此停止新装流程。** 先备份并在独立数据副本上验证迁移，再制定与这些真实卷名匹配的升级配置。不要覆盖旧 `.env`、更改项目名后直接启动、生成新密码套用到旧数据库，或执行 `down -v` / `volume prune`。当前新版应用启动会自动迁移数据库；不能将尚未验收的新版直接连接原库。

## 1. 检查目标机器

在目标 Linux/WSL 终端执行：

```bash
ps -p 1 -o comm=
docker --version
docker compose version
docker info --format '{{.OSType}}'
command -v git curl openssl flock timeout
systemctl show docker.service -p LoadState --value
```

预期结果：

- 第一条输出 `systemd`。
- Docker 和 Compose 输出版本；Compose 支持 `up --wait`。
- Docker 类型输出 `linux`。
- 工具检查逐行输出可执行路径。
- 最后一条若输出 `loaded`，第 9 步使用标准模板；若是 `not-found` 且 Docker 可用，通常属于 Docker Desktop 接入，按第 9 步 WSL 分支修改副本。

若 Docker 未安装，先按 https://docs.docker.com/engine/install/ 对应发行版说明安装 Engine 和 Compose 插件，或在 Windows Docker Desktop 中启用该 WSL 发行版的集成，然后重新执行本步。

若 WSL 的 PID 1 不是 systemd：

```bash
sudoedit /etc/wsl.conf
```

在文件已有配置基础上加入（已有 `[boot]` 时合并，不要重复）：

```ini
[boot]
systemd=true
```

在 Windows PowerShell 执行：

```powershell
wsl --shutdown
```

重新打开 WSL，执行 `ps -p 1 -o comm=`，预期为 `systemd`。`wsl --shutdown` 会停止该机器的 WSL 工作负载；已有部署不能未经维护安排执行。

## 2. 创建部署账号和目录（仅新安装）

以有 sudo 权限的管理员执行：

```bash
id rag >/dev/null 2>&1 || sudo useradd --create-home --shell /bin/bash rag
getent group docker >/dev/null && sudo usermod -aG docker rag
sudo install -d -o rag -g rag /opt/intelligence-rag
sudo -iu rag
cd /opt/intelligence-rag
whoami
docker info --format '{{.OSType}}'
```

预期输出：

```text
rag
linux
```

没有 `linux` 或提示 Docker socket 权限错误时停止，先修复 rag 用户的 Docker 访问权限。后续普通命令均以 rag 执行；带 sudo 的命令由管理员执行。docker 组具有高权限，仅添加可信账号。

## 3. 获取已发布代码

在 rag 终端执行：

```bash
cd /opt/intelligence-rag
git clone https://github.com/GeniusDumpling/RAG_drift.git .
git fetch origin
read -r -p '输入包含部署改造的发布 commit 或 tag: ' RELEASE_REF
git checkout --detach "$RELEASE_REF"
test -f deploy/run-job.sh && test -f deploy/server.env.example && printf 'deployment files OK\n'
git rev-parse HEAD
```

预期输出：`deployment files OK` 和指定发布版本的完整 commit ID。

若文件检查失败，停止：该版本未包含部署文件。开发机尚未提交/推送的改动不会通过 clone 获取。不要用旧版本继续后面的步骤。

## 4. 填写目标机器配置

```bash
cd /opt/intelligence-rag
test ! -e .env && cp deploy/server.env.example .env
chmod 600 .env
openssl rand -hex 32
nano .env
```

预期：生成一串随机十六进制密码；编辑器打开配置文件。命令不覆盖已存在的 `.env`。

填写以下值；不要将密钥提交到 Git：

```dotenv
POSTGRES_PASSWORD=<刚生成的密码，仅供新数据库使用>
INTELLIGENCE_RAG_IMAGE=intelligence-rag-app:<本次发布标签>
COMPOSE_PROJECT_NAME=intelligence-rag
APP_BIND_ADDRESS=127.0.0.1
APP_PORT=8000
COLLECTOR_DATA_DIR=./data/collector
MODEL_CACHE_DIR=./model-cache/huggingface
HTTP_PROXY=
HTTPS_PROXY=
YOUTUBE_API_KEY=<YouTube搜索Key>
VLM_API_KEY=<视频摘要Key>
EMBEDDING_API_KEY=<向量Key，可留空复用VLM_API_KEY>
DEEPSEEK_API_KEY=<问答与供应商分析Key>
BAIDU_QIANFAN_API_KEY=<供应商搜索Key>
```

保留模板中的模型、API 地址和 collection 配置，除非你的服务使用其他地址。

代理配置二选一：

- 直连：`HTTP_PROXY`、`HTTPS_PROXY` 均留空。
- 代理：两项填同一个可达地址，例如 `http://host.docker.internal:7897` 或实际远端代理地址。端口按目标机器修改。

容器内 `127.0.0.1` 不是宿主机。宿主代理须允许 Docker 网桥访问，不能只监听宿主 loopback；不要将代理开放到公网。WSL/Docker Desktop 下以容器实测可达地址为准。

校验配置（不打印密钥）：

```bash
unset HTTP_PROXY HTTPS_PROXY http_proxy https_proxy ALL_PROXY all_proxy
docker compose -f docker-compose.deploy.yml --profile jobs config --quiet && printf 'compose config OK\n'
```

预期输出：`compose config OK`。如果提示缺少 POSTGRES_PASSWORD，返回编辑 `.env`。

## 5. 创建目录和构建镜像

以下目录对应第 4 步的默认值。若修改 COLLECTOR_DATA_DIR/MODEL_CACHE_DIR，则相应修改目录创建命令。

```bash
mkdir -p data/collector/{video-downloads,video-state,whisper-cache,supplier,supplier-logs}
mkdir -p model-cache/huggingface
docker compose -f docker-compose.deploy.yml build app
```

预期结果：构建退出码为 0，末尾包含镜像导出/命名成功信息。首次需下载较多依赖。

确认镜像：

```bash
docker compose -f docker-compose.deploy.yml run --rm --no-deps jobs python --version
```

预期输出：`Python 3.12.x`。此命令只检查 Python，不启动数据库或执行采集。

若目标机无法构建，在同架构、可联网机器用同一发布代码构建后导出：

```bash
docker save -o intelligence-rag-app.tar intelligence-rag-app:<本次发布标签>
scp intelligence-rag-app.tar <目标用户>@<目标服务器>:/tmp/
```

目标机执行：

```bash
docker load -i /tmp/intelligence-rag-app.tar
```

预期输出：`Loaded image: intelligence-rag-app:<本次发布标签>`。`.env` 的镜像名必须匹配。若目标机连基础镜像也无法下载，同时传输 `postgres:16-alpine`、`qdrant/qdrant:v1.12.4`。

## 6. 启动应用与数据库

```bash
docker compose -f docker-compose.deploy.yml up -d --no-build --wait --wait-timeout 180 app postgres qdrant
docker compose -f docker-compose.deploy.yml ps
```

预期结果：app 与 postgres 为 healthy，qdrant 为 running；启动命令退出码为 0。新数据库由应用启动时执行迁移。

检查数据库版本：

```bash
docker compose -f docker-compose.deploy.yml exec -T postgres \
  psql -U intelligence -d intelligence_rag -Atc 'SELECT version_num FROM alembic_version;'
```

当前版本预期输出：

```text
0004_supplier_relation_integrity
```

若以后发布新增迁移，以该发布版本 migration head 为准。

## 7. 检查页面、依赖和网络

```bash
curl -fsS http://127.0.0.1:8000/health
curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:8000/
bash deploy/run-job.sh video-collect --validate
```

预期结果：

- 健康接口返回 JSON，`status` 为 `ok`。
- 首页返回 `200`。
- 最后一条输出 `Job dependencies and Python syntax OK (no network or ingestion)`。

检查容器能否访问外网，不使用 API Key：

```bash
docker compose -f docker-compose.deploy.yml run --rm --no-deps jobs \
  python -c 'import requests; r=requests.get("https://www.youtube.com/robots.txt", timeout=30); print("HTTP", r.status_code); r.raise_for_status()'
```

预期输出 `HTTP 200` 且退出码为 0。超时或代理连接失败时先修改第 4 步配置，再重试。此检查不代表 YouTube 字幕/视频流一定可下载。

从另一台电脑访问，保持默认 loopback 绑定并建立 SSH 隧道：

```bash
ssh -N -L 18000:127.0.0.1:8000 <SSH用户>@<服务器地址>
```

预期：终端保持连接、通常无输出；在客户端浏览器打开 `http://127.0.0.1:18000`，显示项目页面。服务器需已有可达的 SSH 服务；WSL 按其实际 SSH 网络入口连接。不要未经认证防护直接把 API 暴露到公网。

## 8. 手工验证采集任务（会调用 API 并写入数据）

按需要逐个执行，不需要的任务可以不启用：

```bash
bash deploy/run-job.sh video-collect
printf 'video exit=%s\n' "$?"
bash deploy/run-job.sh discourse-collect
printf 'forum exit=%s\n' "$?"
bash deploy/run-job.sh supplier-pipeline
printf 'supplier exit=%s\n' "$?"
bash deploy/run-job.sh supplier-verify
printf 'verify exit=%s\n' "$?"
```

预期结果：

| 任务 | 验收条件 |
| --- | --- |
| 视频 | 输出本轮结果，按 success、duplicate、字幕/流失败等实际分类查看。新增成功数量可能为 0，不能当作“已入库成功”。 |
| 论坛 | 输出采集/去重/入库结果，未出现未处理异常。 |
| 供应商抽取 | 日志阶段完成，未出现 `status=failed`；无新 URL 时可能跳过分析。 |
| 供应商验证 | 完成验证，或报告没有待验证记录。 |

各任务 `exit=0` 只证明进程正常退出；同时检查实际业务输出。失败时先处理配置、模型或网络问题，不要直接启用定时器。

检查持久目录：

```bash
find data/collector -maxdepth 3 -type f -printf '%P\n'
```

预期：有成功任务时出现对应关键帧、轮换状态或供应商产物；纯去重或失败可能没有新文件。不要求每个目录都有数据。

## 9. 安装定时调度

### 9.1 确认没有重复调度

以原采集用户执行：

```bash
crontab -l
systemctl --user list-timers --all --no-pager
systemctl list-timers --all --no-pager
```

预期：没有正在调度同样四类任务的旧条目。如果存在，先只停用对应采集条目；不要删除其他 cron 任务。

### 9.2 准备模板

回到管理员终端：

```bash
STAGING=$(mktemp -d)
cp /opt/intelligence-rag/deploy/systemd/intelligence-rag-* "$STAGING/"
systemctl show docker.service -p LoadState --value
```

预期最后输出 `loaded` 或 `not-found`。

- `loaded`：直接执行 9.3。
- WSL + Docker Desktop 且为 `not-found`：先执行以下命令，只修改临时副本：

```bash
for file in "$STAGING"/*.service; do
  sed -i '/^Requires=docker.service$/d; s/^After=docker.service network-online.target$/After=network-online.target/' "$file"
done
sudo -iu rag docker info --format '{{.OSType}}'
```

预期为 `linux`。不是 `linux` 时停止；必须先启动 Docker Desktop、启用 WSL 集成并解决 rag 账号访问。此分支不会自动启动 Windows Docker Desktop。

### 9.3 安装并校验

```bash
sudo cp "$STAGING"/*.service "$STAGING"/*.timer /etc/systemd/system/
sudo systemd-analyze verify /etc/systemd/system/intelligence-rag-*.service /etc/systemd/system/intelligence-rag-*.timer
sudo systemctl daemon-reload
```

预期：verify 无错误，daemon-reload 无输出。不要忽略 `Unit docker.service not found` 或执行路径不存在等错误。

### 9.4 启用四个 timer

```bash
sudo systemctl enable --now \
  intelligence-rag-video-collect.timer \
  intelligence-rag-discourse-collect.timer \
  intelligence-rag-supplier-pipeline.timer \
  intelligence-rag-supplier-verify.timer
systemctl list-timers 'intelligence-rag-*' --no-pager
```

预期：显示四个 timer，各有下一次执行时间。默认按服务器本地时区：视频双数小时 :00、供应商抽取双数小时 :15、论坛双数小时 :30、供应商验证奇数小时 :45。

WSL 停止或 Windows 睡眠期间不会采集；恢复后 Persistent timer 可以补触发，但不保证 Docker Desktop 已就绪。

## 10. 最终验收

管理员执行一次 service（会再次触发真实采集）：

```bash
sudo systemctl start intelligence-rag-video-collect.service
systemctl show intelligence-rag-video-collect.service -p Result -p ExecMainStatus
sudo journalctl -u intelligence-rag-video-collect.service -n 50 --no-pager
systemctl is-enabled intelligence-rag-video-collect.timer
systemctl is-active intelligence-rag-video-collect.timer
```

正常预期：

```text
Result=success
ExecMainStatus=0
enabled
active
```

同时查看日志中的实际采集结果；锁冲突跳过也可能退出 0。oneshot service 结束后为 inactive 属正常现象，应保持 active 的是 timer。

至此部署完成。保留 `.env`、`data/collector`、模型缓存和数据库数据卷；不要执行 `docker compose down -v`。
