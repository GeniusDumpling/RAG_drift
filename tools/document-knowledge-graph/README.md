# 官方手册多模态图谱

流程、Neo4j 数据结构、命令和限制见 [文档](../../docs/document-knowledge-graph.md)。

- `pilot_selection.json`：两本固定原始 PDF、版本、哈希和试点页码。
- `build_graph.py`：准备、构建、重试/恢复、查询、验证、撤回/恢复来源。
- `review_sources.py`：原文及原图预览，无模型调用。
- `evaluate_pilot.py`：逐页真实抽取，保存结果和精确响应缓存，不发布图谱。
- `../../scripts/evaluate_document_graph_gold.py`：19 个事实/关联目标、4 个负例及 HTML 对照报告。

图谱只写 Neo4j；原始 PDF 和图片放在 `DOCUMENT_GRAPH_ARTIFACTS_DIR` 下，以 SHA-256 寻址。模型与 Neo4j 配置沿用项目设置。golden 初稿尚待人工复核。
