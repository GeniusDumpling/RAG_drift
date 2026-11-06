# 实体抽取评测入口

旧版基线的实体与关系逐项结果见[20段对照表](entity-relation-gold-c88-comparison-2026-11-05.md)；当前关系层优化及同批实体指标见[本轮报告](relation-direction-and-recall-2026-11-05.md)。

当前版本`f39b9656b91a`在用户恢复的旧版`c88ca83a8725`上仅优化关系层，实体阶段提示词保持不变；受控组20份实体输出逐项相同。默认数据为[entity_gold_v3.json](../scripts/evaluation_data/entity_gold_v3.json)：20段、中英文各10段，101条实体。模型`deepseek-v4.1-flash-economy`，温度0，抽取与审核推理均为`none`。四环节候选及同批对照保留为[历史实验](video-graph-entity-relation-flow-2026-11-05.md)。本页下方两组历史实体成绩对应策略`f1d5b98525a8`。数据集版本名仍为entity-gold-v3，具体内容以评测结果中的数据指纹区分。

## 保留的文件

| 用途 | 入口 |
|---|---|
| 当前人工答案 | [entity_gold_v3.json](../scripts/evaluation_data/entity_gold_v3.json) |
| 标注规则与阅读版 | [当前规则](../scripts/evaluation_data/entity_gold_guidelines_v3.md)、[逐片段阅读版](entity-gold-v3-review.md) |
| 数据校验与文档生成 | [entity_gold_dataset.py](../scripts/entity_gold_dataset.py) |
| 真实抽取与严格计分 | [evaluate_entity_gold.py](../scripts/evaluate_entity_gold.py) |
| FP/FN原因分析 | [analyze_entity_gold.py](../scripts/analyze_entity_gold.py) |
| 当前两轮结果 | [汇总与逐片段对照](entity-gold-r3-fresh-2026-11-05.md)、[最近一轮完整评测](entity-gold-prompt-edit-repeat-2026-11-05.md)、[错误诊断](entity-gold-prompt-edit-repeat-errors-2026-11-05.md) |
| 运行时口径、词表与归一 | [口径与词表](entity-extraction-domain-improvements-2026-11-04.md)、[归一实现](entity-normalization-2026-11-04.md) |
| 人工历史取舍 | [24项确认记录](entity-value-selection-review.md) |

`entity_gold_v1.json`及对应规则/阅读版仍保留：它与旧版关系金标准v1的106条实体配套，可供历史复现。当前关系默认数据见[关系评测说明](relation-gold-evaluation.md)。实体运行代码、测试和共享缓存工具继续保留。

## 评分口径

只把标题、当前正文及从正文匹配到的词表候选传入当前抽取流程，标准答案、models、notes均不传给模型，不写生产图谱。实体候选经过独立审核、价值筛选和确定性归一，随后运行关系分类与独立审核；本评测只对最终实体计分，不证明关系质量通过。当前旧版流程适用脚本dry-run中18次阶段/72次HTTP尝试的保守上限，实际调用量见cache_stats。

按片段内实体身份去重，一对一匹配规范名称或人工列出的原文叫法；仅做大小写、空白和排版字符归一，不自由翻译、不模糊补分、不由LLM评分。明确冲突的设备所有者不能匹配。分别报告实体识别，以及还要求类型正确的P/R/F1；歧义及全部未匹配项保留供复核。匹配不能证明泛称补全后的具体型号已经正确。

TP为匹配项、FP为未匹配预测、FN为未匹配标准；执行失败片段的全部标准实体仍计入FN。属性与关系不在本轮评分范围内。这20段已用于人工取舍和调参，属于开发集。

## 常用命令

```bash
# 校验当前人工答案
.venv/bin/python scripts/entity_gold_dataset.py validate

# 根据JSON更新当前阅读版
.venv/bin/python scripts/entity_gold_dataset.py render \
  --output docs/entity-gold-v3-review.md

# 新一轮真实抽取：使用新的输出文件和新的缓存目录
VIDEO_GRAPH_EXTRACTION_EFFORT=none VIDEO_GRAPH_REASONING_EFFORT=none \
.venv/bin/python scripts/evaluate_entity_gold.py \
  --output data/video_graph_runs/entity-eval-new.json \
  --response-cache data/video_graph_runs/entity-eval-new-responses \
  --report docs/entity-eval-new.md --workers 2

# 从保留的最近一轮缓存完整离线重放；不调用模型，输出路径须未使用
.venv/bin/python scripts/evaluate_entity_gold.py \
  --output /tmp/entity-eval-replay.json \
  --response-cache data/video_graph_runs/entity-prompt-edit-repeat-responses-20261105 \
  --cache-only --workers 2
```

已有输出文件会续跑未完成项；仅对真实执行失败使用`--retry-errors`，不能为成功但效果较差的片段重跑挑分。`--cache-only`只接受完全相同的请求，当前代码、模型参数或服务配置变化后可能无法重放。

## 历史实体结果与保留范围

同一101条标准、同一提示词的两组实体及类型结果：

| 输出组 | TP / FP / FN | Precision | Recall | F1 |
|---|---|---:|---:|---:|
| 首次输出按当前标准重评 | 87 / 10 / 14 | 89.7% | 86.1% | 87.9% |
| 再次全新抽取 | 89 / 11 / 12 | 89.0% | 88.1% | 88.6% |

保留两组原始响应缓存、原始输出、按101条重评结果、最近一轮审计及错误分析。必要的108条源快照和启动时计分JSON用于验证重评前后输出不变，也继续保留。已清理过时提示词实验、旧逐轮报告、失败/中断试跑、独用旧缓存及重复日志；历史分数不继续充当当前效果说明。
