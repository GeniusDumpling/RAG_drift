# 在 WSL 中部署（Windows 10/11 + WSL2）

适用：在 Windows 电脑的 WSL2（Ubuntu 22.04 / 24.04）内从零部署。采用 **Ubuntu 内安装 Docker Engine**，不使用 Windows Docker Desktop；宿主机（Windows）不需要安装项目 Python、uv 或 ffmpeg。

约定：部署目录 `/opt/intelligence-rag`，运行账号 `rag`，API 端口 `8000`。命令中的 `<...>` 必须替换。**任一步失败立即停止排查，不要跳步继续。** 除明确标注 `PowerShell` 的命令在 Windows 执行外，其余全部在 Ubuntu 内执行。

正式采集任务统一由 `deploy/run-job.sh` 运行，底层使用 `docker compose -f docker-compose.deploy.yml run --rm jobs`，不要在 API 容器中长期运行采集。

> 远程 Linux 服务器请改用 [在 Linux 服务器上部署](deploy-linux-server.md)。

## 1. 安装 WSL2 与 Ubuntu

在**管理员 PowerShell** 执行：

```powershell
wsl --install -d Ubuntu-24.04
```

预期：安装发行版，必要时提示重启 Windows。首次进入 Ubuntu 按提示创建 Linux 用户和密码。

检查：

```powershell
wsl --list --verbose
```

预期输出：

```text
  NAME            STATE           VERSION
* Ubuntu-24.04    Stopped         2
```

`VERSION` 必须为 `2`。进入 Ubuntu：

```powershell
wsl -d Ubuntu-24.04
```

预期：进入 Linux shell。

## 2. 启用 systemd

```bash
cat /etc/os-release | head -2
ps -p 1 -o comm=
```

预期输出：

```text
NAME="Ubuntu"
VERSION="24.04.x LTS (Noble)"
systemd
```

若第二条不是 `systemd`，编辑配置：

```bash
sudo nano /etc/wsl.conf
```

在已有文件中合并以下内容（已存在 `[boot]` 时只加第二行，不要重复添加段名）：

```ini
[boot]
systemd=true
```

回到 PowerShell 重启 WSL 后再进入：

```powershell
wsl --shutdown
wsl -d Ubuntu-24.04
```

重新执行 `ps -p 1 -o comm=`，预期输出 `systemd`。注意 `wsl --shutdown` 会停止这台 Windows 上**所有**运行中的 WSL 发行版。

## 3. 安装 Docker Engine

> 本机已配置 Docker Desktop 的 WSL 集成时，不要再装第二套 Docker；直接用其分支：跳过本步安装，执行下面检查时 `docker info --format '{{.OSType}}'` 输出 `linux` 即可继续，第 10 步安装 timer 前需按 `systemctl show docker.service -p LoadState --value` 的结果删改副本中的 `Requires=docker.service` 行。

```bash
sudo apt-get update
sudo apt-get install -y ca-certificates curl git openssl nano
sudo install -m 0755 -d /etc/apt/keyrings
sudo curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
sudo chmod a+r /etc/apt/keyrings/docker.asc
echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/ubuntu $(. /etc/os-release && echo "$VERSION_CODENAME") stable" | sudo tee /etc/apt/sources.list.d/docker.list >/dev/null
sudo apt-get update
sudo apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
sudo systemctl enable --now docker
```

预期：各命令正常结束，无签名或网络错误。

检查：

```bash
docker --version
docker compose version
systemctl is-active docker
docker run --rm hello-world
```

预期输出：

```text
Docker version 27.x.x, build ...
Docker Compose version v2.x.x
active
Hello from Docker!
```

## 4. 创建运行账号与部署目录

以刚创建的、有 sudo 权限的 Ubuntu 用户执行：

```bash
id rag >/dev/null 2>&1 || sudo useradd --create-home --shell /bin/bash rag
sudo usermod -aG docker rag
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

提示 Docker socket 权限错误时，退出后重新 `sudo -iu rag` 再检查。后续普通命令均以 rag 执行；带 `sudo` 的管理命令返回原管理员终端执行。

## 5. 获取发布代码

```bash
cd /opt/intelligence-rag
git clone https://github.com/GeniusDumpling/RAG_drift.git .
read -r -p '输入发布 commit 或 tag: ' RELEASE_REF
git checkout --detach "$RELEASE_REF"
test -f deploy/run-job.sh && test -f .env.example && printf 'deployment files OK\n'
git rev-parse HEAD
```

预期输出：

```text
deployment files OK
<指定发布版本的完整 commit ID>
```

文件检查失败说明该版本不含部署文件，停止并改用包含部署改造的发布版本。

## 6. 配置 .env

```bash
test ! -e .env && cp .env.example .env
chmod 600 .env
openssl rand -hex 32
nano .env
```

预期：随机命令输出一串 64 位十六进制密码，编辑器打开 `.env`。已存在的 `.env` 不会被覆盖。

至少填写以下项（不要把密钥提交进 Git）：

```dotenv
POSTGRES_PASSWORD=<粘贴刚生成的密码>
INTELLIGENCE_RAG_IMAGE=intelligence-rag-app:<本次发布标签>
COMPOSE_PROJECT_NAME=intelligence-rag
APP_BIND_ADDRESS=127.0.0.1
APP_PORT=8000
COLLECTOR_DATA_DIR=./data/collector
MODEL_CACHE_DIR=./model-cache/huggingface
HTTP_PROXY=
HTTPS_PROXY=
YOUTUBE_API_KEY=<视频搜索 Key>
VLM_API_KEY=<视频摘要 Key>
EMBEDDING_API_KEY=<向量 Key，可留空复用 VLM_API_KEY>
LLM_API_KEY=<问答与供应商分析 Key>
BAIDU_QIANFAN_API_KEY=<供应商搜索 Key>
```

其余模型、API 地址、collection 配置保留模板默认值即可。

**WSL 代理要点**：采集容器需要访问 YouTube，国内网络必须配代理。

- `HTTP_PROXY`、`HTTPS_PROXY` 填同一个地址。
- 代理跑在 Windows 上时，填 Windows 代理对容器可达的地址（Windows 代理软件须开启"允许局域网连接"），具体 IP/端口以第 8 步实测为准；`127.0.0.1` 在容器内不是你的 Windows，不能直接填。
- 代理跑在 WSL 内时，填 `http://host.docker.internal:<端口>`。
- 直连可达外网的机器两项留空。

校验配置（不打印密钥）：

```bash
unset HTTP_PROXY HTTPS_PROXY http_proxy https_proxy ALL_PROXY all_proxy
docker compose -f docker-compose.deploy.yml --profile jobs config --quiet && printf 'compose config OK\n'
```

预期输出：

```text
compose config OK
```

## 7. 创建持久目录并构建镜像

```bash
mkdir -p data/collector/{video-downloads,video-state,whisper-cache,supplier,supplier-logs}
mkdir -p model-cache/huggingface
docker compose -f docker-compose.deploy.yml build app
```

预期：构建退出码为 0，末尾显示镜像导出成功。首次构建需下载较多依赖，耗时较长。

验证镜像：

```bash
docker compose -f docker-compose.deploy.yml run --rm --no-deps jobs python --version
```

预期输出：

```text
Python 3.12.x
```

## 8. 启动并验证

```bash
docker compose -f docker-compose.deploy.yml up -d --no-build --wait --wait-timeout 180 app postgres qdrant
docker compose -f docker-compose.deploy.yml ps
```

预期：app、postgres 为 `healthy`，qdrant 为 `running`，启动命令退出码为 0。

核对数据库迁移版本：

```bash
docker compose -f docker-compose.deploy.yml exec -T postgres \
  psql -U intelligence -d intelligence_rag -Atc 'SELECT version_num FROM alembic_version;'
```

当前发布预期输出（以后以所部署版本的 migration head 为准）：

```text
0009_supplier_multibrand
```

健康检查、依赖检查与容器外网连通性（外网检查用于验证第 6 步的代理配置）：

```bash
curl -fsS http://127.0.0.1:8000/health
printf '\n'
curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:8000/
bash deploy/run-job.sh video-collect --validate
docker compose -f docker-compose.deploy.yml run --rm --no-deps jobs \
  python -c 'import requests; r=requests.get("https://www.youtube.com/robots.txt", timeout=30); print("HTTP", r.status_code); r.raise_for_status()'
```

预期输出：

```json
{"status":"ok", ...}
200
Job dependencies and Python syntax OK (no network or ingestion)
HTTP 200
```

`HTTP 200` 拿不到时先修正第 6 步的 `HTTP_PROXY`/`HTTPS_PROXY` 再重试。

在 **Windows 浏览器**打开 `http://localhost:8000`，预期显示项目页面。

## 9. 手工试跑采集任务

以下任务会调用外部 API、消耗配额并写入数据。按需执行，缺少对应 Key 的任务先跳过：

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

| 任务 | 验收条件 |
| --- | --- |
| video-collect | 输出本轮候选与分类结果（success / duplicate / 失败原因）；新增 0 条不算成功入库 |
| discourse-collect | 输出主题与回复归档、入库结果，无未处理异常 |
| supplier-pipeline | 各阶段日志正常，无 `status=failed`；无新 URL 时允许跳过分析 |
| supplier-verify | 完成验证，或报告没有待验证记录 |

`exit=0` 只代表进程正常退出，还须检查业务输出。失败时先修配置/模型/网络，不要带病启用定时器。

## 10. 安装 systemd 定时调度

### 10.1 安装模板

返回原管理员终端（`sudo -iu rag` 打开的 shell 执行 `exit` 退出）：

```bash
sudo cp /opt/intelligence-rag/deploy/systemd/intelligence-rag-*.service /etc/systemd/system/
sudo cp /opt/intelligence-rag/deploy/systemd/intelligence-rag-*.timer /etc/systemd/system/
sudo systemd-analyze verify /etc/systemd/system/intelligence-rag-*.service /etc/systemd/system/intelligence-rag-*.timer
sudo systemctl daemon-reload
```

预期：verify 无错误，daemon-reload 无输出。

### 10.2 启用 timer

只启用第 9 步已验证可用的任务；全部验证通过时：

```bash
sudo systemctl enable --now \
  intelligence-rag-video-collect.timer \
  intelligence-rag-discourse-collect.timer \
  intelligence-rag-supplier-pipeline.timer \
  intelligence-rag-supplier-verify.timer
systemctl list-timers 'intelligence-rag-*' --no-pager
```

预期：各 timer 出现且各有下次执行时间。视频整点、供应商抽取 :15、论坛 :30（北京时间）、供应商验证奇数小时 :45（均按 WSL 内时区显示，`timedatectl show -p Timezone --value` 确认）。

### 10.3 WSL 专属注意

- WSL 停止或 Windows 睡眠/关机期间**不会执行采集**；恢复后 `Persistent=true` 的 timer 会补触发一次，但不保证 Docker 已就绪。
- 保持 WSL 后台运行（如依赖此部署长期采集），不要随手 `wsl --shutdown`。

## 11. 最终验收

管理员终端触发一次真实采集：

```bash
sudo systemctl start intelligence-rag-video-collect.service
systemctl show intelligence-rag-video-collect.service -p Result -p ExecMainStatus
sudo journalctl -u intelligence-rag-video-collect.service -n 50 --no-pager
```

正常预期：

```text
Result=success
ExecMainStatus=0
```

oneshot service 结束后显示 `inactive` 属正常。最后在 Windows 浏览器确认 `http://localhost:8000` 可访问。

**日常运维**：应用日志 `docker compose -f docker-compose.deploy.yml logs --tail 100 app`；采集日志 `sudo journalctl -u intelligence-rag-video-collect.service -n 200 --no-pager`。**禁忌**：`docker compose down -v` 与 `docker volume prune` 会删除数据库数据卷；`.env` 含密钥不要入库 Git。
