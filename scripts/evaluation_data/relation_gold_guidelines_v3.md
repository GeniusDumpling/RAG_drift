# 关系金标准集 v3 标注说明

编辑入口：`scripts/evaluation_data/relation_gold_v3.json`。阅读稿：`docs/relation-gold-v3-review.md`。

本集基于当前`entity_gold_v3.json`重建，保留相同20个片段，中文、英文各10段、101条实体。原文、来源、models、实体名称、类型、mentions均逐字沿用实体集；notes在关系集副本中整理，不修改实体源文件。原实体集部分notes尚有旧取舍描述，重建以entities字段为准。

本轮人工复核已完成，状态为`human_reviewed`。关系集共77条关系：70条asserted、7条uncertain，13类谓词；EG009仍无关系。旧`relation_gold_v1.json`及其70条人工复核答案保留。

## 重建范围

旧关系中60条原样保留；1条将原传感器端点改为v3的`DJI Avata 360 下方传感器`；9条因端点删除而移除；新增16条。新增标注只依据原始字幕，不读取模型抽取结果或数据库边来制定答案。

| 片段 | 旧→新关系数 | 重建说明 |
|---|---|---|
| EG001 | 5→5 | 删除桨叶组成边；迁移下方传感器端点；新增桨保缓解炸机风险的能力关系 |
| EG002 | 4→2 | 删除摇杆、无型号遥控器的2条操控关系 |
| EG005 | 10→12 | 新增第一代DJI Neo的品牌及系列归属关系 |
| EG006 | 8→5 | 删除感光元件、镜头、遥控器相关4条边；新增整机uses CMOS |
| EG008 | 4→4 | 沿用人工复核后的4条边，不恢复此前删除的镜头组成或Flip支持HDR关系 |
| EG009 | 0→0 | v3仅保留Mini 4K，不补回地理端点 |
| EG010 | 1→2 | 新增Neo 2与无型号护目镜的兼容关系，不补具体眼镜型号 |
| EG012 | 2→5 | 新增设备与底盖、机身组成关系及螺丝与底盖的固定连接关系 |
| EG015 | 2→1 | 手机已删除，移除相应兼容关系 |
| EG016 | 1→4 | 新增条纹机臂、条纹螺旋桨的组成与安装配对关系 |
| EG017 | 6→8 | 新增机臂导线的组成及与电调板的连接关系 |
| EG018 | 1→4 | 新增Pro版本的运动模式支持、两台设备的竖拍支持关系 |
| EG019 | 5→4 | 删除相机组成关系，不恢复此前人工删除的Head Tracking controls关系 |

其余片段关系原样沿用。保留实体并不强制产生边，不因规范名相似、同句出现、参数相同或比较对象相似而连边。

## 字段与判断口径

每条关系仅包含`subject`、`predicate`、`object`、`assertion`、`quote`，必要时加`qualifiers`。subject/object必须与本片段entities中的name完全相同；不自动翻译、改名或修剪名称。EG012的`drone body `尾空格沿用当前实体文件，未在关系集单独清理。

quote是支持该关系的连续原文，跨行用字符串数组，换行后必须能在text中逐字找到，不拼接不相邻句子。

- asserted：来源明确陈述，不表示已外部核实。
- uncertain：推测、传闻或待验证；无法确认不等于否定。
- negated：原文明确否定。本集目前无此类答案，不为覆盖率补造。
- qualifiers：仅在需要时记录modality、scope、mode。沿用现有项目取值；conditional/hypothetical/design_goal/planned需要原文scope，scope/mode必须出现在该关系quote中。

组成用contains；设备功能/模式用supports；软件或技术使用用uses；明确配用用compatible_with；明确操控用controls；包装附带用packaged_with；方法/软件应用对象用applied_to；品牌归属用offered_by；系列型号用has_model；设备异常用reports_fault。新出现的3类谓词：

| 谓词 | 本集含义 | 复核边界 |
|---|---|---|
| mitigates | 桨保 → 炸机风险，按保护能力描述 | 不等于飞机已炸机，也不构造reports_fault的否定 |
| connected_to | 螺丝 → 底盖的机械固定、机臂导线 → 电调板的电气连接 | 已人工确认本段维修步骤支持所标端点之间的连接，不扩展到其他部件 |
| installed_on | 条纹螺旋桨 → 条纹机臂 | 保留instruction语境，区别于设备已完成安装的当前状态 |

同一事实选择最直接的谓词，不自动扩成多种近义边。对比、同规格、替代不等于兼容或组成；类/子集关系不错误标成硬件contains。`equipped_with`只供规则派生，不作为抽取答案。

## 本轮人工确认的标注边界

1. EG001：保留桨保对炸机风险的缓解能力，不能读成发生故障或保证永不炸机。
2. EG012、EG017：保留固定螺丝、解焊导线的维修步骤支持的具体连接关系，不从一般维修常识补连接。
3. EG016：条纹机臂/螺旋桨配对的安装方向，保持与普通propellers实体的区分。
4. EG018：开头both/后文They both按本段Pro版本与Mini 3处理，不将Pro版本补成特定型号；旧机型否定的是向上仰拍，不是否定竖拍或运动模式。
5. 保留EG005的传闻范围、EG017的If故障条件、EG019的Head Tracking实验边界。此前人审删去的关系不因实体集换版自动恢复。
6. 后续如修改尚未人工确认的答案，应先将status改回assistant_draft；重新复核完成后再标为human_reviewed。

## 校验与评测

默认标注校验和关系评测已配套切换为v3：

```bash
.venv/bin/python scripts/relation_gold_dataset.py validate
.venv/bin/python scripts/relation_gold_dataset.py render --output docs/relation-gold-v3-review.md
.venv/bin/python scripts/evaluate_relation_gold.py --output /tmp/relation-gold-v3-check.json --dry-run
```

校验不调用模型，仅检查结构、版本配套、端点、类型、证据及来源一致性，不能代替语义复核。dry-run允许检查草稿；实际关系评测仅接受human_reviewed。评分口径见`docs/relation-gold-evaluation.md`，旧v1与新v3分母不同，不直接比较历史F1。
