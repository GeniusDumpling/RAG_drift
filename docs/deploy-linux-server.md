# 在 Linux 服务器上部署

适用：Ubuntu 22.04 / 24.04 服务器，从零完成新部署。宿主机不需要安装项目 Python、uv 或 ffmpeg，所有依赖都在容器内。

约定：部署目录 `/opt/intelligence-rag`，运行账号 `rag`，API 端口 `8000`。命令中的 `<...>` 必须替换后再执行。**任一步失败立即停止排查，不要跳步继续。**

正式采集任务统一由 `deploy/run-job.sh` 运行，底层使用 `docker compose -f docker-compose.deploy.yml run --rm jobs`，不要在 API 容器中长期运行采集。

## 1. 检查服务器

以有 sudo 权限的账号登录：

```bash
cat /etc/os-release | head -2
ps -p 1 -o comm=
free -h | head -2
df -h / | tail -1
```

预期输出：

```text
NAME="Ubuntu"
VERSION="22.04.x LTS" (Jammy) 或 "24.04.x LTS" (Noble)
systemd
Mem: 总内存 ≥ 4Gi
/dev/...  可用磁盘 ≥ 20G
```

第二条必须输出 `systemd`（后续定时调度依赖它）。内存或磁盘偏小时先扩容再继续。

## 2. 安装 Docker Engine

已安装 Docker 的服务器跳到检查步骤验证版本即可：

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
sudo docker run --rm hello-world
```

预期输出：

```text
Docker version 27.x.x, build ...
Docker Compose version v2.x.x
active
Hello from Docker!
```

拉取镜像失败时，先解决服务器访问 Docker Hub 的网络问题（`.env` 里的代理只作用于采集任务容器，不影响 Docker daemon 拉镜像）。

## 3. 创建运行账号与部署目录

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

若提示 Docker socket 权限错误，退出后重新 `sudo -iu rag` 再检查。`docker` 组具有高权限，只授予可信账号。后续普通命令均以 rag 执行；带 `sudo` 的管理命令返回管理员终端执行。

## 4. 获取发布代码

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

文件检查失败说明该版本不含部署文件，停止并改用包含部署改造的发布版本。开发机未推送的改动不会通过 clone 获取。

## 5. 配置 .env

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

其余模型、API 地址、collection 配置保留模板默认值即可。代理二选一：

- 直连：`HTTP_PROXY`、`HTTPS_PROXY` 留空。
- 走代理：两项填同一个可达地址，如 `http://<代理IP>:<端口>`。

容器内 `127.0.0.1` 不是宿主机；代理须监听容器可达的接口，不要开放到公网。

校验配置（不打印密钥）：

```bash
unset HTTP_PROXY HTTPS_PROXY http_proxy https_proxy ALL_PROXY all_proxy
docker compose -f docker-compose.deploy.yml --profile jobs config --quiet && printf 'compose config OK\n'
```

预期输出：

```text
compose config OK
```

提示缺少 `POSTGRES_PASSWORD` 时返回编辑 `.env`。

## 6. 创建持久目录并构建镜像

```bash
mkdir -p data/collector/{video-downloads,video-state,whisper-cache,supplier,supplier-logs}
mkdir -p model-cache/huggingface
docker compose -f docker-compose.deploy.yml build app
```

预期：构建退出码为 0，末尾显示镜像以 `.env` 中配置的名称导出成功。首次构建需下载较多依赖，耗时较长。

验证镜像：

```bash
docker compose -f docker-compose.deploy.yml run --rm --no-deps jobs python --version
```

预期输出：

```text
Python 3.12.x
```

服务器无法联网构建时的离线方案——在**同架构、可联网**的机器上用同一发布代码构建后传输：

```bash
# 联网机器
docker save -o intelligence-rag-app.tar intelligence-rag-app:<本次发布标签>
scp intelligence-rag-app.tar <目标用户>@<服务器>:/tmp/
# 目标服务器
docker load -i /tmp/intelligence-rag-app.tar
```

预期输出：

```text
Loaded image: intelligence-rag-app:<本次发布标签>
```

基础镜像也拉不下来时，一并传输 `postgres:16-alpine` 和 `qdrant/qdrant:v1.12.4`。

## 7. 启动应用与数据库

```bash
docker compose -f docker-compose.deploy.yml up -d --no-build --wait --wait-timeout 180 app postgres qdrant
docker compose -f docker-compose.deploy.yml ps
```

预期：app、postgres 状态为 `healthy`，qdrant 为 `running`（或 `Up`），启动命令退出码为 0。新数据库由应用启动时自动执行迁移。

核对数据库迁移版本：

```bash
docker compose -f docker-compose.deploy.yml exec -T postgres \
  psql -U intelligence -d intelligence_rag -Atc 'SELECT version_num FROM alembic_version;'
```

当前发布预期输出（以后以所部署版本的 migration head 为准）：

```text
0009_supplier_multibrand
```

## 8. 验证页面、依赖与网络

```bash
curl -fsS http://127.0.0.1:8000/health
printf '\n'
curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:8000/
bash deploy/run-job.sh video-collect --validate
```

预期输出：

```json
{"status":"ok", ...}
200
Job dependencies and Python syntax OK (no network or ingestion)
```

检查任务容器能否访问外网（不消耗 API 配额）：

```bash
docker compose -f docker-compose.deploy.yml run --rm --no-deps jobs \
  python -c 'import requests; r=requests.get("https://www.youtube.com/robots.txt", timeout=30); print("HTTP", r.status_code); r.raise_for_status()'
```

预期输出：

```text
HTTP 200
```

超时或代理错误时先修正第 5 步配置再重试；本检查通过不代表字幕/视频流一定可下载。

从你自己的电脑访问页面（保持默认 loopback 绑定，通过 SSH 隧道，不要把无认证的 API 暴露到公网）：

```bash
ssh -N -L 18000:127.0.0.1:8000 <SSH用户>@<服务器地址>
```

预期：终端保持连接、通常无输出；在本地浏览器打开 `http://127.0.0.1:18000` 显示项目页面。

## 9. 手工试跑采集任务

以下任务会调用外部 API、消耗配额并写入数据。按需执行，缺少对应 Key 的任务先跳过：

```bash
bash deploy/run-job.sh video-collect
printf 'video exit=%s\n' "$?"
bash deploy/run-job.sh discourse-collect
printf 'forum exit=%s\n' "$?"
bash deploy/run-job.sh discourse-backfill
printf 'forum backfill exit=%s\n' "$?"
bash deploy/run-job.sh supplier-pipeline
printf 'supplier exit=%s\n' "$?"
bash deploy/run-job.sh supplier-verify
printf 'verify exit=%s\n' "$?"
```

| 任务 | 验收条件 |
| --- | --- |
| video-collect | 输出本轮候选与分类结果（success / duplicate / 失败原因）；新增 0 条不算成功入库 |
| discourse-collect | 输出主题与回复归档、入库结果，无未处理异常 |
| discourse-backfill | 输出扫描与归档结果、续跑位置；单轮达预算不等于历史回填完成 |
| supplier-pipeline | 各阶段日志正常，无 `status=failed`；无新 URL 时允许跳过分析 |
| supplier-verify | 完成验证，或报告没有待验证记录 |

`exit=0` 只代表进程正常退出，还须检查业务输出。失败时先修配置/模型/网络，不要带病启用定时器。

检查采集产物：

```bash
find data/collector -maxdepth 3 -type f -printf '%P\n'
```

预期：有成功任务时出现关键帧、轮换状态或供应商产物文件；纯去重或失败时可能没有新文件。

## 10. 安装 systemd 定时调度

### 10.1 确认无重复调度

```bash
crontab -l
systemctl --user list-timers --all --no-pager
sudo systemctl list-timers --all --no-pager | grep -i rag
```

预期：没有调度这些任务的旧条目；有则先停用对应条目。

### 10.2 安装模板并启用

返回管理员终端执行：

```bash
sudo cp /opt/intelligence-rag/deploy/systemd/intelligence-rag-*.service /etc/systemd/system/
sudo cp /opt/intelligence-rag/deploy/systemd/intelligence-rag-*.timer /etc/systemd/system/
sudo systemd-analyze verify /etc/systemd/system/intelligence-rag-*.service /etc/systemd/system/intelligence-rag-*.timer
sudo systemctl daemon-reload
```

预期：verify 无错误，daemon-reload 无输出。`Unit docker.service not found` 之类错误必须先解决。

只启用第 9 步已验证可用的任务；五类任务全部验证通过时：

```bash
sudo systemctl enable --now \
  intelligence-rag-video-collect.timer \
  intelligence-rag-discourse-collect.timer \
  intelligence-rag-discourse-backfill.timer \
  intelligence-rag-supplier-pipeline.timer \
  intelligence-rag-supplier-verify.timer
systemctl list-timers 'intelligence-rag-*' --no-pager
```

预期：五个 timer 均出现且各有下次执行时间。

| Timer | 默认时刻 |
| --- | --- |
| video-collect | 每 2 小时的整点（服务器本地时区） |
| supplier-pipeline | 每 2 小时的 :15（服务器本地时区） |
| supplier-verify | 每 2 小时的 :45（服务器本地时区） |
| discourse-collect | 每 2 小时的 :30（北京时间） |
| discourse-backfill | 每小时的 :05（北京时间） |

检查时区，按实际时区理解上表：

```bash
timedatectl show -p Timezone --value
```

预期输出：

```text
Asia/Shanghai    （或 Etc/UTC 等实际时区）
```

## 11. 最终验收

管理员终端触发一次真实采集：

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

oneshot service 结束后显示 `inactive` 属正常；应保持 `active` 的是 timer。日志里若出现配置失败或锁冲突跳过，回到第 5/9 步排查。

## 12. 日常运维

```bash
# 应用日志
docker compose -f docker-compose.deploy.yml logs --tail 100 app
# 查看某次采集日志（journalctl）
sudo journalctl -u intelligence-rag-video-collect.service -n 200 --no-pager
# 重启应用（不删数据）
docker compose -f docker-compose.deploy.yml up -d --no-build --wait app
```

升级到新发布版本时：`git fetch && git checkout --detach <新发布>` → 更新 `.env` 中 `INTELLIGENCE_RAG_IMAGE` 标签 → 重新构建 → `up -d --wait` → 按第 7/8 步验收。

**禁忌**：`docker compose down -v` 和 `docker volume prune` 会删除数据库数据卷；`.env` 含密钥不要入库 Git；已有数据的旧部署升级前先备份并在副本上验证迁移。
