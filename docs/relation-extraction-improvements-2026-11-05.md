# 关系抽取提示词与共享规则优化

本页为`c88ca83a8725`历史基线。后续当前版本`f39b9656b91a`的规范表达及漏抽定位见[本轮优化报告](relation-direction-and-recall-2026-11-05.md)。

2026-11-05按用户选择恢复本页 `c88ca83a8725` 版本。恢复后293项相关测试通过，20段完整精确缓存重放与原独立复测输出一致（0次模型调用）；见[重放报告](relation-gold-v3-restored-c88-2026-11-05.md)及[恢复核验记录](../data/video_graph_runs/restored-c88-verification-20261105.json)。golden集和历史评测保留不变。

本页当时工作区采用`c88ca83a8725`策略。在同一份20段、77条关系的人工金标准上，关系加断言F1从43.0%提高到68.2%；全新缓存独立复测为65.7%。严格限定匹配F1从39.7%提高到57.6%，独立复测为48.2%。四次运行均20/20成功，无错误、无待运行项；金标准、匹配函数、评分规则和模型推理档位均保持原样。

## 实测对照

模型均为`deepseek-v4.1-flash-economy`，抽取/审核推理档位均为`none`，并发2。主指标要求实体身份、类型、关系方向、谓词及assertion匹配。

| 运行 | 策略 | TP/FP/FN | Precision | Recall | 关系+断言F1 | 严格限定F1 | 新调用/缓存重放阶段 |
|---|---|---|---:|---:|---:|---:|---:|
| 原基线 | f1d5b98525a8 | 26/18/51 | 59.1% | 33.8% | 43.0% | 39.7% | 92/0 |
| 第一轮 | 8357c3fb28cf | 32/11/45 | 74.4% | 41.6% | 53.3% | 38.3% | 46/42 |
| 第二轮，当前方案 | c88ca83a8725 | 45/10/32 | 81.8% | 58.4% | 68.2% | 57.6% | 46/42 |
| 当前方案独立复测 | c88ca83a8725 | 45/15/32 | 75.0% | 58.4% | 65.7% | 48.2% | 92/0 |

第一、二轮精确复用了原基线的实体请求响应；20段最终实体输出均与基线逐项相同，实体及类型F1均为88.1%。因此受控对照中的提升来自关系层。42次重放包含实体提取、审核及其格式修正阶段，不是跳过了实体流程。

独立复测使用全新的缓存目录，92次阶段调用全部为真实调用，重放为0；完整流程实体及类型F1为88.6%。这轮关系主指标比受控对照低2.5个百分点，仍比原基线高22.7个百分点；限定指标波动更大，应继续结合逐项差异阅读。阶段调用次数不等于内部HTTP尝试次数。

按语言的关系加断言F1：

| 范围 | 原基线 | 当前受控对照 | 当前独立复测 |
|---|---:|---:|---:|
| 中文10段 | 45.2% | 74.7% | 67.5% |
| 英文10段 | 40.7% | 59.6% | 63.3% |

## 修改内容

1. **细化谓词选择**：在`video_graph_semantics.py`增加提示词专用的`PREDICATE_PROMPT_DEFINITIONS`和`RELATION_SELECTION_RULES`。明确组成、功能、配用、应用、品牌、系列、包装等关系的边界，以及“优先选择最具体谓词”。原展示标签保持原样，历史标注阅读稿不会因提示词修订而改变。
2. **分类与审核明确范围字段**：修改`PAIR_CLASSIFICATION_INSTRUCTIONS`、`RELATION_REVIEW_INSTRUCTIONS`，明确枚举顺序不是语义方向，条件故障使用asserted+conditional，推测使用uncertain+hypothetical，并输出逐字scope_quote。审核可修复初始谓词或补足连续引文，不把主体切换和比较对象混为一谈。
3. **仅纠正类型唯一的反向关系**：`orient_relation_by_types()`仅在原方向非法、反向合法时交换端点。例如公司→产品的offered_by、型号→系列的has_model；两向均合法或均非法时不猜方向。分类后及审核后均适用，仍必须经过独立原文审核、证据及身份校验，不补造新关系。
4. **保留或清理正确的范围**：审核省略modality/scope_quote时保留候选已有的逐字范围，并重新验证；显式给出的无效范围不能回退蒙混过关。审核明确改为actual时清掉候选残留的旧modality及不再适用的旧范围，保留其他限定。格式重试保留“未填写”状态，避免默认actual覆盖原条件。
5. **区分事实与教程语境**：在`grounding_instructions()`说明稳定的组成、品牌、配用及功能事实不因出现在教程中就统一变成instruction；方法应用、安装操作建议才使用步骤语境。未执行的假设与已执行的其他模式分别判断。

实际拼接后的关系分类提示词为1923字符，审核提示词为2102字符；原来分别为1060、1165字符。没有塞入金标准答案、具体case ID或型号样例；实体阶段提示词未变。提示词长度增加约八成，现有单次2800字符及四阶段合计8000字符的测试约束仍通过。

## 改善与剩余差异

受控对照中，品牌offered_by的TP从3提高到9，系列has_model从0提高到4，uses从0提高到3；EG007的7条关系全部匹配，EG014补回品牌归属，EG017保留了条件故障关系。主指标漏匹配从51条降到32条，未匹配预测从18条降到10条。

仍有以下明确限制：

- applied_to的3条答案在两次最终方案测试中仍未匹配；素材软件与设备的关系仍可能被分成connected_to或uses，手动教程动作仍可能被分成supports。
- packaged_with的3条仍未匹配。EG013的遥控器及保护件在实体审核已被过滤，仅调整关系阶段无法恢复不存在的端点；本轮没有改变实体筛选口径。
- 不同设备同名部件、旧型号简称、True vertical shooting的实体边界，以及Head Tracking实验的uncertain边界仍会造成差异。条件原文摘录和actual/capability/instruction的选择也使严格限定分数波动。
- 范围外预测按既定规则单列，不计TP/FP：原基线9条、第一轮12条、第二轮7条、独立复测3条。它们仍需另行人工审查；上述Precision不是全图所有关系的准确率。

这20段已用于误差分析与调优，是开发评测集；两轮提升证明了在当前固定样本上的效果，不能替代独立盲测，也不设置自动发布阈值。本轮只更新后续抽取代码，没有重建数据库中的历史图谱。

## 验证与复现

相关293项测试通过，覆盖关系分类/审核、原文证据、条件与假设、包装/制造/地理约束、身份归一、缓存、实体及关系评分和标注版本配套。新增14项条件保留、格式修正、旧范围清理、类型唯一方向修正及拒绝无来源制造事实的测试；没有降低已有断言或改动评分器。

```bash
.venv/bin/python -m pytest \
  backend/tests/test_video_graph_relation_scope.py \
  backend/tests/test_video_graph_quality.py \
  backend/tests/test_video_graph_grounding.py \
  backend/tests/test_video_graph_semantics.py \
  backend/tests/test_video_graph_normalization.py \
  backend/tests/test_video_graph_terms.py \
  backend/tests/test_video_graph_acceptance.py \
  backend/tests/test_video_graph_release.py \
  backend/tests/test_video_graph_response_cache.py \
  backend/tests/test_relation_gold_dataset.py \
  backend/tests/test_relation_gold_evaluation.py \
  backend/tests/test_entity_gold_dataset.py \
  backend/tests/test_entity_gold_evaluation.py -q
```

完整新抽取复测命令：

```bash
.venv/bin/python scripts/evaluate_relation_gold.py \
  --dataset data/video_graph_runs/relation-gold-v3-reviewed-20261105.json \
  --entities data/video_graph_runs/entity-gold-v3-relation-baseline-20261105.json \
  --output data/video_graph_runs/relation-gold-v3-prompt-r2-repeat-20261105.json \
  --report docs/relation-gold-v3-prompt-r2-repeat-2026-11-05.md \
  --response-cache data/video_graph_runs/relation-gold-v3-prompt-r2-repeat-responses-20261105 \
  --workers 2
```

相同命令会续用已完成结果；另一次独立实验应同时更换output、report和缓存目录。模型与推理档位保持上文配置。不要重跑成功但分数较差的case来挑选结果；本轮没有对成功case进行这种重试。

完整报告：[原基线](relation-gold-v3-baseline-2026-11-05.md)、[第一轮](relation-gold-v3-prompt-r1-2026-11-05.md)、[当前受控对照](relation-gold-v3-prompt-r2-2026-11-05.md)、[独立复测](relation-gold-v3-prompt-r2-repeat-2026-11-05.md)。

实验数据：[全部指标与调用统计](../data/video_graph_runs/relation-prompt-comparison-20261105.json)、[当前受控原始结果](../data/video_graph_runs/relation-gold-v3-prompt-r2-20261105.json)、[独立复测原始结果](../data/video_graph_runs/relation-gold-v3-prompt-r2-repeat-20261105.json)、[相对基线的完整补丁](../data/video_graph_runs/relation-prompt-r2-changes-20261105.patch)。实体指标另存为各轮`*-entity-metrics-20261105.json`，未覆盖关系输出中的原始记录。

为保留旧策略的审核凭据与评分环境，保存了[原策略源码](../data/video_graph_runs/relation-prompt-before-source-20261105.tar.gz)、[第一轮源码](../data/video_graph_runs/relation-prompt-r1-source-20261105.tar.gz)、[当前源码](../data/video_graph_runs/relation-prompt-r2-source-20261105.tar.gz)。归档包含全部抽取策略指纹涉及的文件及评分/缓存脚本；已重新计算并核对三份策略指纹分别为f1d5b98525a8、8357c3fb28cf、c88ca83a8725。旧策略结果应在对应源码下验证，不能用当前策略跳过过期凭据校验。
