# Hubsan／Skydio 前端与部署更新（2026-11-13）

已部署 `rag-hubsan-skydio-ui:20261112-v1`，实际镜像 ID 为 `sha256:c45bf85d2f6af6b33c63172952c70564b471e9118c13c4774f2316a4f2200c47`。刷新「工作空间 → 无人机信息」，可切换 DJI、Parrot、Autel、Hubsan 和 Skydio。

新增 Hubsan **59 个型号、1,147 项参数**，Skydio **9 个型号或配置、656 项参数**。采集工具、官方来源、资料统计、缺失项和复现命令见[采集记录](hubsan-skydio-collection-2026-11-13.md)。现有 DJI 82／Parrot 9／Autel 19 个型号保持可用。

## 实现范围

- 前端添加两个品牌、各品牌的型号搜索提示；切换时取消旧请求、清空旧筛选，沿用型号搜索、分类、参数分组、参数内容搜索及官方来源。五个品牌按钮在窄屏换行，悬停保持文字可读。Parrot 已移除的说明未恢复。
- `/drones` 和 `/drones/{slug}` 支持 `brand=hubsan`、`brand=skydio`；各品牌独立读取资料，未知品牌返回 422，型号不存在返回 404。
- Settings、`.env.example`、Compose 新增 `HUBSAN_DOCUMENTS_DIR`、`SKYDIO_DOCUMENTS_DIR`。生产将两份资料根目录只读挂载至 `/data/hubsan-documents`、`/data/skydio-documents`，数据不打包进镜像。
- 部署基于发布时实际运行的 `rag-graph-global:20261112-v1`。隔离重建与线上前端全部文件哈希一致后，仅覆盖 `DronesPage.tsx`、`DronesPage.css` 和品牌类型声明。后端仅更新 `api/drones.py`，在实际部署的 Settings 中插入两个目录字段。工作区其余未发布改动未纳入镜像。

## 验证结果

采集／后端相关 37 项测试及前端无人机页面 13 项测试通过，隔离生产构建通过。原始文件及参数来源哈希、目录参数数量与值通过校验，详见两份资料目录的 `校验报告.json`。

预览与正式容器均使用真实无人机 API，逐项比较五品牌所有型号详情与保存的基线；实际渲染全部 Hubsan、Skydio 和 Autel 型号，逐项比对参数值、数量和来源链接。检查黑鹰 712.7 克、Skydio 2／2+ 的 23／27 分钟、X2D INTL／ATO 独立配置及 Autel 不同地区重量条件。型号与参数搜索、参数分组、品牌切换和 1024／768／390／320 像素布局均通过，无页面脚本异常。两轮截图存放在发布目录的 `preview-drones/`、`production-drones/`。

正式环境现有图谱的全局重载、筛选清空、手机工具栏和名称字体通过回归验证；300 节点性能检查中布局及缩放期间 React 提交均为 0。iFixit 图片内容哈希保持一致，原运行环境、端口、已有挂载和其他容器 ID 保持一致，未运行数据库迁移。HTTP `/health` 返回 200。

## 发布记录与回退

发布记录位于 `data/releases/hubsan-skydio-ui-20261112-v1/`（12 日开始准备，13 日完成发布）。包含基线哈希证明、参数文件哈希、镜像清单、预览与生产报告、私有运行配置和回退脚本；目录权限 700，私有配置 600。预览容器已删除，其他任务的预览容器未操作。

旧应用保留为 `intelligence-rag-app-before-hubsan-skydio-ui-20261112`。生产镜像配置已同步至 `.env`。回退恢复旧容器及原网络配置，并更新镜像配置；脚本会核对当前镜像，防止覆盖后续发布。

```bash
bash data/releases/hubsan-skydio-ui-20261112-v1/rollback.sh
```
