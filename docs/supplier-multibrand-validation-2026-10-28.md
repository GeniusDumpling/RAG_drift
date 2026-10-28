# 无人机供应商多品牌改造验证记录

验证日期：2026-10-28，北京时间。实际使用项目 `.env` 中统一 LLM 配置及已更新的 Brave key。

## 实际运行结果

| 品牌 | 输入 | 入库供应商 | 独立验证通过 | 待验证 |
|---|---|---:|---:|---:|
| Parrot | ANAFI USA 官方白皮书，36 页、7 个表格 | 6 | 2 | 4 |
| Skydio | Nidec 公告、FLIR 公告、Skydio X10 官方产品页 | 3 | 2 | 1 |

正式名单仅收录采购方符合当前品牌、来源 URL 和引用原句通过匹配的关系。这是有限样本验证，不是完整供应商名单。

- Parrot：AKM、Ambarella、FLIR、Invensense、ST Microelectronics、U-BLOX；机型为 ANAFI USA。证据年份 2024，来自白皮书封面的 `Updated: July 31st, 2024`。AKM 与 Ambarella 独立验证结论为“确认”。
- Skydio：NVIDIA、Nidec Corporation、Teledyne FLIR；机型为 Skydio X10。NVIDIA 与 Nidec 的证据年份为 2024，独立验证结论均为“确认”；本次抽取未可靠取得 FLIR 来源年份，保持空数组。
- 以上“确认”针对文档明确披露的器件采用关系，不代表已经证明品牌与芯片厂商存在直接采购合同，也不代表 2026 年仍在供货。
- AKM 首次验证因原文引用未通过严格匹配而被标为失败，重试后通过；失败审计保留，最新状态为已验证。

额外解析检查中，ANAFI UKR 官方白皮书成功提取 104 页、52 个表格；这份文档没有作为上述 6 家统计的分析输入。Nidec 来源实际响应为 PDF，验证了没有 `.pdf` 后缀的链接也能正确识别。独立验证还抓取到了带 HTML 表格的页面。

## 运行命令

```bash
.venv/bin/python tools/supplier-information/pipeline.py --brand parrot --provider brave --max-results 1 --force
.venv/bin/python tools/supplier-information/pipeline.py --brand skydio --provider brave --max-results 3 --force
.venv/bin/python tools/supplier-information/supplier_verify.py --brand parrot --provider brave --max-suppliers 2 --fetch-limit 2
.venv/bin/python tools/supplier-information/supplier_verify.py --brand skydio --provider brave --max-suppliers 2 --fetch-limit 2
.venv/bin/python tools/supplier-information/supplier_verify.py --brand parrot --provider brave --mode failed --max-suppliers 0 --fetch-limit 1
```

PDF 日期传递修正后，对已保存的 Parrot LLM 总结重新做原文校验及入库，没有重复调用 LLM。重新处理同一 run 不增加关系的出现次数，同一原文证据只补充年份。

## 数据与产物

- 数据库已应用 `0009_supplier_multibrand`，最终只读核对：大疆 74 家、Parrot 6 家、Skydio 3 家；本次新品牌入库没有增加大疆供应商数量。
- 品牌名单：`tools/supplier-information/search_results/parrot/_index/confirmed_relations.md`、`tools/supplier-information/search_results/skydio/_index/confirmed_relations.md`。
- 抽取日志：`pipeline_parrot_20261028_200312.log`、`pipeline_skydio_20261028_200314.log`，位于工具的 `logs/`。
- 验证报告：Parrot 的 `verification_20261028_200916.md` 及重试报告 `verification_20261028_201651.md`；Skydio 的 `verification_20261028_201316.md`，位于各自 `_index/`。
- 每份抽取总结、验证报告有 JSON 产物；原始 PDF 和按页解析结果保存到各品牌 `_index/documents/`。这些运行产物由仓库忽略规则保留在本地，不作为源码提交。

## 自动检查

34 项相关单元测试、1 项 PostgreSQL 集成测试通过；所改供应商模块静态检查和 `git diff --check` 通过。

覆盖品牌索引与相同供应商隔离、PDF 正文/页码/表格、无扩展名 PDF、HTML 合并单元格、英文搜索、自动回退、引用原文校验、未知年份、PDF 日期跨分片传递、引用页码与标点兼容、空/截断 LLM 输出、失败 URL 重跑以及同 run 元数据重处理。

使用方法和配置说明见 [工具 README](../tools/supplier-information/README.md)。
