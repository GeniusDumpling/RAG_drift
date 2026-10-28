# 无人机信息页品牌切换

“无人机信息”增加 DJI 大疆 / Parrot 切换，分别加载本地品牌目录；默认保持 DJI。切换时立即隐藏旧目录和参数、重置型号/类型/参数筛选，取消旧请求。目录和详情状态同时关联品牌，避免同名 slug 或延迟返回的数据串入另一个品牌。

型号详情新增可选的资料版本和更新日期。Parrot 的原始采集时间未提供时不生成采集日期；PDF 参数的原文条件与脚注继续作为参数值显示。

## 接口与配置

- `GET /drones`、`GET /drones/{slug}` 保持默认 DJI。
- `?brand=dji` / `?brand=parrot` 在列表和详情接口均可使用；未知品牌返回 422。
- 某个品牌目录缺失返回该品牌的 503 错误，另一品牌仍可读取。
- 本地 DJI 目录使用 `DRONE_DOCUMENTS_DIR`，Parrot 使用 `PARROT_DOCUMENTS_DIR`。
- Compose 为应用增加 Parrot 只读挂载：宿主机 `PARROT_DOCUMENTS_DIR` → `/data/parrot-documents`。DJI 原挂载保留。

当前本地目录：DJI 82 个型号、7,318 项参数；Parrot 9 个型号/版本、643 项参数。两个目录的全部型号详情均已直接读取并核对数量。

## 验证

- 前端页面与主导航：13 项测试通过，包括品牌切换、筛选清空、迟到目录响应、同 slug 迟到详情、缺失目录后切回。
- 品牌接口与原 DJI API：7 项测试通过，包括默认品牌、参数校验、404、品牌独立的 503 和版本字段。
- Compose 与 Parrot 工具：17 项测试通过，包含两个品牌资料的只读挂载核对。
- TypeScript 与 Vite 生产构建通过；相关 Python 文件 Ruff 检查通过。
- 真实 Chromium：桌面 1440×1100、手机 390×844；读取真实 `/drones` 接口及本地参数文件，核对 82 / 9 个型号、版本信息、参数搜索及品牌来回切换。无页面脚本异常，无手机横向溢出。仅初始总览页的四个非无人机接口返回空列表，避免访问无关业务数据库。

接口线程测试与 Chromium 在沙箱内受 socket 权限限制，已在沙箱外重跑并通过。

浏览器产物：

- [DJI 桌面截图](../data/parrot-brand-ui/20261028/desktop-dji.png)
- [Parrot 桌面截图](../data/parrot-brand-ui/20261028/desktop-parrot.png)
- [Parrot 手机截图](../data/parrot-brand-ui/20261028/mobile-parrot.png)
- [浏览器验证报告](../data/parrot-brand-ui/20261028/report.json)

## 发布状态

已部署至原 8000 端口，镜像为 `rag-drone-brands:20261028-v1`，应用为 healthy。新增 Parrot 只读目录挂载，正式页面品牌切换、参数及手机布局均已验证。没有写入业务数据库、重建向量或修改原始资料。发布与回退信息见 [部署记录](drone-brand-deployment-2026-10-28.md)。
