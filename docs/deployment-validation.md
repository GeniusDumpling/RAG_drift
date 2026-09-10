# 部署改造验证记录

## 已执行

- 部署相关测试：`tests/unit/test_deployment_contract.py`、`test_compose_portability.py`、`test_job_runner_portability.py`、`test_dockerfile_contract.py`，21 passed。
- 上述测试与修改的 video pipeline 通过 Ruff；shell 脚本通过 `bash -n`；`git diff --check` 通过。
- Compose 用临时目录和合成配置实际展开，覆盖空代理直连/不同端口代理、app/jobs 同库同向量配置、内部端口隔离与持久目录。
- 完整 Dockerfile 构建曾成功，镜像依赖离线 import 成功。
- 最终脚本修改后的完整重建先遇到 Docker Hub TLS handshake timeout；重试的 Debian 包下载缓慢，主动取消。随后基于本次已成功构建的依赖镜像复制最新 tools 构建验证镜像。此增量验证不能表述为最终源码再次完成了干净全量构建。
- 最终验证镜像：`intelligence-rag-app:deployment-check`。镜像内没有 `.env`，包含最新 VIDEO_SEARCH_STATE_PATH 支持。
- 真实隔离 Compose 栈：新建独立 PostgreSQL/Qdrant 卷、随机项目名/本地端口、合成数据库密码，没有使用开发密钥。app 启动并完成新库迁移；`/health` 与 `/` 返回 200；通过真实 runner 启动 jobs `--validate`，依赖及 tools 语法检查成功。测试容器/网络/命名卷已清理。

## 基线与限制

- `tests/unit` 结果：48 passed，36 failed。未修改 HEAD 的临时导出副本重现相同的 36 项失败：视频测试仍引用已迁移的 `skills/video-crawler` 路径，以及 FlyForum 演示默认数量断言过期。本次没有借部署改造改写这批业务测试。
- 本机没有可供 `systemd-analyze verify` 解析的系统级 docker.service（使用现有 Docker 接口），因此系统单元依赖校验报告 `Unit docker.service not found`。模板未安装或启动；应在安装标准 Docker Engine 的目标服务器按指南校验。
- 未获得目标服务器访问权限，未执行目标机部署。
- 未执行真实付费 LLM、YouTube 视频下载和供应商采集；隔离 smoke 证明启动、依赖和数据库迁移，不证明外部网络/业务入库链路。
- 未更改本机运行中的 cron、user timer 或业务数据；未提交或推送 Git。
