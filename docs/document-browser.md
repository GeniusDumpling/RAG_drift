# 管理页面查看文档处理数据

管理 → 数据库：选择业务库 `intelligence_rag`、文献库 `literature_research` 或文档库
`drone_documents`，查看对应的表、分页记录和向量集合状态。数据库及表名使用固定白名单，
不提供任意数据库连接串或 SQL 执行入口。文献库未使用向量集合时显示“不适用”。

管理 → 内容详情 → 文档处理数据：按型号、厂商或标题查找文档。默认展示当前发布的处理
版本；没有发布版本时展示最新导入版本，并提示尚未发布。可切换历史版本，查看正文与
元素、恢复的表格、原图与图片说明质量、参数元数据和来源差异、检索切片及向量化输入、
中文筛选和排除页。原文页码支持定位与分页；HTML 技术参数没有 PDF 页面。
只有已经导入文档库的任务会出现在列表中，刷新可查看后台新增记录。

接口：`/database/catalog`，原数据库浏览接口增加 `database=primary|literature|documents`；
文档浏览接口位于 `/document-library`，详情及分页数据接受 `processing_id`，避免混用处理版本。
文档浏览沿用 `DATABASE_BROWSER_ENABLED` 管理接口开关。生产环境关闭开关时返回 404。

文档库和文献库的管理连接使用数据库级只读事务，设置查询和锁等待超时。
浏览功能不调用图文或向量模型，不触发解析、导入、索引或重新处理。

## 文档图片挂载

应用需要访问已保存的图片文件。Compose 将产物目录只读挂载到 `/data/document-artifacts`：

- `DOCUMENT_DATABASE_URL`：独立文档库连接；未设置时沿用业务库服务器并改库名为 `drone_documents`。
- `DOCUMENT_QDRANT_URL`：默认沿用 `QDRANT_URL`。
- `DOCUMENT_QDRANT_COLLECTION`：默认 `drone_document_chunks_bge_m3_v1`。
- `DOCUMENT_ARTIFACTS_DIR`：应用内可读的文档产物目录。
- `DOCUMENT_ARTIFACTS_SOURCE_ROOT`：数据库中记录的原宿主机产物目录前缀，容器通过该前缀换算挂载位置。

本地运行且目录未搬迁时无需设置 `DOCUMENT_ARTIFACTS_SOURCE_ROOT`。
资产接口只接受数据库中的资产 UUID，并校验路径仍在允许的产物目录内，限制为 PDF 和
PNG/JPEG/WebP；不接受任意文件路径，不直接渲染 HTML 或 SVG。

## 更新镜像

`docker/Dockerfile.admin-update` 基于已部署镜像，只替换构建后的前端和指定后端文件。
发布时仅更新 `app`，使用 `--no-deps --no-build` 并设置 `RUN_MIGRATIONS=0`。
文档批处理在宿主机执行，保持原 PID；数据库和 Qdrant 容器无需重建或重启。
