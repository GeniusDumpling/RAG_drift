# Skydio 官方文档与型号参数

在仓库根目录运行：

```bash
.venv/bin/pip install -r tools/skydio_documents/requirements.txt
.venv/bin/python -m tools.skydio_documents collect
.venv/bin/python -m tools.skydio_documents verify
```

默认目录 `data/Skydio无人机文档`。支持 `discover`、`download`、`build-catalog`、`verify`、`report`，以及 `--output PATH`、`--workers 1..8`（默认 4）、`--refresh`。可重复运行续采，缓存须通过 SHA256 校验；同一目录使用文件锁。无需账号和模型服务。

采集官方支持中心公开 Help Center API 的全部分类和分页文章，跳过草稿，下载其中明确链接的 PDF 手册、维护指南、清单等。API JSON 原文与文章正文分别保存；派生 HTML 记录 `capture_kind=help_center_api_body`、API 来源 URL／哈希和文章更新时间，不冒充网页响应原文。额外采集 X10、X10D、R10 官方技术规格页。域名限 Skydio 官方站和官方页面实际链接的 `cdn.sanity.io` 文档，重定向同样受限；Adobe 在线查看器链接不作为已下载 PDF，文章原链接仍保留。

归档输出与 Hubsan、Autel 相同，包括来源快照、原始 PDF、文档索引、进度、统计和失败清单；多入口引用保留关联，相同 URL 只下载一次。英文页面中的日语、西班牙语和乌克兰语手册依据标题和分区标记语言，不因页面语言而统一标为英语。

`机型参数目录/` 为前端使用的型号目录，包含 CSV 索引和每型号 HTML／TXT／JSON：

- X10、X10D、R10：提取官网明确的双列表格，保留表格外的测试条件和配置差异。
- Skydio 2 与 2+：同一操作手册内按明确的型号规格页分别提取，23 分钟和 27 分钟等参数不混用。
- X2E、X2D INTL、X2D ATO：使用对应操作手册的规格表，INTL／ATO 分开，JSON 保留页码及表格坐标。
- R1：目前收录官方 FAQ 的飞行和影像信息，标记为部分资料；没有从其他型号推算参数。

`build-catalog` 只读本地归档，检查源哈希，不发送网络请求。新增型号或手册版本须在 `collector.py` 中明确来源及规格页标记，避免把共享手册的其他型号当成当前型号。未取得对应来源的配置记录在 `技术参数缺失清单.json`。

`verify` 对所有成功归档重验 SHA256 并打开 PDF，校验失败返回 1；存在来源失败或待下载项时命令返回 2，查看失败清单定位。应用通过 `SKYDIO_DOCUMENTS_DIR` 指向资料根目录，读取其中 `机型参数目录`，Docker 只读挂载。

```bash
.venv/bin/pytest -q tools/drone_collection/tests tests/unit/test_drone_brands.py
.venv/bin/ruff check tools/drone_collection tools/skydio_documents
```
