# 关系谓词、方向规范及漏抽环节优化

当前版本：`f39b9656b91a`，基于用户恢复的`c88ca83a8725`修改关系层。模型仍为`deepseek-v4.1-flash-economy`，抽取/审核推理均none。金标准及严格评分器未修改。

最终20段受控对照的关系加断言F1由65.7%升到75.7%；独立前序输出组为72.5%。受控组的20份实体输出逐项不变，实体及类型F1仍88.6%；独立组为86.4%。旧版的另一组关系F1为68.2%，本报告以与受控组相同前序响应的65.7%作为直接基线。

## 漏抽在哪一步发生

基线32条未命中关系中，13条端点未通过实体身份/类型对齐，2条谓词不同，1条断言不同；另外16条是端点齐全但最终没有对应关系。

对基线全流程进行92次精确缓存重放，20段原始output逐项一致，记录分类前实体、候选实体对、实际分类裁决、分类候选、独立审核裁决及程序拒绝原因。golden只在抽取完成后用于定位，未传给模型。

| 这16条关系的丢失环节 | 数量 | 证据 |
|---|---:|---|
| 候选实体对未生成/预算截断 | 0 | 所有16个目标实体对均已送给模型 |
| 分类模型判无关系 | 13 | 明确返回supported=false，包括品牌、系列、组成和接线关系 |
| 独立关系审核拒绝 | 1 | 已提出Neo 3所属DJI关系，审核又以未表达品牌归属拒绝 |
| 所有者归一校验拒绝 | 1 | 一个泛称桨叶被同时绑定两个独立机型，无法区分实例 |
| 原文引文校验拒绝 | 1 | ESC控制电机的引文漏了原文中的the，独立审核未修正 |

基线实际裁决及请求证据见[逐阶段诊断JSON](../data/video_graph_runs/relation-c88-stage-diagnosis-20261105.json)。这区别于仅从最终FN猜测原因；没有将全部漏关系笼统归因于模型召回。

## 规范与实现

| 场景 | 统一表达 | 边界 |
|---|---|---|
| 外部App、编辑/导入软件、操作方法应用于设备 | 软件/方法 → applied_to → 设备 | 须有原文工作流依据；不从仅共现推导 |
| 设备内置固件、运行软件、所用技术/数据 | 设备 → uses → 资源 | 不把所有uses都反转为applied_to |
| 整体与自身部件 | 整体 → contains → 部件 | 物主词、拆换对象、连续指代可说明归属；跨设备比较不能强绑 |
| 部件安装 | 部件 → installed_on → 载体 | 安装不自动等价于物理包含 |
| 产品品牌归属 | 产品/系列 → offered_by → 组织 | 正文品牌+型号可作依据；规范名自行补出的品牌不作新事实 |
| 产品系列与型号 | 系列 → has_model → 型号 | 比较或替代不自动产生型号归属 |
| 兼容、连接 | compatible_with / connected_to | 沿用语义对称口径；不将连接软件误写为USB硬件连接 |

1. 在共享语义规则中区分外部工作流与内置运行，分类和独立审核使用同一口径。新增`canonicalize_relation_expression`，仅对有逐字原文及外部工作流证据的软件/方法关系规范谓词和方向；分类后、审核后均执行。保留引文、assertion、qualifiers和端点身份。已有类型唯一的方向纠正规则继续使用。

2. 每个候选对按实体类型增加简短question，明确要核对的角色：品牌归属、系列代际、整机部件、部件控制/接线、实际配用等。question是问题而非事实；模型返回false时不创建关系。实体初抽、价值筛选、归一策略和96对预算不变，没有重新引入实体补漏或放宽实体池。

3. 审核输入显式提供quote_valid。无效引文的批次附原文source_lines，允许返回连续行号quote_lines；程序复制原文，检查行号、顺序、6000字符上限及原有来源/语义约束。没有使用同义词匹配、删词或模糊引文补救。

4. 支持项没有按要求修复无效引文时，进入原有最多一次格式修正，不将该不完整肯定裁决冻结；其他合法肯定/否定裁决仍冻结。修正后仍缺失则隔离，不循环重试，也不绕过原文校验。

实现：[共享语义与规范化](../backend/app/services/video_graph_semantics.py)、[分类/审核与证据修复](../backend/app/services/video_graph_quality.py)、[新增29项契约测试](../backend/tests/test_video_graph_relation_expression.py)。

## 完整golden集对照

20段（中英文各10段）、101条实体、77条关系。所有组均20/20成功，没有语义不理想案例的选择性重跑。关系主指标包含端点身份/类型、方向、谓词和断言；限定指标继续严格比较qualifiers。

| 指标 | c88直接基线 | 当前受控A组 | 当前独立前序B组 |
|---|---:|---:|---:|
| 关系TP / FP / FN | 45 / 15 / 32 | 56 / 15 / 21 | 50 / 11 / 27 |
| 关系Precision | 75.0% | 78.9% | 82.0% |
| 关系Recall | 58.4% | 72.7% | 64.9% |
| 关系F1 | 65.7% | 75.7% | 72.5% |
| 三元组F1 | 67.2% | 78.4% | 75.4% |
| 严格限定F1 | 48.2% | 60.8% | 59.4% |
| 实体及类型TP / FP / FN | 89 / 11 / 12 | 89 / 11 / 12 | 86 / 12 / 15 |
| 实体及类型F1 | 88.6% | 88.6% | 86.4% |
| 预测关系总数 / 范围内 / 范围外 | 63 / 60 / 3 | 73 / 71 / 2 | 63 / 61 / 2 |

受控组从旧版的45条正确关系中保留42条，补回14条原未命中关系，净增加11条；仍有3条原正确关系丢失，分别为Neo 3相机模组组成、Flip镜头组成、Air 3螺丝与底盖连接。没有用净收益掩盖退化。独立组保留旧版39条、补回11条，实体识别变化也会影响该结果。

中间结果保留：首版`54abba219fab`的关系F1为73.5%；增加行号证据的`0570c5eaf325`为74.8%，全新缓存独立复测为72.5%。最终`f39b9656b91a`再补齐无效引文的有界格式修正。未删掉低分实验。

## 原16条目标的去向

以下逐项比较同一golden关系是否命中。B组的初始实体与A组不同，未命中不能一概归因于原先同一个阶段。

| 片段 | Golden关系 | 基线丢失环节 | A组 | B组 |
|---|---|---|---|
| EG004 | DJI Neo 2 → offered_by → DJI | 分类模型判无关系 | 命中 | 命中 |
| EG004 | DJI Flip → offered_by → DJI | 分类模型判无关系 | 命中 | 命中 |
| EG005 | DJI Neo 3 → offered_by → DJI | 独立审核拒绝 | 命中 | 未命中 |
| EG005 | DJI Neo 2 → offered_by → DJI | 分类模型判无关系 | 命中 | 未命中 |
| EG005 | DJI Neo系列 → offered_by → DJI | 分类模型判无关系 | 未命中 | 未命中 |
| EG005 | DJI Neo系列 → has_model → DJI Neo 3 | 分类模型判无关系 | 命中 | 未命中 |
| EG005 | DJI Neo 3 → contains → Neo 3感光元件 | 分类模型判无关系 | 未命中 | 未命中 |
| EG005 | DJI Neo系列 → has_model → DJI Neo | 分类模型判无关系 | 命中 | 命中 |
| EG008 | DJI Flip → offered_by → 大疆 | 分类模型判无关系 | 命中 | 命中 |
| EG015 | Parrot ANAFI → compatible_with → ANAFI遥控器 | 分类模型判无关系 | 命中 | 命中 |
| EG016 | DJI Mini 3 → contains → propellers | 所有者归一校验拒绝 | 未命中 | 未命中 |
| EG017 | DJI Air 3 → contains → Air 3电调板 | 分类模型判无关系 | 命中 | 命中 |
| EG017 | DJI Air 3 → contains → Air 3 motors | 分类模型判无关系 | 未命中 | 命中 |
| EG017 | Air 3电调板 → controls → Air 3 motors | 引文非逐字原文 | 命中 | 命中 |
| EG017 | DJI Air 3 → offered_by → DJI | 分类模型判无关系 | 命中 | 未命中 |
| EG017 | DJI Air 3 arm wires → connected_to → Air 3电调板 | 分类模型判无关系 | 命中 | 命中 |
| 合计 | 16条 | — | 补回12条 | 补回9条 |

另两条谓词不一致的目标：EG003统一为DJI Studio→applied_to→Avata 360，EG013统一为DJI Fly→applied_to→Mini 4 Pro，两组均命中。EG003的受控组还通过一次行号格式修正保住了原本因抄写错误被拒绝的关系。

## 缓存、成本与验证

最终A组重放97次阶段响应，新增1次缺失引文修复调用；前序关系分类/审核来自本轮首版和行号版的真实调用，实体响应精确复用c88基线。最终B组完整重放94次；前序来自行号版同一请求协议的全新缓存独立运行（94次真实调用、0重放），最终版新增的缺失引文检查在该组未触发额外请求。B组不是最终版本全部阶段再次全新调用；两组也没有混用逐case最好输出。

逻辑阶段请求：旧版92次，当前A组98次、B组94次，平均每段4.90/4.70次（旧版4.60次）；范围2–9与2–8次。角色问题会增加输入文本，不能用阶段次数直接推算token费用。本轮没有新增常规LLM阶段；每次结构/引文格式修正仍最多一次，原18次常规阶段/72次HTTP尝试保守上限保持。

相关322项测试通过，含新增29项：规范化幂等、断言和条件不变、内置固件/普通软件/GPS/硬件不误反转、缺原文不规范化、六类角色问题不自动建边、规范化候选仍须独立审核、无效引文必须修复、原文行号边界及长度、一次修正、合法否定冻结及失败隔离。模型质量不能仅凭这些单元测试认定。

另对最终A组20段、98次阶段请求完整离线重放，0次模型调用，原始output及指标逐项一致。六个golden/评分器保护文件与c88源码快照逐字相同；当前源码及测试与最终清单一致。

限制：13条基线关系首先受实体漏抽或类型/身份影响，本轮未修改实体层。共享泛称部件的所有者歧义仍拒绝，未为召回而强行绑定。包装随附3条关系仍全漏；Head Tracking的uncertain及条件摘录仍有差异。两组各2条范围外关系待人工复核，不计关系TP/FP，因此上述Precision不是全图准确率。20段已用于开发，无否定关系标准案例，不能据此宣布盲测或生产质量通过。本轮没有重建数据库图谱。

## 文件与复现

- [基线逐项表](entity-relation-gold-c88-comparison-2026-11-05.md)、[基线恢复输出](../data/video_graph_runs/relation-gold-v3-restored-c88-20261105.json)、[逐阶段诊断](../data/video_graph_runs/relation-c88-stage-diagnosis-20261105.json)。
- A组：[关系报告](relation-direction-r3-2026-11-05.md)、[原始输出](../data/video_graph_runs/relation-direction-r3-20261105.json)、[同批实体指标](../data/video_graph_runs/relation-direction-r3-entity-metrics-20261105.json)。
- B组：[关系报告](relation-direction-r3-repeat-2026-11-05.md)、[原始输出](../data/video_graph_runs/relation-direction-r3-repeat-20261105.json)、[同批实体指标](../data/video_graph_runs/relation-direction-r3-repeat-entity-metrics-20261105.json)。
- [最终源码快照](../data/video_graph_runs/relation-direction-r3-source-20261105.tar.gz)、[源码指纹清单](../data/video_graph_runs/relation-direction-r3-manifest-20261105.json)、[完成核验与16条去向](../data/video_graph_runs/relation-direction-final-audit-20261105.json)。

完整离线重放A组，output使用未占用路径：

```bash
.venv/bin/python scripts/evaluate_relation_gold.py \
  --dataset data/video_graph_runs/relation-gold-v3-reviewed-20261105.json \
  --entities data/video_graph_runs/entity-gold-v3-relation-baseline-20261105.json \
  --output /tmp/relation-direction-replay-new.json \
  --response-cache data/video_graph_runs/relation-gold-v3-prompt-r2-repeat-responses-20261105 \
  --cache-only --workers 2
```

全新模型调用：去掉cache-only，并同时更换output与response-cache目录。B组离线重放用relation-direction-r2-repeat-responses-20261105目录。复现历史c88需使用相应源码，不能跳过过期凭据检查。
