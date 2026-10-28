# 无人机品牌切换部署（2026-10-28）

正式应用已从 `rag-supplier-multibrand:20261028-v1` 更新至 `rag-drone-brands:20261028-v1`，使用原 8000 端口，状态为 healthy。

新镜像 ID：`sha256:745dd77f86ee36af962f81c8387e065cbf059140fd761fa3afe57642f2b78acd`。

## 发布范围

- 使用 `docker/Dockerfile.drone-update`，基于实际运行的供应商多品牌镜像制作增量镜像。
- 发布已通过生产构建的前端，以及无人机 API、schema 和目录读取服务三个模块；同时更新源码目录与实际安装包。
- 在已部署的 Settings 中仅补充 `parrot_documents_dir` 字段，没有覆盖工作区中其他未发布的配置及 LLM/图谱业务改动。
- 从正式容器的实际配置生成本次 Compose 配置，保留原有环境变量、端口、网络、原有四个挂载及启动参数。新增 `/data/parrot-documents` 只读挂载，指向本地已整理的 Parrot 资料。
- 本次无数据库结构变化，应用使用 `RUN_MIGRATIONS=0`，没有执行迁移、重新向量化、供应商采集或付费模型请求。
- `.env` 的 `INTELLIGENCE_RAG_IMAGE` 已更新为新镜像。

## 验收

先在仅绑定本机的临时端口验收，再仅重建正式 app。临时与正式应用均通过：

- 健康检查、DJI 82 个型号 / 7,318 项参数、Parrot 9 个型号版本 / 643 项参数。
- 全部 Parrot 型号详情的厂商、参数数量，以及 UKR 的版本 `8.5.0.0` 和更新日期 `2026-10-01`。
- 未知品牌返回 422，默认 DJI 查询不会错误返回 Parrot 型号。
- 原有供应商品牌、统计与列表、会话和数据库概览接口正常；验收时供应商关系数量为 86，与部署前一致。
- 真实 Chromium 正式页面可切换 DJI / Parrot、选择具体型号、查看版本和参数并搜索。1440×1100 桌面与 390×844 手机布局正常，品牌来回切换通过，无页面脚本异常及手机整体横向溢出。

浏览器无人机请求使用正式接口和真实参数文件；仅初始总览页四个无关列表请求返回空列表，避免额外访问业务数据库。

PostgreSQL、Qdrant、Neo4j、pgAdmin、Redis 的容器 ID 和启动时间与部署前一致。临时验证容器已清理。

## 发布资料与回退

发布资料位于 `data/releases/drone-brands-20261028-v1/`，目录权限 700，含私有配置的文件权限为 600：

- `manifest.json`、`build/`、`build.log`：镜像与构建输入校验和。
- `before-app-inspect.json`、`before.env`：部署前配置。
- `runtime-compose.json`、`rollback-compose.json`：本次正式配置和回退配置。
- `baseline-smoke.json`、`preview-smoke.json`、`production-smoke.json`、`verification.json`：接口、挂载与容器核对。
- `production-screenshots/`：正式页面截图和浏览器验证报告。
- `rollback.sh`、`deploy.py`：受镜像版本检查保护的回退入口。

如需回退本次应用版本：

```bash
bash data/releases/drone-brands-20261028-v1/rollback.sh
```

回退恢复旧镜像及部署前的实际应用配置，仍跳过迁移，不执行数据库降级。旧镜像和原始资料保留；若正式服务已被其他版本替换，回退脚本会拒绝覆盖。
