# 数据源页面：定时采集控制

页面读取宿主机 systemd 的实际状态，每 10 秒刷新。手动按钮只对四个固定采集 service 执行 `start --no-block`，不修改 timer 周期；运行中的任务不重复启动。进程退出码不代表业务入库成功。

## 当前 WSL 用户级任务

在运行 `rag-*.timer` 的用户下，从项目根目录执行：

```bash
export PROJECT_DIR="$PWD"
export SCHEDULER_BRIDGE_DIR="$PROJECT_DIR/data/scheduler"
install -d -m 700 "$SCHEDULER_BRIDGE_DIR" "$HOME/.config/systemd/user"
python3 -c 'import os,secrets; from pathlib import Path; p=Path(os.environ["SCHEDULER_BRIDGE_DIR"])/"token"; fd=os.open(p,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600); os.write(fd,secrets.token_urlsafe(32).encode()); os.close(fd)'
```

预期：生成权限为 600 的操作口令文件；如果文件已存在会报错，不会覆盖已有口令。

```bash
tee "$HOME/.config/systemd/user/rag-scheduler-bridge.service" >/dev/null <<EOF
[Unit]
Description=RAG scheduled task Unix socket bridge
[Service]
ExecStart=/usr/bin/python3 "$PROJECT_DIR/scripts/scheduler_bridge.py" --socket "$SCHEDULER_BRIDGE_DIR/control.sock" --scope user
Restart=on-failure
UMask=0077
[Install]
WantedBy=default.target
EOF
systemctl --user daemon-reload
systemctl --user enable --now rag-scheduler-bridge.service
curl --unix-socket "$SCHEDULER_BRIDGE_DIR/control.sock" http://localhost/tasks
```

预期：返回 `items`，只包含已安装的四类采集任务。无对应 timer 时为空；systemd 不可访问时返回 503，不伪造任务。

重建部署的 app（沿用原部署的 Compose 参数和环境文件）：

```bash
SCHEDULER_BRIDGE_DIR="$PROJECT_DIR/data/scheduler" \
  docker compose -f docker-compose.deploy.yml up -d --build --no-deps app
curl --fail http://127.0.0.1:8000/scheduled-tasks
```

预期：API 返回真实 timer 列表。页面的“采集操作口令”填写 `$SCHEDULER_BRIDGE_DIR/token` 文件内容，再点击任务的“手动采集”。口令不写入浏览器持久存储；错误口令返回 403。仅本机使用 HTTP，远程访问须通过 HTTPS 或 SSH 隧道。

独立宿主 API 可直接设置 `SCHEDULER_SOCKET` 与 `SCHEDULER_TOKEN_FILE` 为上述文件绝对路径。容器默认读取 `/run/rag-scheduler/control.sock` 和 `/run/rag-scheduler/token`；Compose 将宿主目录只读挂载，不挂 Docker socket、不授予特权容器权限。未部署桥接时，页面显示明确的不可用错误。

## 其他服务器的系统级任务

`--scope system` 对应部署模板 `intelligence-rag-*.timer`。将上面的桥接 service 安装到 `/etc/systemd/system/`，改为 `--scope system`，通过 `sudo systemctl enable --now rag-scheduler-bridge.service` 启动，并使用 `WantedBy=multi-user.target`。该桥接由 root 运行，只接受 Unix socket 的四种固定任务名。目录和口令保持 700/600，仅向 app 容器挂载；不要开放 TCP 端口或 chmod 777。

此页面不列出或允许执行系统维护 timer、任意 shell 命令或 Hermes cron。

## WSL / Docker Desktop 重启后返回 503

若宿主桥接正常但页面提示“宿主机定时任务接口不可用”，先比较宿主与容器的 socket；不要放宽目录权限或重建数据库：

```bash
curl --fail --unix-socket "$PWD/data/scheduler/control.sock" http://localhost/tasks
docker exec intelligence-rag-app python -c 'from pathlib import Path; print(Path("/run/rag-scheduler/control.sock").is_socket())'
```

预期：宿主返回任务 JSON，容器输出 `True`。如果宿主正常、容器输出 `False`，Docker Desktop 可能在 WSL 目录就绪前恢复容器，导致容器仍挂着空目录。待宿主桥接就绪后仅重启 app，重新建立挂载：

```bash
docker restart intelligence-rag-app
docker exec intelligence-rag-app python -c 'from pathlib import Path; assert Path("/run/rag-scheduler/control.sock").is_socket()'
curl --fail http://127.0.0.1:8000/scheduled-tasks
```

预期：断言通过，API 返回真实任务列表；若应用尚在启动，待健康检查通过后重试 curl。此操作会短暂中断 Web 服务，不重启数据库或采集 service。Docker Desktop 自动恢复容器不保证 WSL 目录已就绪，后续重启仍需检查该接口。

## 验证

```bash
.venv/bin/python -m pytest tests/unit/test_scheduler_control.py -q
npm --prefix frontend test -- --run tests/sources-page.test.tsx
VITE_API_BASE_URL= npm --prefix frontend run build
```

预期：后端白名单、运行中去重、操作口令及前端状态/按钮测试通过，前端构建成功。
