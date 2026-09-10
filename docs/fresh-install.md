# 从零部署：Ubuntu 24.04 / WSL2

适用：没有本项目旧部署、旧数据库和采集数据的新环境。采用 **Ubuntu 内安装 Docker Engine**，不使用 Windows Docker Desktop。宿主机不需要安装项目 Python、uv 或 ffmpeg。

文中“预期结果”为验收条件，版本号、时间和容器 ID 随机器变化，不是目标机执行记录。任一步失败即停止，不跳过继续执行。所有 `<...>` 必须替换。

## 1. 准备 Ubuntu

### 1.1 普通 Linux 服务器

以能使用 sudo 的账号登录 Ubuntu 24.04，直接执行第 1.3 步。

### 1.2 Windows 新装 WSL2

在管理员 PowerShell 执行：

```powershell
wsl --install -d Ubuntu-24.04
```

预期：安装发行版，必要时提示重启 Windows。首次进入 Ubuntu 时按提示创建 Linux 用户和密码。

检查：

```powershell
wsl --list --verbose
```

预期：列表出现 `Ubuntu-24.04`，`VERSION` 为 `2`。

进入 Ubuntu：

```powershell
wsl -d Ubuntu-24.04
```

预期：进入 Linux shell。后文命令除明确注明 PowerShell 外，均在 Ubuntu 中执行。

### 1.3 检查系统与 systemd

```bash
cat /etc/os-release
ps -p 1 -o comm=
```

预期：系统为 Ubuntu 24.04，第二条输出 `systemd`。

如果 WSL 中不是 systemd：

```bash
sudo nano /etc/wsl.conf
```

在已有文件中合并以下配置，不要重复添加 `[boot]`：

```ini
[boot]
systemd=true
```

退出 Ubuntu，在 PowerShell 执行：

```powershell
wsl --shutdown
wsl -d Ubuntu-24.04
```

重新执行 `ps -p 1 -o comm=`，预期为 `systemd`。注意 `wsl --shutdown` 会停止这台 Windows 上所有运行中的 WSL 发行版。

## 2. 安装 Docker Engine 与基础工具

> 如果这台机器已使用 Docker Desktop 的 WSL 集成，不执行本步骤安装第二套 Docker；请使用另一份 [Linux / WSL 部署指南](linux-server-deployment.md) 的 Docker Desktop 分支。

```bash
sudo apt-get update
sudo apt-get install -y ca-certificates curl git openssl nano util-linux coreutils
sudo install -m 0755 -d /etc/apt/keyrings
sudo curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
sudo chmod a+r /etc/apt/keyrings/docker.asc
printf '%s\n' "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/ubuntu noble stable" | sudo tee /etc/apt/sources.list.d/docker.list >/dev/null
sudo apt-get update
sudo apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
sudo systemctl enable --now docker
```

预期：各命令正常结束，无仓库签名/网络错误，Docker 服务启动。此源配置只适用于本文指定的 Ubuntu 24.04。

检查：

```bash
sudo docker --version
sudo docker compose version
systemctl is-active docker
sudo docker run --rm hello-world
```

预期：输出 Docker/Compose 版本，服务状态为 `active`，最后出现 `Hello from Docker!`。

如果拉取镜像失败，先解决服务器访问 Docker Hub 的网络问题。后续项目 `.env` 中的代理只作用于任务容器，不会自动配置 Docker daemon 的镜像拉取代理。

## 3. 创建运行账号与部署目录

在管理员 Ubuntu 终端执行：

```bash
id rag >/dev/null 2>&1 || sudo useradd --create-home --shell /bin/bash rag
sudo usermod -aG docker rag
sudo install -d -o rag -g rag /opt/intelligence-rag
sudo -iu rag
```

预期：进入 `rag` 用户 shell。`rag` 不需要 sudo 权限；后面的管理步骤返回原管理员终端执行。docker 组具有高权限，只授予可信运行账号。

在 rag shell 检查：

```bash
whoami
docker info --format '{{.OSType}}'
```

预期输出：

```text
rag
linux
```

如果 Docker 提示 permission denied，退出 rag shell 后重新执行 `sudo -iu rag` 再检查。

## 4. 下载包含部署文件的发布版本

仍以 rag 执行：

```bash
cd /opt/intelligence-rag
git clone https://github.com/GeniusDumpling/RAG_drift.git .
git fetch origin
read -r -p '输入发布 commit 或 tag: ' RELEASE_REF
git checkout --detach "$RELEASE_REF"
test -f deploy/run-job.sh && test -f deploy/server.env.example && printf 'deployment files OK\n'
git rev-parse HEAD
```

预期：出现 `deployment files OK`，最后打印选定版本的完整 commit ID。

必须使用包含本次部署改造的已发布版本。开发机未提交/推送的文件不会通过 clone 获取。若缺少文件，停止并先发布代码，不能拿旧版本继续执行。

## 5. 配置数据库、模型与代理

### 5.1 创建配置

```bash
cd /opt/intelligence-rag
test ! -e .env && cp deploy/server.env.example .env
chmod 600 .env
openssl rand -hex 32
nano .env
```

预期：随机命令输出一串十六进制密码，随后编辑器打开 `.env`。将密码填入 POSTGRES_PASSWORD；配置文件已存在时不会被复制覆盖。

至少填写/确认：

```dotenv
POSTGRES_PASSWORD=<刚生成的随机密码>
INTELLIGENCE_RAG_IMAGE=intelligence-rag-app:server-v1
COMPOSE_PROJECT_NAME=intelligence-rag
APP_BIND_ADDRESS=127.0.0.1
APP_PORT=8000
COLLECTOR_DATA_DIR=./data/collector
MODEL_CACHE_DIR=./model-cache/huggingface
HTTP_PROXY=
HTTPS_PROXY=
YOUTUBE_API_KEY=<视频搜索Key>
VLM_API_KEY=<视频摘要Key>
EMBEDDING_API_KEY=<向量模型Key>
DEEPSEEK_API_KEY=<问答及供应商分析Key>
BAIDU_QIANFAN_API_KEY=<供应商搜索Key>
```

保留模板里的 VLM_BASE_URL、VLM_MODEL、EMBEDDING_BASE_URL、DEEPSEEK_BASE_URL、DEEPSEEK_MODEL、QDRANT_COLLECTION；如你的服务不同再调整。EMBEDDING_API_KEY 留空时复用 VLM_API_KEY。缺少某类 Key 时不要启用对应任务。

### 5.2 配置目标机器代理

在同一个 `.env` 中选择一种方式：

| 网络方式 | HTTP_PROXY 与 HTTPS_PROXY |
| --- | --- |
| 直连 | 两项均留空 |
| 远端代理 | 两项均填写实际地址，例如 `http://<代理IP>:<端口>` |
| Linux 宿主机代理 | 两项均填写 `http://host.docker.internal:<端口>` |
| WSL 使用 Windows 上的代理 | 两项均填写容器可达的 Windows IP 与代理端口；必须以第 8 步实测为准 |

容器内 127.0.0.1 不是宿主机。对于本文的 WSL 内 Docker Engine，host-gateway 指向 Linux Docker 宿主网关，不保证等于 Windows 代理地址。代理须监听可达接口，防火墙仅允许必要网段，不能暴露为公网开放代理。

### 5.3 校验配置

```bash
unset HTTP_PROXY HTTPS_PROXY http_proxy https_proxy ALL_PROXY all_proxy
docker compose -f docker-compose.deploy.yml --profile jobs config --quiet && printf 'compose config OK\n'
```

预期：`compose config OK`。此命令不会打印密钥。提示缺少 POSTGRES_PASSWORD 时返回编辑 `.env`。

## 6. 创建持久目录并构建镜像

```bash
mkdir -p data/collector/{video-downloads,video-state,whisper-cache,supplier,supplier-logs}
mkdir -p model-cache/huggingface
docker compose -f docker-compose.deploy.yml build app
```

预期：构建退出码为 0，最后显示镜像导出成功，并使用配置的 `intelligence-rag-app:server-v1` 名称。首次下载依赖耗时较长。

如果第 5 步自定义了 COLLECTOR_DATA_DIR 或 MODEL_CACHE_DIR，将 mkdir 路径同步替换为实际路径。

检查镜像：

```bash
docker image inspect intelligence-rag-app:server-v1 --format '{{.Id}}'
docker compose -f docker-compose.deploy.yml run --rm --no-deps jobs python --version
```

预期：输出 `sha256:...` 镜像 ID 和 `Python 3.12.x`，命令退出码均为 0。

## 7. 启动数据库与应用

```bash
docker compose -f docker-compose.deploy.yml up -d --no-build --wait --wait-timeout 180 app postgres qdrant
docker compose -f docker-compose.deploy.yml ps
```

预期：app、postgres 为 healthy，qdrant 为 running；启动命令退出码为 0。app 会为新数据库自动执行迁移。

核对数据库版本：

```bash
docker compose -f docker-compose.deploy.yml exec -T postgres \
  psql -U intelligence -d intelligence_rag -Atc 'SELECT version_num FROM alembic_version;'
```

当前代码预期：

```text
0004_supplier_relation_integrity
```

以后使用其他发布版本时，以该发布的 Alembic head 为准。

## 8. 验证页面、依赖与网络

```bash
curl -fsS http://127.0.0.1:8000/health
printf '\n'
curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:8000/
bash deploy/run-job.sh video-collect --validate
```

预期：健康接口 JSON 中 `status` 为 `ok`；首页返回 `200`；依赖检查输出：

```text
Job dependencies and Python syntax OK (no network or ingestion)
```

验证任务容器访问外网：

```bash
docker compose -f docker-compose.deploy.yml run --rm --no-deps jobs \
  python -c 'import requests; r=requests.get("https://www.youtube.com/robots.txt", timeout=30); print("HTTP", r.status_code); r.raise_for_status()'
```

预期：`HTTP 200`。如果超时、代理连接失败或返回错误，修正代理/出口后重试。本检查不能代替真实字幕与视频流下载测试。

打开网页：

- 同机 WSL：先在 Windows 浏览器尝试 `http://localhost:8000`。
- 远程服务器：已有 SSH 服务时，在访问者电脑执行：

```bash
ssh -N -L 18000:127.0.0.1:8000 <SSH用户>@<服务器地址>
```

预期：SSH 连接保持、通常无输出；浏览器打开 `http://127.0.0.1:18000` 显示项目页面。WSL 的 SSH 地址使用实际配置的可达入口。不要为了远程访问直接将无认证 API 暴露到公网。

## 9. 手工运行四类任务

本步会调用外部 API、消耗配额并可能写入新数据。按需要运行，缺少 Key 的任务先跳过。

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
| 视频 | 输出本轮候选和实际处理结果；检查新增、去重和失败分类，不能把 0 新增当作成功入库。 |
| 论坛 | 输出采集/去重/入库结果，无未处理异常。 |
| 供应商抽取 | 各阶段正常完成，无 `status=failed`；无新内容时允许跳过分析。 |
| 供应商验证 | 输出验证完成，或没有待验证记录。 |

进程正常结束时 `exit=0`。还须检查业务输出，退出码为 0 不保证每条候选成功。

检查采集文件：

```bash
find data/collector -maxdepth 3 -type f -printf '%P\n'
```

预期：成功任务后可见相应轮换状态、关键帧或供应商产物；无字幕、下载失败或去重时可能没有新增文件。

## 10. 安装 systemd 定时任务

### 10.1 返回管理员终端

如果仍在 `sudo -iu rag` 打开的 shell：

```bash
exit
whoami
```

预期：回到原先具有 sudo 权限的管理员账号，不是 rag。

### 10.2 安装并校验模板

```bash
sudo cp /opt/intelligence-rag/deploy/systemd/intelligence-rag-*.service /etc/systemd/system/
sudo cp /opt/intelligence-rag/deploy/systemd/intelligence-rag-*.timer /etc/systemd/system/
sudo systemd-analyze verify /etc/systemd/system/intelligence-rag-*.service /etc/systemd/system/intelligence-rag-*.timer
sudo systemctl daemon-reload
```

预期：verify 无错误，daemon-reload 无输出。出现 docker.service 不存在时停止：本指南要求 Ubuntu 内 Docker Engine，不要忽略此错误。

### 10.3 启用 timer

只启用第 9 步已验证可用的任务；四类任务均可用时执行：

```bash
sudo systemctl enable --now \
  intelligence-rag-video-collect.timer \
  intelligence-rag-discourse-collect.timer \
  intelligence-rag-supplier-pipeline.timer \
  intelligence-rag-supplier-verify.timer
systemctl list-timers 'intelligence-rag-*' --no-pager
```

预期：出现四个 timer，每个均有下一次运行时间。

| 任务 | 默认执行时间（服务器本地时区） |
| --- | --- |
| 视频 | 双数小时 :00 |
| 供应商抽取 | 双数小时 :15 |
| 论坛 | 双数小时 :30 |
| 供应商验证 | 奇数小时 :45 |

检查时区：

```bash
timedatectl show -p Timezone --value
```

预期：输出实际时区，如 `Asia/Shanghai` 或 `Etc/UTC`。按该时区理解 timer 时间。

## 11. 最终验收

管理员终端执行（会再运行一次视频采集）：

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

同时确认日志里不是配置失败或锁冲突跳过。oneshot service 完成后 inactive 正常；应保持 active 的是 timer。

再检查网页：

```bash
curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:8000/
```

预期：`200`。至此部署完成。

WSL/Windows 必须保持运行且网络可用，睡眠、关机或关闭 WSL 时不会执行采集。不要同时添加相同 cron 任务；不要执行 `docker compose down -v`，该命令会删除数据库数据卷。
