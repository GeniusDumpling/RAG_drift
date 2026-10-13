# 当前情报数据库数据概览

统计日期：2026 年 10 月 9 日。

统计依据：当前 PostgreSQL 数据库的只读查询，以及项目中的数据库模型定义。数据会随采集任务运行而变化。

情报内容主要覆盖无人机技术与故障、YouTube 视频资料、大疆供应链及 IEEE 学术文献。

## 1. 数据类型与内容覆盖

| 数据类型 | 当前规模 | 内容覆盖 |
|---|---:|---|
| 论坛帖子 | 213 条 | ArduPilot 100 条、PX4 93 条；DJI Lito 系列主题 10 条、评论 10 条 |
| YouTube 视频情报 | 334 条 | 无人机导航、GPS、抗干扰、维修、零部件、产品功能及行业动态；主要存储中文摘要，部分附字幕和关键帧信息 |
| 供应关系 | 63 条 | 采购方均为“大疆”；涉及电池、电机、光学镜头、CMOS、通信芯片、结构材料、代工、降落伞及售后等，也包含相机、扫地机器人等产品线 |
| 供应关系验证 | 63 条 | 保存验证判定、可信度、证据说明和来源链接；22 条“确认”、41 条“待确认” |
| 学术文献 | 11 篇 | IEEE 文献，发表于 2021—2026 年；覆盖 SLAM、多传感器融合、激光雷达、避障与路径规划、控制算法、多目标跟踪、无人机集群通信及电池优化 |
| 文献研究成果 | 7 次任务、43 个产物、102 条证据 | 研究查询为“无人机”和“激光雷达”；3 次成功、4 次失败，已保存 3 份最终报告、3 份 PDF 和 3 份全文 |
| 检索切片 | 4,592 条 | 包括 3,712 条字幕片段、327 条视频摘要切片，以及 553 条其他文本切片 |

论坛和视频合计为 `content_items` 表中的 547 条内容；供应关系和论文分别存放在专用表中。切片、证据和研究产物是相关内容的衍生数据，不应与内容条数直接相加。

论坛内容以英文为主，重点覆盖 GPS 欺骗与干扰、MAVLink 安全、固件漏洞、失控飞走、EKF 失效保护、ESC 故障、螺旋桨、RTK、罗盘校准。英文帖的发布时间跨度为 2014 年 2 月至 2026 年 9 月。

## 2. 各类情报的主要字段

论坛和视频共用 `content_items` 表，通过 `item_type` 区分类型。当前实际类型包括 `post`、`thread`、`comment` 和 `video_description`。

| 数据类别 / 表 | 主要字段及含义 |
|---|---|
| 通用内容 `content_items` | `item_type` 类型；`title` 标题；`canonical_url` 标准链接；`source_url` 来源链接；`published_at` 发布时间；`language` 语言；`raw_text` 原文；`cleaned_text` 清洗正文或视频摘要；`summary_text` 摘要；`tags` 标签；`metadata_json` 专有信息 |
| 来源与关联，位于 `content_items` | `source_site_id` 来源；`raw_page_id` 原始快照；`crawl_run_id` 采集批次；`author_id` 作者；`parent_item_id` 父内容；`thread_root_id` 主题根节点；`content_hash`、`dedup_key` 去重标识 |
| 论坛专有信息，位于 `metadata_json` | ArduPilot/PX4：`forum`、`query`、`post_id`、`post_number`、`category_id`、`like_count`；DJI：`tid`、`pid`、`series`、`views`、`replies`、`likes`、`position`、`thread_title` 等 |
| 视频专有信息，位于 `metadata_json` | `video_id`、`video_page_url`、`channel`、`description`、`duration_seconds`、`caption_source`、`caption_language`、`transcript_segment_count`、`video_summary`、`keyframes`；关键帧记录包含 `timestamp_seconds`、`local_path`、`byte_size` |
| 供应关系 `supplier_relations` | `supplier_name` 供应商；`buyer_name` 采购方；`supply_content` 供应内容；`credibility` 抽取可信度；`seen_count` 出现次数；`source_urls` 来源链接；`verify_status` 验证状态 |
| 供应验证 `supplier_verifications` | `relation_id` 对应关系；`verdict` 判定；`confidence` 可信度；`supply_content` 供应内容；`evidence_md` 证据说明；`evidence_urls` 证据链接；`orig_source_urls` 原始链接；`verify_time` 验证时间 |
| 论文信息 `literature_papers` | `title`、`doi`、`ieee_article_number`、`authors_json`、`abstract`、`publication_title`、`publication_year`、`citation_count`、`access_type`、`document_url`、`pdf_url`、`raw_metadata_json` |
| 论文筛选与分析 `literature_run_papers` | `paper_id`、`literature_run_id`、`direction_title`、`search_query`、`score`、`selected`、`abstract_zh` 中文摘要、`match_how`、`match_use`、`conclusion`、`analysis_json` |
| 文献证据 `literature_evidence` | `matched_term` 匹配词；`match_level` 匹配等级；`evidence_text` 证据文本；`evidence_source` 来源；`page_number`、`section_name`、`char_start`、`char_end` 定位；`verified` 核验标记 |
| 文献产物 `literature_artifacts` | `artifact_type` 产物类型；`json_data`、`text_data`、`binary_data` 内容；`mime_type`、`byte_size`、`sha256`、`source_url` |
| 检索切片 `content_chunks` | `content_item_id` 所属内容；`chunk_index` 序号；`display_text` 展示文本；`embed_text` 向量化文本；`chunk_metadata_json` 证据类型、字幕起止秒数等；`embed_status`、`vector_backend`、`vector_point_id`、`embedded_at` |

JSON 元数据字段因来源和采集流程而异，上表中的专有字段并非每条记录都具备。

字段定义参考：

- [内容模型](../backend/app/models/content.py)
- [供应商模型](../backend/app/models/supplier.py)
- [文献模型](../backend/app/models/literature.py)
- [采集控制模型](../backend/app/models/control.py)
- [检索及实体模型](../backend/app/models/search.py)

## 3. 支撑数据

| 支撑数据 | 数量 | 主要字段 |
|---|---:|---|
| 数据源 `source_sites` | 7 | `name`、`site_type`、`base_url`、`fetch_mode`、`config_json` |
| 原始快照 `raw_pages` | 947 | `requested_url`、`final_url`、`http_status`、`raw_html`、`raw_text`、`raw_json`、`fetched_at`、`parse_status`、`fetch_error`、`parse_error` |
| 作者 `authors` | 188 | `display_name`、`handle`、`profile_url`、`external_author_id` |
| 采集任务 `crawl_jobs` | 7 | `source_site_id`、`name`、`trigger_mode`、`cron_expr`、`seed_config_json`、`parser_profile`、`enabled` |
| 采集运行 `crawl_runs` | 475 | `source_site_id`、`crawl_job_id`、`status`、`started_at`、`finished_at`、各阶段计数、`config_snapshot_json`、`error_message` |
| 采集事件 `crawl_run_events` | 605 | `crawl_run_id`、`stage`、`level`、`event_type`、`message`、`related_url`、`counters_json`、`agent_trace_json` |
| 检索历史 `search_queries` | 66 | `raw_query`、`filter_json`、`optimized_query_text`、`result_count`、`query_trace_json` |
| 文献任务 `literature_runs` | 7 | `query`、`status`、`current_stage`、进度字段、`directions_json`、`picks_json`、`analyses_json`、`final_report_markdown`、`error_message` |
| 文献任务事件 `literature_run_events` | 109 | `literature_run_id`、`stage`、`level`、`event_type`、`message`、`counters_json`、`trace_json` |

多数业务表还包含 `id`、`created_at`，部分包含 `updated_at`，用于标识记录和追踪入库、更新时间。

## 4. 当前覆盖边界

- **供应商记录数不等于独立企业数**：存在名称变体、母子公司以及未指名的供应商集合。关系表虽全部标为“明确”，最终验证仅有 22 条“确认”；39 条“已验证”表示完成验证流程，另有 24 条“验证失败”。
- **视频语言字段表示中文摘要**，原始字幕包含中英文；当前视频的 `published_at` 均为空，无法直接统计视频发布时间覆盖。
- 视频样例中也存在游戏、虚拟围栏等偏离核心无人机主题的内容；入库摘要的相关性和质量存在差异。
- **11 篇论文都有摘要和 PDF 链接，实际保存 PDF 与全文的各为 3 篇。**
- 关键帧元数据记录本地图片路径、时间戳和大小；这不代表图片二进制存储于 PostgreSQL 中。
- 4,090 条切片在 PostgreSQL 中标记为已成功写入 Qdrant，502 条待处理；本次未独立核对 Qdrant 实际点数。
- 实体表 `entities`、实体提及表 `content_entity_mentions` 和模型调用表 `agent_calls` 当前均为空。
- 7 个数据源中，FlyForum 演示源当前没有内容记录。
