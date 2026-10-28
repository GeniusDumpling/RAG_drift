# Parrot / Skydio 供应商定时任务

2026-10-28 已安装并启用四个 systemd 用户定时器，沿用现有供应商任务的宿主机调度方式。源文件保存在 `deploy/systemd/user/`，实际安装目录为 `~/.config/systemd/user/`。

所有时间显式使用 `Asia/Shanghai`；每个任务每半小时执行一次，同品牌的抽取与验证错开 15 分钟。

| 品牌 | 任务 | 每小时触发分钟 | 定时器 |
| --- | --- | --- | --- |
| Parrot | 抽取 | :00、:30 | `rag-supplier-parrot-pipeline.timer` |
| Parrot | 验证 | :15、:45 | `rag-supplier-parrot-verify.timer` |
| Skydio | 抽取 | :05、:35 | `rag-supplier-skydio-pipeline.timer` |
| Skydio | 验证 | :20、:50 | `rag-supplier-skydio-verify.timer` |

## 运行行为

- 工作目录为 `%h/projects/RAG_drift`，使用项目 `.venv/bin/python` 和根目录 `.env`。
- 分别传入 `--brand parrot` / `--brand skydio`；验证显式使用 `--mode unverified`。当前配置每轮最多验证 3 家供应商。
- 同一品牌抽取与验证共用 `%t/rag-supplier-<brand>.lock`。锁被占用时记录日志并跳过该轮，退出码 75 视为正常跳过。
- 单次最长运行 90 分钟，停止宽限 30 秒；停止时清理整个进程组。
- `Persistent=true`：关机期间错过执行时间，会在定时器恢复后补触发一次。触发精度为 1 分钟。
- 已确认用户 `Linger=yes`，退出登录后仍可运行，开机后自动启动定时器。

## 校验结果

- 八个 unit 文件通过 `systemd-analyze --user verify`。
- 四个定时器均为 `enabled`、`active`。
- 在实际 systemd 环境中完成依赖导入、品牌配置、证据索引、LLM 配置及数据库只读连接检查；数据库版本为 `0009_supplier_multibrand`，Parrot 有 6 家供应商，Skydio 有 3 家。
- 运行前检查未调用搜索或大模型 API。启用时仅启动定时器，未手动启动抽取或验证服务。
- 已按用户要求将四个任务从每两小时调整为每半小时执行，保留品牌锁与验证数量限制。若上轮仍在运行，systemd 不会重复启动同一服务；若同品牌另一任务占用锁，则跳过本轮。

## 查看状态与日志

```bash
systemctl --user list-timers --all 'rag-supplier-*'
journalctl --user -u rag-supplier-parrot-pipeline.service -n 100 --no-pager
journalctl --user -u rag-supplier-parrot-verify.service -n 100 --no-pager
journalctl --user -u rag-supplier-skydio-pipeline.service -n 100 --no-pager
journalctl --user -u rag-supplier-skydio-verify.service -n 100 --no-pager
```

脚本同时将各品牌运行产物写入 `tools/supplier-information/` 下的 `logs/`、`search_results/<brand>/`。

## 重新安装

在项目根目录运行：

```bash
install -m 0644 deploy/systemd/user/rag-supplier-{parrot,skydio}-{pipeline,verify}.{service,timer} ~/.config/systemd/user/
systemd-analyze --user verify ~/.config/systemd/user/rag-supplier-{parrot,skydio}-{pipeline,verify}.{service,timer}
systemctl --user daemon-reload
systemctl --user enable --now rag-supplier-{parrot,skydio}-{pipeline,verify}.timer
```
