# 文献独立数据库迁移记录

2026-10-26 16:52（北京时间）完成正式迁移。新数据库为同一 PostgreSQL 实例上的
`literature_research`，原数据库为 `intelligence_rag`。

## 结果

| 表 | 迁移前记录数 | 迁移后记录数 |
| --- | ---: | ---: |
| `literature_runs` | 7 | 7 |
| `literature_papers` | 11 | 11 |
| `literature_run_events` | 109 | 109 |
| `literature_run_papers` | 11 | 11 |
| `literature_artifacts` | 43 | 43 |
| `literature_evidence` | 102 | 102 |
| 合计 | 283 | 283 |

六张表已从主库删除，原库其余 24 张表保留。文献库仅包含六张文献表及独立的
`alembic_version`。7 个历史任务的状态仍为 3 次成功、4 次失败，没有重新执行研究任务。

## 校验与切换

- 迁移前确认没有运行中的文献任务，也没有其他业务表通过外键引用文献表。
- API 停止期间取得源表写锁，使用同一数据库快照进行备份、复制和校验。
- 比较了每条记录完整内容的 SHA-256，包括 JSON、文本、时间戳及 PDF 二进制；全部一致。
- 字段定义、外键、约束和索引全部一致。校验成功后，在事务中使用 `DROP TABLE ... RESTRICT`
  移除原表，没有执行 `CASCADE`。
- 文献 API 和 worker 使用 `app.db.literature` 的独立连接池；主库模型元数据不再包含文献表。
- 主库迁移版本为 `0008_literature_database_split`，文献库为 `0001_literature_schema`。
  主库升级遇到非空旧文献表会拒绝删除，要求先执行迁移脚本。
- 主库的数据库浏览页只查询主库表；原文献 API 路径及文献页面继续使用。

应用已更新为 `rag-literature-split:20261026`，健康状态为 `healthy`。
镜像基于切换前的 `rag-graph-obsidian:20261026-v10`，只覆盖文献拆库涉及的后端文件，
保留该版本前端及其他已部署代码。根 `.env` 的 `INTELLIGENCE_RAG_IMAGE` 已更新为新镜像。

## 验证范围

- 配置、会话隔离、文献逻辑和部署契约：21 项针对性测试通过。
- 真实 PostgreSQL 集成测试：2 项通过，覆盖全新安装、重复升级、非空源表删除保护、
  完整迁移、中文与 PDF 二进制保留、文献 API 读取及产物下载。使用随机临时数据库并已清理。
- 正式应用：文献列表、结果、事件、PDF 下载、主库概览及首页均返回成功。
- 正式 PDF 下载的长度与 SHA-256 和数据库内容一致。
- 正式 worker 单轮运行确认无排队任务；独立文献会话成功写入测试任务并回滚，未遗留测试记录。
- Ruff、Shell 语法检查、Git 差异空白检查通过。未运行整个项目的全量测试。

## 备份与复现

备份位于被 Git 忽略的 `data/backups/literature-split-20261026/`：

- `literature.dump`：六张表的 PostgreSQL 自定义格式备份，20,631,402 字节。
- `verification.json`：逐表结构、记录数、数据摘要以及迁移完成标记。
- `runtime-before.json`、`environment-before.env`：切换前运行配置，仅供本机恢复，包含私有配置。

备份 SHA-256：

```text
25bba115a9bc237d0a6c0b6e17a552010c220550b2d151e5a54372245526a5fa
```

此次独立构建目录、运行配置及最终验收结果位于
`data/deployments/literature-split-20261026/`。后续迁移命令和新环境配置见
[文献研究说明](literature-research.md)。迁移脚本是
`scripts/migrate_literature_database.py`，要求目标数据库不存在，不会覆盖已有目标库。

如需回滚，先停止文献写入并备份当前文献库，再根据需要将文献数据恢复到主库，
恢复旧应用连接配置，并将主库迁移版本恢复到 `0007_chat_sessions`。
本次 dump 只对应迁移时点；切换后产生的数据应从当前文献库另行迁回。
不要直接运行新版本的 `alembic downgrade`，它不会自动合并两个数据库。

此次没有提交或推送 Git，也未将备份、私有配置或构建产物加入版本控制。
