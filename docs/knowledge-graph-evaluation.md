# 实体与关系金标准评测

本页统一说明数据、计分和复现方法。当前抽取规则见 [抽取与审核](knowledge-graph-extraction.md)，
构图操作见 [统一入口](knowledge-graph.md)。评测不写 Neo4j 或业务数据库。
历史策略哈希和分数仅适用于对应输出，不能作为当前代码或生产图谱的验收结论。

## 数据与保留文件

默认开发集含 20 段，中英文各 10 段；实体 v3 有 101 条实体，关系 v3 有 77 条关系：
70 条 `asserted`、7 条 `uncertain`。目前没有明确否定关系样本，不能据此判断否定抽取能力。
这些样本已经用于人工取舍与调参，不能解释为独立盲测成绩，也不设自动上线门槛。

| 用途 | 文件 |
| --- | --- |
| 当前人工实体答案 | [entity_gold_v3.json](../scripts/evaluation_data/entity_gold_v3.json) |
| 当前人工关系答案 | [relation_gold_v3.json](../scripts/evaluation_data/relation_gold_v3.json) |
| 标注边界 | [实体 v3 规则](../scripts/evaluation_data/entity_gold_guidelines_v3.md)、[关系 v3 规则](../scripts/evaluation_data/relation_gold_guidelines_v3.md) |
| 人工阅读版 | [实体 v3](entity-gold-v3-review.md)、[关系 v3](relation-gold-v3-review.md) |
| 历史配套答案 | [实体 v1](../scripts/evaluation_data/entity_gold_v1.json)、[关系 v1](../scripts/evaluation_data/relation_gold_v1.json) |
| 历史阅读版 | [实体 v1](entity-gold-v1-review.md)、[关系 v1](relation-gold-v1-review.md) |
| 人工取舍 | [24 项确认](entity-value-selection-review.md) |
| 校验、抽取及分析工具 | [数据工具](../scripts/entity_gold_dataset.py)、[实体评测](../scripts/evaluate_entity_gold.py)、[关系评测](../scripts/evaluate_relation_gold.py)、[实体错误分析](../scripts/analyze_entity_gold.py) |
| 保留的逐项报告 | [实体最近一轮](entity-gold-prompt-edit-repeat-2026-11-05.md)、[实体错误诊断](entity-gold-prompt-edit-repeat-errors-2026-11-05.md)、[重构 golden 对照](video-graph-refactor-golden-2026-11-10.md) |
| 逐轮历史结果 | [指标与原始 JSON 入口](knowledge-graph-evaluation-results.md)、[方案取舍及审计](knowledge-graph-history.md) |

历史实体 v1 的 106 条实体与关系 v1 的 70 条答案配套，新旧范围和分母不同，不能直接比较分数。
关系答案只允许实际评测 `human_reviewed` 数据；`--dry-run` 可校验草稿。
更改实体答案后须同步核查关系答案，不能混用版本；同名 v3 的内容变更依靠数据指纹区分。
金标准 entities/relations 是答案，历史 notes 不覆盖人工确认的取舍。

## 实体计分

模型只接收标题、当前正文、运行流程需要的上下文和正文匹配的词表候选；标准答案、models、notes 不传给模型。
实体经过独立审核、价值筛选和确定性归一；完整抽取还会运行关系分类与审核，实体分数不证明关系通过。
调用预算以当前脚本 `--dry-run` 为准，不沿用旧策略固定调用上限。

按片段内身份去重，一对一匹配规范名称或人工列出的原文叫法，只做 Unicode、大小写、空白及排版字符归一。
不自由翻译、不模糊补分、不由 LLM 评分；明确冲突的设备所有者不能匹配。
分别报告实体识别，以及同时要求类型正确的 P/R/F1；歧义与未匹配项继续保留供复核。
名称命中不能证明泛称补全出的具体型号正确。属性不计入实体得分。

TP 为命中，FP 为未匹配预测，FN 为未匹配标准；失败片段的全部标准实体仍计 FN。

## 关系计分

关系评测运行完整抽取流程，实体漏抽也可能导致关系漏抽；不是向模型输入正确实体后的独立关系分类实验。

| 指标 | 命中要求 |
| --- | --- |
| `triple` | 主体身份和类型、谓词、客体身份和类型、方向一致 |
| `assertion` | triple 成立，并且 asserted/uncertain/negated 一致 |
| `qualified` | assertion 成立，并且全部 qualifiers 严格匹配 |

- 端点按名称/mentions 与类型一对一对齐；明确冲突的所有者不能匹配。不根据标准关系反推部件归属。
- 优先匹配明确规范名，再匹配原文提及。泛称对应多个部件时保留歧义；范围内类型错误、映射冲突或归属不明的关系计未匹配预测。
- 同一身份的多个提及先合并；关系按片段内事实去重，不按引文数量重复计数，一个预测不能命中多个答案。
- 只有 `compatible_with`、`connected_to` 允许反向；其他谓词要求方向一致。不做传递路径、父子系列或谓词近义推断。
- 引文仍须通过原文、类型、身份和审核凭据校验，不要求与标准答案选取完全相同的引文边界。
- qualified 对全部键和值严格比较，值仅做基础字符归一；省略 modality 等价于 actual。限定同义但措辞或范围不同也可能扣分，应读取差异清单，不能把该分数当作语义裁定。

关系标准只覆盖已确认实体之间的关系。两个端点均可识别为标准实体范围内对象的预测才进入 Precision 分母；
其他预测单列 `out_of_scope`，待人工审查，不计 TP/FP。范围内端点还须正确对齐才能计 TP。
标准关系始终留在 Recall 分母；实体漏抽、执行失败和 pending 都不能移除答案。
这是既定实体范围内的端到端关系指标，不是全图所有预测关系的准确率。

Precision = TP/(TP+FP)，Recall = TP/(TP+FN)，F1 = 2TP/(2TP+FP+FN)。
错误谓词、方向或断言通常同时贡献一个 FP 和一个 FN。没有分母时记 null，不虚构 100%。
报告整体微平均、中英文及各谓词指标，同时展示总预测数、范围内/范围外数量。

## 运行与离线复现

在项目根目录运行。先校验数据、查看规模与预算，不构造模型客户端或调用模型：

```bash
.venv/bin/python scripts/entity_gold_dataset.py validate
.venv/bin/python scripts/evaluate_entity_gold.py \
  --output data/video_graph_runs/entity-eval-new.json --dry-run
.venv/bin/python scripts/evaluate_relation_gold.py \
  --output data/video_graph_runs/relation-eval-new.json --dry-run

# 仅在人工答案实际修改后更新阅读版
.venv/bin/python scripts/entity_gold_dataset.py render \
  --output docs/entity-gold-v3-review.md
```

新实验使用新的输出和缓存路径；报告也放入本地运行目录，避免再产生大量逐轮 docs 文件：

```bash
VIDEO_GRAPH_EXTRACTION_EFFORT=none VIDEO_GRAPH_REASONING_EFFORT=none \
.venv/bin/python scripts/evaluate_entity_gold.py \
  --output data/video_graph_runs/entity-eval-new.json \
  --response-cache data/video_graph_runs/entity-eval-new-responses \
  --report data/video_graph_runs/entity-eval-new.md --workers 2

VIDEO_GRAPH_EXTRACTION_EFFORT=none VIDEO_GRAPH_REASONING_EFFORT=none \
.venv/bin/python scripts/evaluate_relation_gold.py \
  --output data/video_graph_runs/relation-eval-new.json \
  --response-cache data/video_graph_runs/relation-eval-new-responses \
  --report data/video_graph_runs/relation-eval-new.md --workers 2
```

离线重放精确请求缓存，缓存缺失会失败且不调用模型：

```bash
.venv/bin/python scripts/evaluate_entity_gold.py \
  --output /tmp/entity-eval-replay.json \
  --response-cache data/video_graph_runs/entity-prompt-edit-repeat-responses-20261105 \
  --cache-only --workers 2
```

离线重评分使用已有实体/关系评测 JSON 中的完整原始抽取输出，无需模型密钥：

```bash
.venv/bin/python scripts/evaluate_relation_gold.py \
  --predictions data/video_graph_runs/existing-entity-evaluation.json \
  --output data/video_graph_runs/relation-eval-rescore.json \
  --report data/video_graph_runs/relation-eval-rescore.md
```

输入需含 `results[case_id].status` 及成功项完整 `output`，可含 `extraction_version`。
离线模式仍重验原文与当前审核凭据，不信任旧分数；缺失 case 列 pending 并计 FN。
历史策略凭据与当前策略不兼容时，应使用对应代码评分或重新运行，不能绕过验证。
精确缓存绑定完整请求、模型参数及提供方/账户，改提示词或服务配置后可能无法重放。

| 参数/行为 | 约束 |
| --- | --- |
| 已有输出 | 断点续跑，不重复完成项；改规则、输入或标注时使用新文件名 |
| `--retry-errors` | 仅重试执行失败，不能对成功但低分的片段重跑挑分 |
| `--case-id EG001 --case-id EG014` | 仅运行并评分选定片段，整体分母随之改变 |
| `--cache-only --response-cache <目录>` | 精确重放；不会用未匹配请求补位 |
| `--dataset`、`--entities` | 显式选择配套版本，并校验来源与实体一致性 |

复现 v1 时必须同时选择两份旧答案：

```bash
.venv/bin/python scripts/evaluate_relation_gold.py \
  --dataset scripts/evaluation_data/relation_gold_v1.json \
  --entities scripts/evaluation_data/entity_gold_v1.json \
  --output data/video_graph_runs/relation-v1-replay.json --dry-run
```

结果记录数据、策略、评分器及离线预测指纹，不得覆盖输入金标准。
全部选定片段成功且没有 pending 时返回 0；低 F1 本身不是执行失败。

## 历史分数的解释

同一 101 条实体标准、同一提示词的两轮实体及类型结果为 87/10/14（TP/FP/FN，F1 87.9%）
和 89/11/12（F1 88.6%）。这是重复运行差异，不是两次规则改进的因果对照。
108 条旧答案快照重评为 101 条时，原始模型输出保持不变；原始输出、缓存、快照和重评 JSON 仍保留。

关系策略 c88 的历史 assertion F1 为 68.2%，全新缓存复测 65.7%；相同实体受控组中，
f39 关系优化轮为 75.7%，另一组为 72.5%。token 与模块重构使用的另一份固定输出为 75.4%。
这些是不同轮次，不能混写成同一版本的稳定得分或最新线上效果。
完整指标及原始路径见 [评测汇总](knowledge-graph-evaluation-results.md)，取舍见 [历史记录](knowledge-graph-history.md)。
