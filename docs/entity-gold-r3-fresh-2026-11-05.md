# 实体抽取当前评测记录（2026-11-05）

当前策略`f1d5b98525a8`，抽取与审核推理均为`none`。当前golden为20段、101条实体，中英文各10段。只保留当前同提示词两轮结果及必要溯源；过时提示词实验、失败试跑和重复日志已清理。

[当前标注阅读版](entity-gold-v3-review.md) · [评测与文件入口](entity-gold-evaluation.md)

## 同一提示词再次复测（none / none，当前101条标准）

本次提示词及配置保持不变：`video-graph-v2:deepseek-v4.1-flash-economy:f1d5b98525a8-none-none`，抽取、审核及关系阶段推理均为none，thinking=disabled；仍为同一20段原文，中英文各10段，模型与评分函数不变。候选和后续模型响应全部重新生成，使用新的缓存目录。

用户已将golden从108条修订为101条。因此本节将上一次同提示词的输出也按当前101条标准重新评分；历史108条口径不参与当前两轮比较。下面两行统一使用本次101条标准。

| 输出组（均按101条标准） | 成功片段 | TP | FP | FN | Precision | Recall | F1 |
|---|---:|---:|---:|---:|---:|---:|---:|
| 上次同提示词输出，重新计分 | 20/20 | 87 | 10 | 14 | 89.7% | 86.1% | 87.9% |
| 本次全新真实抽取 | 20/20 | 89 | 11 | 12 | 89.0% | 88.1% | 88.6% |

同一标准下，两次输出的实体及类型F1相差+0.68个百分点，FP变化+1条，FN变化-2条。提示词没有改动，此处主要观察两次模型输出的波动。

| 语言 | 上次输出重评 P / R / F1 | 本次输出 P / R / F1 |
|---|---|---|
| 中文 | 91.1% / 87.9% / 89.5% | 89.7% / 89.7% / 89.7% |
| 英文 | 87.8% / 83.7% / 85.7% | 88.1% / 86.0% / 87.1% |

本轮真实成功阶段响应93次、响应重放0次。成功输出的身份及关系审核凭据、全部缓存请求/响应指纹与原文均已验证；最终指标重新计算一致。阶段计数不等于HTTP尝试数。

数据口径说明：本轮进程启动时引用了108条旧快照，校验后发现当前为101条。已逐字段确认两者正文、标题、来源、语言及型号说明完全相同，标准答案不进入模型输入，因而继续完成真实抽取后按101条统一评分。未改动或补造任何抽取实体；启动时108条的原始计分JSON另存，换标准计分本身未额外调用模型。

[本次101条标准快照](../data/video_graph_runs/entity-gold-reviewed-repeat-20261105.json) · [本次完整评测](entity-gold-prompt-edit-repeat-2026-11-05.md) · [本次原始抽取及最终计分](../data/video_graph_runs/entity-gold-prompt-edit-repeat-20261105.json) · [启动时108条原始记录](../data/video_graph_runs/entity-gold-prompt-edit-repeat-original-108-20261105.json)

[上次输出按101条重评](../data/video_graph_runs/entity-gold-prompt-edit-first-current-gold-20261105.json) · [本次来源与请求审计](../data/video_graph_runs/entity-gold-prompt-edit-repeat-audit-20261105.json) · [剩余错误诊断](entity-gold-prompt-edit-repeat-errors-2026-11-05.md)

### 本次逐片段结果

| 片段 | 上次按101条重评 TP / FP / FN | 本次 TP / FP / FN | 本次未匹配标准实体 | 本次额外抽取实体 |
|---|---|---|---|---|
| EG001 | 6 / 0 / 1 | 5 / 0 / 2 | DJI Avata系列 [product_family]<br>DJI Avata 360 下方传感器 [component] | 无 |
| EG002 | 4 / 0 / 0 | 4 / 0 / 0 | 无 | 无 |
| EG003 | 2 / 0 / 0 | 2 / 0 / 0 | 无 | 无 |
| EG004 | 5 / 3 / 0 | 5 / 3 / 0 | 无 | 摺疊無人機專利 [technology]<br>摺疊機臂 [component]<br>環形螺旋槳保護罩 [component] |
| EG005 | 7 / 0 / 3 | 8 / 0 / 2 | DJI [organization]<br>Neo 3镜头 [component] | 无 |
| EG006 | 4 / 1 / 2 | 5 / 0 / 1 | CMOS [technology] | 无 |
| EG007 | 8 / 0 / 0 | 8 / 0 / 0 | 无 | 无 |
| EG008 | 11 / 0 / 0 | 11 / 0 / 0 | 无 | 无 |
| EG009 | 1 / 0 / 0 | 1 / 0 / 0 | 无 | 无 |
| EG010 | 3 / 1 / 1 | 3 / 3 / 1 | RC 2 [product] | RC 2 遥控器 [product]<br>DJI Mavic 4 Pro 的终极套装 [product]<br>DJI [organization] |
| EG011 | 1 / 1 / 1 | 1 / 2 / 1 | Trippy Spin [method] | trippy spin [technology]<br>DJI Neo 2 相机 [component] |
| EG012 | 4 / 0 / 1 | 4 / 0 / 1 | drone body  [component] | 无 |
| EG013 | 2 / 0 / 3 | 2 / 0 / 3 | remote controller [product]<br>propeller holder [component]<br>gimbal protector [component] | 无 |
| EG014 | 5 / 0 / 0 | 5 / 0 / 0 | 无 | 无 |
| EG015 | 2 / 2 / 0 | 2 / 3 / 0 | 无 | charging ⏎ cable USB &quot;C&quot; [component]<br>spare battery [component]<br>USB cable for the iPhone [component] |
| EG016 | 4 / 1 / 0 | 3 / 0 / 1 | striped propellers [component] | 无 |
| EG017 | 6 / 0 / 1 | 7 / 0 / 0 | 无 | 无 |
| EG018 | 5 / 1 / 1 | 6 / 0 / 0 | 无 | 无 |
| EG019 | 5 / 0 / 0 | 5 / 0 / 0 | 无 | 无 |
| EG020 | 2 / 0 / 0 | 2 / 0 / 0 | 无 | 无 |

这里是已参与人工取舍和调参的开发集结果，不是独立保留集结论。未写图谱数据库，推理默认配置保持none。
