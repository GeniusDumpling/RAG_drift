# 无人机供应商抽取与验证

默认品牌仍为大疆；通过 `--brand dji|parrot|skydio` 切换。品牌名、别名、检索语言、官方起始来源以及每次采集/验证数量在 `conf.yaml` 的 `brands` 下配置。

## 安装与数据库

```bash
uv sync --extra supplier
uv run alembic upgrade head
```

`0009_supplier_multibrand` 在两张供应商表新增 `drone_models`、`evidence_years`，在关系表新增 `evidence_items`，在验证表新增 `execution_status`；均为新增列，旧记录保留，旧记录的机型和年份默认为空数组。数据库原有 `(buyer_name, supplier_name)` 唯一键继续使用，查询和更新都按品牌隔离。

凭据读取项目根目录 `.env`：

- `BASE_URL`、`LLM_API_KEY`、`LLM_MODEL`：沿用项目统一 LLM 配置。
- `BRAVE_SEARCH_API_KEY`：英文品牌优先使用的 Brave Web Search。
- `BAIDU_QIANFAN_API_KEY`：百度搜索及自动回退。
- `DATABASE_URL` / `SYNC_DATABASE_URL`：供应商数据库。

`--provider brave|baidu|auto` 选择搜索源。显式指定时错误直接上报；`auto` 优先 Brave，在认证或限流错误时可回退至已配置的百度。Parrot、Skydio 默认执行英文和中文多条查询并合并去重，而不是仅翻译一个关键词。搜索结果和官方起始来源共同参与采集；`--max-results` 限制最终采集文档数，起始来源优先。

## 执行

从项目根目录运行：

```bash
# 小样本：1 份 Parrot 官方白皮书，以及 3 份 Skydio 官方资料
.venv/bin/python tools/supplier-information/pipeline.py --brand parrot --provider brave --max-results 1
.venv/bin/python tools/supplier-information/pipeline.py --brand skydio --provider brave --max-results 3

# 各验证最多 2 家；原始来源和新搜索结果都用于验证
.venv/bin/python tools/supplier-information/supplier_verify.py --brand parrot --max-suppliers 2 --fetch-limit 2
.venv/bin/python tools/supplier-information/supplier_verify.py --brand skydio --max-suppliers 2 --fetch-limit 2

# 重验失败项：0 表示不限制供应商数量
.venv/bin/python tools/supplier-information/supplier_verify.py --brand parrot --mode failed --max-suppliers 0

# 重验全部项，或强制重新抓取/分析已知 URL
.venv/bin/python tools/supplier-information/supplier_verify.py --brand skydio --mode all --max-suppliers 0
.venv/bin/python tools/supplier-information/pipeline.py --brand parrot --force
```

可用 `--config /path/to/conf.yaml` 指定独立配置。命令行覆盖只影响本次进程。

## 文档、证据和隔离

- HTTP MIME 和 PDF 文件头共同识别 PDF，支持没有 `.pdf` 后缀或经重定向返回的 PDF。
- PyMuPDF 提取全部 PDF 页面文字及可识别表格，保留页码；HTML 表格按行列展开并保留合并单元格关系。扫描版 PDF 无可提取文本时明确报错，目前不自动 OCR 整份扫描 PDF。版式特殊的表格仍可能需要人工复核。
- 原始 PDF 和解析 JSON 保存在品牌索引的 `documents/`。长文档按有界文本段分片，避免超出模型上下文。空或截断的 LLM 响应会重试、增大输出预算，最终失败不会当成“零关系成功”。
- 关系只有在采购方匹配当前品牌、来源 URL 属于已抓取文档、引用原句能在正文匹配时才进入正式名单。JSON 结果保存通过原文校验的关系；原始 LLM Markdown 供排错使用。
- `drone_models` 和 `evidence_years` 可为空；不以抓取年份或页脚版权年份填补未知年份。`evidence_items` 逐条关联 URL、机型、年份、原文引用和关系类型，避免把不同机型/年份误拼成一条证据。
- `直接供货`、`器件采用`、`OEM代工` 分开记录。官方白皮书说明使用某芯片，仅证明器件采用，不自动证明品牌直接向芯片厂商采购。经销商销售整机不属于上游供货关系。
- 验证结果为 `确认/否定/待确认`；网络或模型错误另标 `execution_status=failed`，支持 `--mode failed`。每次结果追加为审计记录并更新对应品牌的当前验证状态。

大疆继续使用 `search_results/_index/`；其他品牌使用 `search_results/<brand>/_index/`。已知 URL、关系索引、正式名单、原始文档缓存和报告均按品牌隔离。pipeline 只有在分析和入库成功后才将 URL 标为已处理，失败可再次尝试。

API `GET /supplier/relations?buyer_name=Parrot` 或 `buyer_name=Skydio` 可按品牌过滤，并返回新增字段；通用数据库查看接口也提供这些字段。

## 测试

```bash
.venv/bin/pytest -q tests/unit/test_supplier_multibrand.py backend/tests/test_supplier_web_fetch.py backend/tests/test_supplier_schema.py tests/unit/test_database_supplier_tables.py tests/unit/test_llm_config.py
.venv/bin/pytest -q backend/tests/test_supplier_multibrand_db.py
```

最后一个测试使用名称以 `_test` 结尾的专用 PostgreSQL 测试库，验证相同供应商在两个品牌下独立入库、验证记录关联、机型/年份往返、品牌过滤和失败重试。
