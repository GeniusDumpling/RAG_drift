# 供应商多品牌页面部署（2026-10-28）

正式应用已从 `rag-admin-documents:20261027-v1` 更新为 `rag-supplier-multibrand:20261028-v1`，状态为 `healthy`，继续使用 8000 端口。

镜像 ID：`sha256:6b31a747ac763fc01c744874dcb8c36cc50544985addd1e08759f5257ce1e2a4`。

## 发布内容

通过 `docker/Dockerfile.supplier-update` 制作增量镜像，保留原镜像的其他业务实现与依赖，仅复制以下内容：

- 已通过生产构建的前端资源，支持大疆、Parrot、Skydio、全部品牌选择，以及品牌范围内的统计、搜索、分页和验证记录。
- 供应商 API、repository、schema、model、数据库查看字段和脚本使用的独立 LLM 配置模块。
- 供应商搜索/抽取/验证工具与品牌配置，新增 PyMuPDF 1.28.2；构建中 `pip check` 通过。
- `0009_supplier_multibrand` 迁移文件，与已应用的主库版本一致。

从实际运行容器生成发布配置，保留端口、4 个挂载和原网络。原有环境变量保持，更新 Brave key，并补充原环境缺少的供应商脚本配置。没有发布工作区中无关的 LLM/图谱业务代码改动。

## 验收

先在 `127.0.0.1:18081` 临时容器验收，再通过 Compose 仅重建正式 app。临时和正式应用均通过：

- `/health`、品牌列表、按品牌统计、按品牌供应商列表、分页与验证记录关联。
- 大疆 74 家、Parrot 6 家、Skydio 3 家，全部品牌共 83 条关系；统计和列表一致。
- 前端资源包含品牌选择和元数据展示；生产页面可切换品牌、关闭旧验证展开、重置分页，390px 手机宽度无页面整体横向溢出。
- `/drones`、`/chat-sessions`、`/database/overview` 返回 200。
- 新镜像中的 PyMuPDF 解析器实际解析合成 PDF 表格成功；品牌和共享 LLM 模块可导入。

逐一比较 PostgreSQL、Qdrant、Neo4j、pgAdmin 的容器 ID 和启动时间，均与部署前一致，没有重启。临时 Docker 验证容器已移除，先前 18011 的宿主机页面预览已停止。

本次验收使用读取接口及合成 PDF，没有再次调用付费搜索/LLM，也没有重新采集或验证供应商。

## 备份与回退

发布资料位于 `data/releases/supplier-multibrand-20261028-v1/`：

- `before-main.dump`：主库部署前一致性备份，189,980,234 字节。
- `before.env`、`before-app-inspect.json`：部署前配置。
- `runtime-compose.json`：正式应用实际运行配置。
- `preview-smoke.json`、`production-smoke.json`、`verification.json`、`startup.log`：验收记录。
- `production-screenshots/`：正式应用截图。
- `rollback-compose.json`、`rollback.sh`：回退配置和脚本。

目录权限 700，包含私有环境变量的文件权限为 600，不在源码中提交。项目 `.env` 的 `INTELLIGENCE_RAG_IMAGE` 已更新。

如需回退：

```bash
bash data/releases/supplier-multibrand-20261028-v1/rollback.sh
```

回退脚本会确认正式 app 仍运行本次镜像，再恢复旧镜像和原镜像配置。旧镜像的迁移脚本不认识 0009，因此回退配置使用 `RUN_MIGRATIONS=0`；数据库新增列和业务数据保留，不执行数据库降级。
