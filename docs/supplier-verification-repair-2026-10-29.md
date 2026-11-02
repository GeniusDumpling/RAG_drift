# 供应商验证字段与多来源证据修复

当前 systemd 任务直接执行宿主机项目中的脚本，以下修复会在下一次任务启动时生效。

## 修复内容

- 验证模型优先输出 JSON，每个 `evidence` 对象独立保存一个来源 URL 和一段连续原文。
- 兼容旧 Markdown 的多行字段、加粗字段名、中英文冒号及多个证据块。
- URL 解析识别中英文分号、中文逗号等分隔符，避免把多个来源拼为一个 URL；保留 URL 去片段处理。
- 旧格式包含多段引号引用时，每段分别匹配其列出的来源全文。JSON 每条证据只允许一个 URL；任意一条引用不匹配时仍拒绝确认或否定结果。
- 机型、年份仅从通过引用校验的来源中核验。带括号注释的供应商名使用括号前名称定位长文证据。
- 收到模型响应后、执行解析前，立即保存响应及输入正文。模型截断、解析异常、引用校验失败后均保留诊断。
- 异常携带已完成的搜索、抓取计数，失败报告与数据库证据中保留错误及模型原文，不再统一丢弃现场。

## 诊断文件

每条验证的独立 JSON 文件位于：

```text
tools/supplier-information/search_results/<brand>/_index/verification_runs/<timestamp>_<uuid>.json
```

内容包含 `raw_response`、`response_attempts`（含接口返回的完整 JSON）、`source_documents`（实际送入模型的正文片段）、`evidence_items`、搜索/抓取计数和失败原因。目录权限 0700、文件权限 0600，写入通过临时文件替换完成；不记录请求凭据。

正常批次报告的 JSON 同样保存每条记录的原始响应与诊断路径，Markdown 报告列出诊断路径。数据库无需新增字段。

## 验证与定向重试

29 项相关测试通过，覆盖：品牌隔离、PDF/表格、结构化多来源引用、旧 Markdown 兼容、来源错配与伪造引用拒绝、失败响应和截断响应留存；修改代码通过 Ruff 检查。

新增 `--supplier` 参数（可重复），与 `--brand`、`--mode failed` 组合，仅重试指定供应商。此次限定以下六条关系，保留历史审计，追加新的验证记录：

| 品牌 | 供应商 |
| --- | --- |
| Parrot | Amazon |
| Parrot | FLIR（原文以 FLIR Boson® 品牌/模块名出现） |
| Parrot | Qualcomm |
| Parrot | SONY |
| Parrot | STMicroelectronics |
| Skydio | FLIR |

通过 `rag-supplier-parrot-retry-20261029.service`、`rag-supplier-skydio-retry-20261029.service` 临时用户服务运行，读取项目 `.env`，使用与定时任务相同的品牌锁。常规定时任务仍为 `unverified` 模式。

## 实际重试结果

2026-10-29 已完成六条定向重试，全部结论为“确认”，执行状态为 `success`，关系状态更新为“已验证”。历史失败审计保留。

- Parrot：17:20:18 开始，17:26:57 完成，五条均确认。
- Skydio：17:26:58 开始，17:28:39 完成，FLIR 确认；三段证据分别通过对应来源正文校验。
- 六份诊断文件均保存非空原始响应和完整接口 JSON；重新离线核验全部引用与来源正文一致，文件权限均为 0600。
- 数据库核验：Parrot 15 条、Skydio 7 条关系记录均处于“已验证”，当前“验证失败”记录为 0。该状态仅表示流程完成；Skydio 既有的 Qualcomm 结论仍是“待确认”。
- 两个已完成的临时服务已停止，四个每半小时运行的常规定时器仍为 `active`。

批次报告：

```text
tools/supplier-information/search_results/parrot/_index/verification_20261029_172657.md
tools/supplier-information/search_results/parrot/_index/verification_20261029_172657.json
tools/supplier-information/search_results/skydio/_index/verification_20261029_172839.md
tools/supplier-information/search_results/skydio/_index/verification_20261029_172839.json
```
