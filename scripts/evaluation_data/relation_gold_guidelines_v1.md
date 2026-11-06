# 无人机关系金标准集标注说明

本集在已人工复核的实体集 `entity_gold_v1.json` 上增加关系：同样20个视频片段，中文、英文各10段。原文、来源、实体名称和类型保持一致。用户已确认关系集人工复核完成，`status=human_reviewed`；此状态来自关系集的独立复核。校验固定使用v1实体集，不随实体脚本的默认版本变化。

## 文件与字段

编辑 `scripts/evaluation_data/relation_gold_v1.json`，按原有 EG001–EG020 对照。每段保留来源、逐行原文、实体和说明，新增 `relations` 数组；没有足够依据时允许 `[]`。

| 关系字段 | 含义 |
|---|---|
| subject | 主体，必须与本片段某个实体的name完全一致 |
| predicate | 当前项目已有的关系类型，见下表 |
| object | 客体，同样引用本片段实体name |
| assertion | asserted=原文明确陈述；uncertain=推测、传闻或尚待验证；negated=原文明确否定 |
| quote | 支持关系的连续原文；跨行用字符串数组，换行连接后必须逐字存在于text中 |
| qualifiers | 可省略；仅在必要时记录modality、scope、mode |

`qualifiers.modality`沿用项目口径：actual=事实描述，capability=能力，instruction=操作步骤，conditional=条件，hypothetical=假设/推测，design_goal=设计目标，planned=计划。条件、假设、目标、计划需用`scope`保留原文范围；`mode`记录原文中的模式名称。`scope`和`mode`必须逐字出现在该关系证据中。

asserted表示来源这样说，不表示已核实其现实真伪。条件句可标asserted并保留conditional范围，但不能当作设备当前已发生的事实；“未证实”不等于negated。

## 本集使用的关系口径

| 关系 | 方向与判断依据 |
|---|---|
| contains | 设备 → 自身硬件部件；保护、配套使用、包装随附不自动等于组成 |
| uses | 设备/部件 → 使用的软件、技术等；不因用了软件就推断安装位置 |
| supports | 设备 → 明确支持的功能、模式或技术 |
| compatible_with | 主设备 → 明确可搭配的另一设备/配件；语义对称，只标一条，不再反向重复 |
| controls | 控制器/技术/部件 → 被控制的设备或部件；仅说搭配不够 |
| packaged_with | 产品 → 明确随包装/产品附带的物品；个人使用清单不算原厂包装清单 |
| applied_to | 方法/软件 → 明确应用的设备；与设备相关的工作流不等于安装于该设备 |
| offered_by | 产品/系列 → 原文明确的品牌/所属公司，不升级为制造或研发关系 |
| has_model | 产品系列 → 该系列具体型号；比较、替代、整合旧机种不自动构成系列归属 |
| reports_fault | 设备 → 出现的故障；假设故障需保留条件，不表示所有同型号设备都有故障 |

其他关系可使用项目现有谓词，但必须有原文依据及合法端点类型；`equipped_with`仅供规则派生，不作为本集抽取答案。不要新增“同规格”“对比”等自造谓词。

## 人工复核

1. 只读当前片段原文及已确认实体，检查每条关系是否有依据，主体/客体及方向是否正确。
2. 检查同一片段是否漏掉其他明确关系；不要求每个实体都必须有边，不为增加数量强行连边。
3. 对同一事实保留最直接的谓词，不把contains再扩成uses，不为controls重复添加compatible_with。不同事实可以分别标注。
4. 核对代词和跨行指代，防止把比较对象的部件、模式、故障接到另一型号上。同规格部件不等于同一个物理部件。
5. 保留推测、条件、模式范围。EG005的曝光配置、EG017的If故障条件、EG019的实验目的与结果边界已单独复核，后续修改仍需保持这些范围，说明写在各段notes中。
6. 关系端点仅限本段已复核实体；不从models、标题、常识或其他片段补实体/关系。如需扩大实体范围，先单独修订实体集再同步关系集。
7. 修改JSON后运行校验并重新生成复核稿。后续如新增或更改尚未人工确认的关系，应先将status改回assistant_draft；全部关系人工确认后再标为human_reviewed。

人工复核后共70条关系、10类谓词：63条asserted、7条uncertain，EG009无关系。EG008删除6条对比机型的镜头组成关系及1条Flip支持HDR的关系；EG019删除Head Tracking控制相机和无人机的2条关系，保留模式限定下的不确定支持关系。实体答案保持不变。

本集没有明确否定关系。缺少否定样本是覆盖范围限制，不能补造。未来需要否定能力评估时另选有明确否定且端点齐全的片段。

本集评测范围是已确认实体之间的原文关系。未标注的关系不是“现实中明确不存在”，也不能作为negated答案。关系评分入口为`scripts/evaluate_relation_gold.py`，规则与命令见`docs/relation-gold-evaluation.md`；当前测试完整抽取流程的关系结果，固定正确实体后单测关系分类属于后续独立实验。标注校验本身不产生模型Precision/Recall/F1。

## 本地命令

无需模型API或数据库。当前默认已切到v3，复现此处的历史v1需明确指定两份旧文件：

```bash
.venv/bin/python scripts/relation_gold_dataset.py validate \
  --dataset scripts/evaluation_data/relation_gold_v1.json \
  --entities scripts/evaluation_data/entity_gold_v1.json
.venv/bin/python scripts/relation_gold_dataset.py render \
  --dataset scripts/evaluation_data/relation_gold_v1.json \
  --entities scripts/evaluation_data/entity_gold_v1.json \
  --output docs/relation-gold-v1-review.md
```

JSON是修改入口，Markdown是生成的阅读稿；不要只改Markdown。校验检查来源一致性、端点、关系类型和证据连续性，不能代替人工判断关系语义。
