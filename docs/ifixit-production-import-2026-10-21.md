# iFixit 正式库导入与真实向量化结果

2026 年 10 月 21 日完成正式 PostgreSQL 导入与 SiliconFlow `BAAI/bge-m3` 真实模型向量化。导入任务状态为 `success`，失败计数为 0。

| 项目 | 结果 |
|---|---|
| 正式数据库 | `intelligence_rag` |
| 来源 ID | `2a07a917-e1dc-4faf-a220-637589f7d52a` |
| 导入运行 ID | `53c6e2b3-43b1-41c5-bf01-18433198c13b` |
| 指南／原始快照 | 391／391 |
| 保留的结构化步骤 | 2,028 |
| 检索切片／成功索引 | 3,502／3,502 |
| 嵌入模型／维度 | `BAAI/bge-m3`／1024 |
| Qdrant 集合 | `content_chunks_bge_m3_v1` |
| 本次来源在 Qdrant 中的向量点 | 3,502 |
| 导入失败／索引失败／待清理旧点 | 0／0／0 |

正式数据包为 [20261021-production-bge-m3](../data/processed/ifixit/20261021-production-bge-m3/)。准备阶段使用从 Hugging Face 下载的 BGE-M3 tokenizer，包含上下文前缀的预算为 480 token、1600 字符，超限切片数为 0。与早期使用 BGE-small tokenizer 的 3,501 个切片相比，正式分块结果为 3,502 个。

通过项目现有嵌入配置解析出的 SiliconFlow 服务批量生成真实向量，共 219 次批量请求，服务返回的输入 token 合计 262,719；不含接口预检和验收查询。生成结果按模型和文本哈希持久化缓存，随后通过导入工具的 `index_bundle` 同步正式向量点与数据库索引状态。缓存保存真实模型返回值，没有使用测试向量。

验收逐篇比对了 391 份原始 Markdown／JSON、规范化正文与步骤记录，确认原始内容及前置指南展开顺序一致。逐块比对了 3,502 个切片的正文、索引状态、模型、维度、向量点 ID 和待清理任务；逐点读取了 Qdrant 的全部对应 ID 与来源载荷，并抽查 20 个向量的实际维度。运行记录中的 `embedded_count=3502`、`error_count=0`。

正式 `/search` 接口的三条中英文查询均成功返回本来源内容，向量检索分支均无错误。直接向量检索中，“DJI Mavic Pro GPS 模块更换”首条为指南 114388，“DJI Mini 2 battery replacement”首条为指南 149601，符合查询目标。

现有混合排序仍有局限：GPS 查询的前五条被同型号外壳拆卸指南占据，电池查询也出现其他维修任务优先。直接向量查询能返回对应指南，问题表现为型号关键词的高分结果压过相关语义结果；本次未修改或部署检索排序代码。上述查询属于可用性检查，不代表已完成正式召回率评估。

空步骤指南及其视频链接继续保留，其中 5 篇缺乏有效正文，未生成检索块。图片保留本地文件清单和远端引用，尚未核验本地文件与具体步骤的对应关系；本次未进行图片内容向量化或视频转录。

可追溯产物：

- [数据包与输入校验清单](../data/processed/ifixit/20261021-production-bge-m3/manifest.json)
- [正式导入报告](../data/processed/ifixit/20261021-production-bge-m3/production-import-report.json)
- [真实模型索引报告](../data/processed/ifixit/20261021-production-bge-m3/production-index-report.json)
- [数据库、向量点和检索验收记录](../data/processed/ifixit/20261021-production-bge-m3/production-verification-report.json)
- [实际向量化运行脚本](../data/processed/ifixit/20261021-production-bge-m3/production-vectorize.py)

数据包、向量缓存和运行产物位于本地 `data` 目录，不随代码分发。导入工具使用说明见 [tools/ifixit_rag/README.md](../tools/ifixit_rag/README.md)。
