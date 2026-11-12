# 官方手册多模态图谱试点验证

已完成两本固定手册的原文/原图准备、实体与事实抽取、图文审核、Neo4j 构建、检查点恢复和查询回读。实现与命令见[流程说明](document-knowledge-graph.md)，固定来源见[选材记录](document-graph-pilot-selection-2026-11-10.md)。

| 来源 | 固定版本 | 试点物理页 | 当前有效图谱 |
|---|---|---|---|
| DJI Air 2S 中文用户手册 | 1.0，2021.06 | 9、26、28、50–53，共7页 | 33个实体、103条事实 |
| Parrot ANAFI Ai 英文指南 | 7.7.1.7，2025-04-02 | 14、15、17–19、23，共6页 | 25个实体、76条事实 |

实体数按各手册投影分别统计。事实包含关系、参数、操作和灯态；共179条均绑定来源、原文或图片引用，不能将其全部视为已经人工确认的事实。

## 定向 golden 结果

| 检查 | 结果 |
|---|---:|
| 完整抽取窗口 | 13/13，错误0 |
| 目标事实及证据检查 | 19/19 |
| 要求图片定位的目标 | 13/13 |
| 纯文字参数目标 | 6/6 |
| 匹配预测的图片关联精确率 | 13/13 |
| 错图、错灯态负例拒绝 | 4/4 |

[对照表与原图](../data/document_graph_runs/pilot-20261110/release-comparison.html) 默认展示名称、事实、原文和图片，坐标及完整字段可展开；[机器报告](../data/document_graph_runs/pilot-20261110/release-comparison.json) 保留全部指标。原页和裁剪见[来源预览](../data/document_graph_runs/pilot-20261110/source-preview.html)。

[`document_multimodal_gold_v1.json`](../scripts/evaluation_data/document_multimodal_gold_v1.json) 的答案在调试期间未改动；2026-11-10初标快照的 SHA-256 为 `9732f2eb10c1e93b1f265eaaa86046ed6bec0e4a8d11710eeecd968c04444aab`。该集由助手对原始手册初标，初次评测时尚待人工复核。它参与了开发调试，不能据此推断未见文档的效果，也不用于报告全量实体/关系精确率或 F1。

## 2026-11-11 人工复核确认

用户已确认复核完成，v1 标记为 `human_reviewed`。19个目标与4个负例的标注内容与初标一致，因此保留 v1，仅更新复核状态和日期。全文 SHA-256 随元数据更新为 `1887c82ca9b6798f62e53134d50624eb6fb02c027e134379c353da0f8a31bacc`；[复核记录](../data/document_graph_runs/reviewed-20261111/review-receipt.json) 保留前后哈希及标注内容校验。

使用既有抽取结果重新评分，未调用模型：19/19目标、13/13图文关联目标、4/4错配负例通过。见[复核后对照表](../data/document_graph_runs/reviewed-20261111/comparison.html)和[机器报告](../data/document_graph_runs/reviewed-20261111/comparison.json)。初次构建的报告与审计保留为历史记录。

## 实际 Neo4j 验证

使用专用隔离 Neo4j，未改动业务数据库。实际构建数据来自配置模型的真实响应缓存，合成测试响应仅用于单元测试。

- 先保存一个真实窗口的检查点并注入失败，再恢复构建；同一任务两次领取，已完成窗口未重新抽取。
- 每本手册全部窗口完成后原子发布，179条事实及其端点、提及、原文和图片证据边回读一致。
- 删除一条真实图片证据边后，回读检测出不一致，被篡改来源的查询拒绝返回；恢复后验证通过。
- 新构建不完整时拒绝发布，旧有效图谱仍可查询；重复运行识别为当前构建。
- 撤回后事实不可见、图片接口返回404；恢复后重新可用。
- 两本手册的实际 API 图片响应与原 PDF 渲染出的来源包 PNG 的 SHA-256 一致，8类内容查询均能回到相应来源。

见[生命周期记录](../data/document_graph_runs/pilot-20261110/neo4j-release-lifecycle-audit.json)、[当前投影与 API 验证](../data/document_graph_runs/pilot-20261110/neo4j-release-current-verification.json)及[图谱快照](../data/document_graph_runs/pilot-20261110/neo4j-release-current-projection.json)。最终构建与复验严格使用 `--cache-only`，没有新增模型 HTTP 调用；首次恢复构建读取45次精确响应缓存，另复用一个已完成窗口。

## 实现与回归

文档视觉模型为 `Qwen/Qwen3-VL-32B-Instruct`，文本整理与审核使用现有 `deepseek-v4.1-flash-economy`。原生文字优先于模型 OCR 描述；部件以编号清单和实际图号绑定，四灯表使用原始矢量/图标像素测量，章节边界阻止同页 microSD 与 nano SIM 操作图错绑。

修复了裁剪渲染将单参数 Matrix 当成缩放的错误。重新生成的28个裁剪均已按原 PDF 坐标和双轴缩放检查。几何聚类复用轻量模块，文档图谱准备不再依赖旧解析器的语言检测；并发缓存写入使用唯一临时文件和原子替换。

相关回归共 **557项通过**，包括文档图谱、视频图谱、实体/关系评测和既有 DJI/Parrot 解析测试。视频 golden 20例的结果及抽取版本 `dcdff7721e27` 保持不变。测试日志见[完整记录](../data/document_graph_runs/pilot-20261110/release-all-tests.log)，逐项验收与文件哈希见[实现审计](../data/document_graph_runs/pilot-20261110/implementation-audit.json)。

首轮范围为固定的13页与已识别符号类型；未实现扫描 OCR、完整跨页表格恢复、所有跨来源别名归一或聊天前端接入。未标注的其余事实仍需抽样复核。
