# 论坛主题级 item 改造与数据迁移（2026-10-22）

已将已入库 ArduPilot/PX4 主题调整为一个主题一个 `content_items`，主帖和回复的全部切片归属于该 item。保留主帖 item ID，回复 item 的内容、来源和处理状态迁入主题；不重新抓取论坛，不重新计算向量。

| 来源 | 迁移前 item 数 | 迁移后主题 item 数 | 保留帖子正文数 | 保留 chunk 数 | 核验向量数 |
| --- | ---: | ---: | ---: | ---: | ---: |
| ArduPilot | 2,341 | 107 | 2,341 | 3,136 | 1,191 |
| PX4 | 551 | 109 | 551 | 895 | 895 |
| 合计 | 2,892 | 216 | 2,892 | 4,031 | 2,086 |

chunk 总数包含 7 个历史 `obsolete` 切片；4,024 个当前切片继续遵循现有过滤和检索规则。原有向量化状态保留，本次没有将 pending/failed 切片调用模型补齐。

## 数据结构和增量更新

- `structured_by=discourse-v3.0`，`metadata_json.forum_item_layout=topic-v1`。
- item 身份由规范化论坛地址和 `topic_id` 确定，沿用现有表与 `dedup_key` 唯一约束，不新增业务表或列。
- item 的 `cleaned_text` 包含按楼层排列的全部可见正文；`metadata_json.posts` 按 `post_id` 保存逐条正文、作者、楼层、回复对象、过滤状态和处理指纹。
- 每条 chunk 保留帖子内序号和字符区间，并保存作者、发帖时间、回复原文 URL、原始快照 ID、采纳状态。其 `char_start/char_end` 对应主题 item 的完整正文；历史失效切片的当前字符区间置空。
- 新增回复只追加该回复的切片；编辑正文或上下文时，仅重建受影响的帖子切片。完整快照确认某回复消失时淘汰其切片，恢复后重新生成；主题 item ID 保持稳定。
- 过滤回复仍保留正文和快照；不符合检索策略的回复不生成检索切片。
- 检索证据和向量 payload 使用 chunk 的回复作者、时间及原文链接，避免引用成主帖。
- `legacy_item_ids` 保存旧 item 映射；内容详情 API 接收旧回复 item ID 时返回对应主题及其切片。

## 迁移与恢复点

脚本：`tools/forum-crawler/scripts/discourse_migrate_topics.py`。默认只读预览，`--apply` 执行归并。迁移持有两个论坛的 advisory lock，按论坛提交事务，再更新 Qdrant payload；更新失败通过 `vector_payload_pending` 持久化重试。

恢复文件保存在 `data/backups/forum-topic-20261022/`（已由 gitignore 排除）：

- `database.dump`：迁移前 PostgreSQL 完整自定义格式备份，约 56 MB。
- `ardupilot.before.json.gz`、`px4.before.json.gz`：原 item/chunk 记录，以及对应 Qdrant point 的原 payload 和原向量值。
- `verification.json`：正式库逐条核验结果。

已完成核验：所有 chunk ID、展示文本、嵌入文本、向量化状态和向量引用与迁移前一致；2,086 个 point 的向量值逐条一致，item 归属、帖子 ID 和回复链接正确。全部主帖 ID 保留，逐条帖子来源覆盖完整，当前切片字符区间正确。重复执行迁移时归并数量为 0、向量更新数量为 0、失败数量为 0。

迁移中首次向量更新因联表结果逐 chunk 重复加载整帖 JSON/正文而触发内存不足；已改为分别读取 chunk 与去重后的 item，并为元数据队列在 SQL 层限制批量大小。正式续跑完成，峰值内存约 238 MB。检索与通用向量重建也已避免相同的大正文重复加载。

## 运行环境

应用及后续 Docker 采集任务使用本次代码镜像：

- 应用：`rag-forum-topic-items:20261022`。
- 采集器：`rag-collector-forum-topics:20261022`。
- 可复现构建文件：`docker/Dockerfile.forum-topic-update`，分别基于原应用和采集器镜像叠加代码，保留现有模型和浏览器依赖。
- 原镜像：`rag-retrieval-fix:20261021-v1`、`rag-collector:20261014-playwright`，仍保留。
- 两个论坛定时器仍为 `inactive/disabled`，本次未恢复自动采集。

## 验证

- 论坛 PostgreSQL 随机 schema 回归、定时器及任务入口回归：47 项通过；测试 schema 自动清理。
- 共享检索、向量重建、切片及排序回归：55 项通过；使用独立临时测试数据库及 fake 查询模型，测试库已删除。
- 新应用/采集器镜像导入检查通过，应用容器健康检查通过。
- 可选 sentence-transformers 模型测试有 2 项因本地模型不可用且沙箱禁止下载而失败；本次没有修改模型实现。这不影响已入库向量的逐条一致性核验。
