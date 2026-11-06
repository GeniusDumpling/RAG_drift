# 关系金标准评测

当前版本`f39b9656b91a`的谓词/方向规范、漏抽定位及双组结果见[本轮优化报告](relation-direction-and-recall-2026-11-05.md)。旧版基线的实体与关系逐项结果见[20段对照表](entity-relation-gold-c88-comparison-2026-11-05.md)。

入口：`scripts/evaluate_relation_gold.py`。默认配套使用`relation_gold_v3.json`与`entity_gold_v3.json`；20个片段，中英文各10段，101条实体、77条关系（70条asserted、7条uncertain）。用户已确认新版关系集人工复核完成，状态为human_reviewed，可用于基线评测；实际评测只接受human_reviewed，dry-run允许校验草稿。重建差异与标注边界见[关系v3标注说明](../scripts/evaluation_data/relation_gold_guidelines_v3.md)。

历史`relation_gold_v1.json`及其70条人工答案保留，配套实体文件仍为`entity_gold_v1.json`；新旧答案范围和分母不同，成绩不直接比较。实体集再次变化时必须明确重建关系答案，校验会拒绝混用版本。

当前基于用户恢复的 `c88ca83a8725` 优化关系层，版本为`f39b9656b91a`。本版测试**完整抽取流程中的关系结果**，包含实体识别、实体审核/价值筛选与归一、关系分类、独立关系审核。关系层增加角色问题、外部软件应用的规范表达及有界引文修复，实体层不变。模型仅接收视频标题与当前字幕，不接收金标准实体、关系、models或notes；金标准只在评分时使用。因此漏抽实体也可能造成关系漏抽，本版不是“把正确实体送给模型后只测关系分类”的独立实验。基线恢复核验见[旧版重放报告](relation-gold-v3-restored-c88-2026-11-05.md)；四环节候选保留为[历史实验](video-graph-entity-relation-flow-2026-11-05.md)。

不写Neo4j或业务数据库；模型调用、精确请求缓存、错误诊断复用现有实体评测逻辑。

## 计分口径

| 指标 | 命中条件 |
|---|---|
| triple | 主体身份和类型、谓词、客体身份和类型、方向一致 |
| assertion | triple成立，且asserted/uncertain/negated一致；建议用来比较当前关系抽取基线 |
| qualified | assertion成立，且全部qualifiers严格匹配，用于发现条件/模式丢失或差异 |

- 端点沿用实体评测的规范名或mentions匹配：Unicode、大小写、排版和空白归一，要求类型相同；明确冲突的设备型号所有者不能匹配。不用自由翻译、模糊子串、LLM自评或标准关系反推部件归属。
- 优先匹配明确规范名称，否则使用原文提及。一个泛称camera同时对应多个标准部件时不任意选择所有者，保留歧义清单；可识别但类型错误、映射冲突或归属不明的范围内关系计未匹配预测。
- 实体按一对一对齐；同一规范身份的多个原文提及先合并。关系按片段内事实计数，不按引文数量计数；完全重复的关系只计一次，同一预测也不能重复命中多个答案。
- `compatible_with`、`connected_to`语义对称，允许反向；其他谓词要求方向一致。不做传递路径、父子系列、谓词近义替换推断。
- quote仍须通过现有抽取器的原文、类型、身份和审核凭据校验，但不要求与金标准选取完全相同的引文边界。
- qualified对限定键和值做严格比较，值只作基础字符归一；省略modality等价于actual。actual与capability、不同scope摘录边界或同义限定可能得到差异，即使人工认为意思接近。这项分数是严格记录匹配，不是语义裁定，应读差异清单。

TP为命中、FP为多抽/未匹配、FN为漏抽。Precision=TP/(TP+FP)，Recall=TP/(TP+FN)，F1=2TP/(2TP+FP+FN)。错误的谓词、方向或断言通常贡献一个FP和一个FN；没有相应分母时记为null，不虚构100%。输出微平均整体分数，并按中英文、谓词分别统计。

## 评测范围

关系金标准只标注已确认实体之间的关系，并非原文全部实体的全部关系。两个端点均能在标准实体范围内识别的预测才进入关系Precision分母；无法对应标准实体范围的预测单列`out_of_scope`，等待人工审查，不直接当作错误，也不当作命中。范围内关系的端点必须进一步正确对齐才能计TP。

标准关系始终保留在Recall分母；实体漏抽、失败或尚未运行都不能移除标准关系。因而此处是**既定实体范围内的端到端关系指标**，不是全图所有关系的准确率。报告同时给出预测总数、范围内与范围外数量，避免忽略评测覆盖率。

当前没有明确否定关系样本，不能据此判断否定关系抽取能力。已复核的取舍如EG008删去部分关系，按用户确认后的答案计分，不根据模型预测补回金标准。20段是开发评测集，不设置自动上线门槛。

## 使用命令

仅校验数据与查看规模，不构造模型客户端、不调用模型：

```bash
.venv/bin/python scripts/evaluate_relation_gold.py \
  --output data/video_graph_runs/relation-gold-v3-baseline.json --dry-run
```

运行当前抽取器并保存原始输出、逐段匹配和Markdown报告：

```bash
.venv/bin/python scripts/evaluate_relation_gold.py \
  --output data/video_graph_runs/relation-gold-v3-baseline.json \
  --report docs/relation-gold-v3-baseline.md \
  --response-cache data/video_graph_runs/relation-gold-responses --workers 2
```

复用**已有实体评测或关系评测JSON中的完整原始抽取结果**，离线重新计分，无需模型密钥：

```bash
.venv/bin/python scripts/evaluate_relation_gold.py \
  --predictions data/video_graph_runs/existing-entity-evaluation.json \
  --output data/video_graph_runs/relation-gold-v3-from-existing.json \
  --report docs/relation-gold-v3-from-existing.md
```

输入需含`results[case_id].status`与成功项的完整`output`，可含`extraction_version`。离线模式重新检查原文与当前审核凭据，不信任旧分数；过期或错误凭据记为执行失败，缺少的case明确列为pending并计FN，返回非零退出码。历史抽取策略的凭据若与当前策略不兼容，应使用对应代码版本评分或重新运行，不绕过验证。

其他常用参数：

- `--case-id EG001 --case-id EG014`：只运行/评分指定片段，结果和整体分母也仅包含这些片段。
- `--retry-errors`：仅重试失败项；成功但效果不佳的样本不会自动重跑。默认断点续跑，不重复已完成项。
- `--cache-only --response-cache <目录>`：只重放精确匹配请求；缓存缺失记执行错误，不调用模型。精确缓存与实体评测共用请求命名空间，可复用相同片段及尝试序号下的相同请求；改提示词后的不匹配请求不会命中。
- `--dataset`与`--entities`：显式选择配套的关系集、实体集，两者必须通过来源/实体一致性校验。

复现历史v1时必须同时指定两份旧文件：

```bash
.venv/bin/python scripts/evaluate_relation_gold.py \
  --dataset scripts/evaluation_data/relation_gold_v1.json \
  --entities scripts/evaluation_data/entity_gold_v1.json \
  --output data/video_graph_runs/relation-gold-v1-baseline.json --dry-run
```

结果记录金标准、配套实体集、抽取版本、评分器及离线预测文件指纹。更换标注、策略、输入预测或评分规则时使用新结果文件名；报告和结果不能覆盖输入金标准。评测逐段保存进度；只有全部选定片段执行成功且没有pending时返回0，低F1本身不等于脚本执行失败。

## 2026-11-05关系层优化

本轮基线策略`c88ca83a8725`细化关系提示词与谓词选择、纠正类型唯一的反向关系，并修复审核时的范围字段保留。相同77条答案、相同模型与评分规则下，关系加断言F1从43.0%提高到68.2%，全新缓存独立复测为65.7%；严格限定F1分别为57.6%、48.2%，原基线为39.7%。该历史变更见[关系提示词优化记录](relation-extraction-improvements-2026-11-05.md)。当前进一步优化后的完整结果以本页顶部的新报告为准。
