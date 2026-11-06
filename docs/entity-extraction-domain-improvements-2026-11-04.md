# 实体抽取：口径、领域词表与错误分类

本文保留运行时仍适用的实体口径、领域词表和错误分析说明；旧实验分数与逐轮产物已清理。当前策略、101条标准及两轮结果见[实体评测入口](entity-gold-evaluation.md)。

## 1. 统一标注口径

[标注规则](../scripts/evaluation_data/entity_gold_guidelines_v3.md)按“提到什么 → 对应哪个对象 → 是否值得入图”定义判断顺序，并列出产品、部件、软件、技术、方法、故障及协议的边界。保留人工已经确认的固件=software、完整遥控器/飞行眼镜=product。

运行时通过同一份`ENTITY_SCOPE_RULES`约束初抽和实体审核，不新增模型阶段。对可整词确定的固件、完整遥控器和眼镜，程序按确认口径纠正类型；遥控器电路板、眼镜屏幕、固件芯片不因名称中包含这些词而被改类。

实体审核可返回已有的`surface_text`字段，收窄候选边界，例如去掉尺寸或普通修饰。修正必须来自原候选内部的连续原文，且不能缩成纯参数；失败时保留原边界并记录原因，不能移到正文中另一对象，也不能增加型号词。

## 2. 小型领域词表

[词表JSON](../backend/app/services/video_graph_terms.json)包含42条项目常见术语，每条只有`name/type/aliases`。包含型号、完整控制器/眼镜、软件及技术概念；它是拼写、类型和候选参考，不是产品规格库，不证明型号已发布或关系成立。

[运行时实现](../backend/app/services/video_graph_terms.py)只向模型提供当前正文匹配到的条目，最多24条；只从正文补充候选，仍须通过独立实体审核和来源校验。新词不受词表限制，不能因为不在表中就丢弃。型号匹配保留代际、Pro/Lite等变体，不从未知型号截取一个旧型号；短型号也不能仅凭词表补造品牌。

词表文件与实现都进入策略指纹，修改后旧抽取缓存不能直接当作新策略结果；JSON已加入Python打包数据配置，没有新增数据库字段、表、实体类型或模型依赖。

词表和口径参考了项目现有内容，包含开发样本出现的术语。当前golden是开发集，不能把这一轮分数当作未见术语或新视频的泛化成绩。

## 3. 独立错误分析

新增[分析脚本](../scripts/analyze_entity_gold.py)，按漏识别、类型、归属、边界/命名、价值筛选、待判读六类解释原有FP/FN。它不修改预测、标准答案或严格评分，不以名称相似给预测补分。

脚本可读取精确请求缓存，区分“候选没提出”和“提出后被审核拒绝”，并核对缓存请求、响应及正文。明确的类型冲突可自动识别；同一部件词涉及多个设备时保守处理，不能看到一个不匹配的所有者就判错。其余项目读完整原文和候选审核记录后由助手分类，未经用户逐条确认。

复用方式：

```bash
.venv/bin/python scripts/analyze_entity_gold.py \
  --evaluation data/video_graph_runs/entity-gold-prompt-edit-repeat-20261105.json \
  --response-cache data/video_graph_runs/entity-prompt-edit-repeat-responses-20261105 \
  --output data/video_graph_runs/entity-gold-prompt-edit-repeat-errors-20261105.json \
  --report docs/entity-gold-prompt-edit-repeat-errors-2026-11-05.md
```

可以修改分析JSON中每项的`category`及`note`再执行，生成统计和阅读版。原始实体、错误项范围、评测指纹及严格计数发生变化时会拒绝沿用旧分析，避免解释文件掩盖实际错误。失败/待运行样本仍保留全部FN，并与语义漏抽区分。
