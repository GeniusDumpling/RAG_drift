# Autel 官方资料采集

从 Autel 中英文官网的下载中心、产品页、历史菜单和官方商店支持页发现公开 PDF，归档到 `data/Autel无人机文档`。不需要账号、API Key 或大模型。原始文件、来源和采集时间保留，失败记录单独输出。

安装依赖并完整采集（在仓库根目录运行）：

```bash
.venv/bin/pip install -r tools/autel_documents/requirements.txt
.venv/bin/python -m tools.autel_documents collect
.venv/bin/python -m tools.autel_documents verify
# 生成前端专用的无人机型号/系列目录
.venv/bin/python -m tools.autel_documents build-catalog
```

已有项目虚拟环境通常包含这些依赖，可直接运行。重复执行 `collect` 会校验缓存哈希并复用完整文件，重试失败项；中断后仍执行同一命令。相同 URL 的 PDF 只下载一次，多个入口保留各自索引。不同 URL 的相同内容通过 SHA256 识别，保留来源关联。

分阶段执行及筛选：

```bash
# 只采集网页并生成 PDF 清单，不下载 PDF
.venv/bin/python -m tools.autel_documents discover
# 查看已有目录的统计、按型号/系列分组的机型采集记录.csv
.venv/bin/python -m tools.autel_documents report
# 仅从已有原始网页重新提取标题、日期、版本、参数，不发起网络请求
.venv/bin/python -m tools.autel_documents reindex
# 下载清单中的全部 PDF，或仅下载指定 scope
.venv/bin/python -m tools.autel_documents download
.venv/bin/python -m tools.autel_documents download --scope evo-nano --scope evo-lite
# 官方页面更新后，重新获取；已有原始文件按内容哈希保留
.venv/bin/python -m tools.autel_documents collect --refresh
# 增加官网新入口
.venv/bin/python -m tools.autel_documents collect --seed https://www.autelrobotics.cn/doc/evo-lite-ent/
```

可配置 `--output`、`--workers`（默认 4，最多 8）、`--timeout`、`--retries`、`--delay`（同一主机最短间隔，默认 0.3 秒）、`--max-pages`（默认 400）、`--max-documents`、`--max-pdf-mb`（默认 150）。达到数量上限会保留待采集项并返回退出码 2；校验失败返回 1。来源 HTTP 失败记录在汇总和失败清单中，不以退出码 0 表示官网全部可用。

输出结构与 DJI 原始资料目录对应：

```text
data/Autel无人机文档/
  <型号或系列>/原始标题_URL哈希_内容哈希.pdf
  <产品页标识>/技术参数.html / .txt / .json
  网页文档/<入口标识>/<URL哈希>-<内容哈希>.html / .txt
  文档索引.csv             # DJI 同名字段 + 厂商、版本、语言、类型、采集时间
  文档清单.json           # 完整来源列表，包括共享文件的全部来源页面
  机型采集记录.csv         # 按入口分组，配件/软件单列类型
  网页采集记录.csv         # 原始 HTML 快照、TXT 路径和来源哈希
  技术参数索引.csv         # DJI 同名字段，附原始网页路径、哈希、scope 类型
  资料目录.html            # 可直接打开的本地 PDF 目录
  汇总.json / 失败清单.json / 校验报告.json
  采集状态.json / 下载进度.jsonl
```

范围和数据含义：

- 自动跟随官网产品、文档和下载页，不抓取整个新闻站或电商商品目录。官方页面明确链接的 Shopify CDN PDF，以及 `app.autelrobotics.com` 实际跳转使用的 `d42ar9bw0cz7m.cloudfront.net` 可以下载；每次重定向也检查域名，不接受任意 CloudFront 子域。不使用站点脚本内的凭据，也不枚举受限云存储。
- 文档中心 `manuals.autelrobotics.com` 是 JavaScript 动态目录，其空壳网页不能代表文件清单；脚本对此记录 `javascript_directory_not_enumerated`，主要从公开官网 PDF 链接发现文件。
- 保存手册、快速指引、安全说明、更新说明、产品画册等 PDF，以及产品/下载网页。固件、APP、视频和图片压缩包不下载。
- 每个 scope 是官方型号、系列、配件或软件入口。系列手册不会被推断成每个变体的独立手册；scope 数量不等于独立机型数量。
- `build-catalog` 从校验过哈希的本地快照生成 `机型参数目录/技术参数索引.csv` 和各型号 HTML/TXT/JSON，供应用读取。目录去除中英文重复路由和独立配件/软件入口；官网明确标记的 EVO II V2/V3 分开，Dragonfish Standard/Pro 只依据明确的表头列提取。已收录 19 个型号或系列、2,493 项参数，未将系列共用参数推断成单个变体参数。
- 参数来源为官网已识别的 `.insrecruit-list` 双列表格，输出兼容现有 DJI 参数读取器的 HTML。原文单位、地区、电池和测试条件保留；多列或合并表格不猜测对应关系，标为 `partial`，完整表格仍保存在原始 HTML。没有识别到这种表格的页面仅保存快照，不伪造参数。
- 原始页面中英文均尝试采集。官网 403、404、超时均如实记录；不把错误 HTML 保存为 PDF，不以中文站内容冒充英文原文。语言字段按文件名和标题初判，不是翻译结果。
- 本工具采集和生成本地目录，不修改已有 DJI/Parrot 目录，不入库、不调用付费模型。应用通过 `AUTEL_DOCUMENTS_DIR` 指定原始资料根目录，并读取其中的 `机型参数目录`。

验证会重新读取索引中的所有成功文件，检查 SHA256，并用 PyMuPDF 实际打开 PDF、核对页数；参数文件还检查原始网页哈希和参数数量。

```bash
.venv/bin/pytest -q tools/autel_documents/tests
.venv/bin/ruff check tools/autel_documents
```
