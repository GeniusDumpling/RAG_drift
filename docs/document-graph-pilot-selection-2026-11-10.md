# 官方文档多模态图谱试点选材

已检查本地下载索引及6本候选原始 PDF，并目视核对所选两本手册的8张样页。首批固定为 **DJI Air 2S 用户手册**与 **Parrot ANAFI Ai User Guide**，覆盖中文/英文、正文、参数、部件编号图、操作图与灯态符号表。

选材阶段仅完成来源和样页定位；后续构建、golden 与 Neo4j 验证见[文档图谱流程](document-knowledge-graph.md)。

| 项目 | DJI | Parrot |
|---|---|---|
| 手册 | DJI Air 2S 用户手册 | ANAFI Ai User Guide |
| 手册版本 | v1.0 | 7.7.1.7 |
| PDF 内日期 | 封面2021.06 | 封面 Updated: April 02, 2025 |
| 语言 | 简体中文 | 英文 |
| 原始 PDF 页数 | 55 | 89 |
| 选择原因 | 篇幅较小、图文齐全，已有带坐标及图片质检的解析结果 | 编号图清楚，具备英文多栏参数、图文操作步骤和灯态表，软件/硬件角色明确 |

## 固定来源

- [DJI 原始 PDF](</home/gaowei/projects/RAG_drift/data/DJI无人机文档/air-2s/DJI Air 2S   - 用户手册 v1.0_bbbf5d08e32f.pdf>)。
  - 官方下载地址：[DJI Air 2S 手册](https://dl.djicdn.com/downloads/DJI_Air_2S/20240528UM/DJI_Air_2S_User_Manual_v1.0_CHSV.pdf)。此地址取自本地索引，本次没有联网重新下载。
  - SHA-256：`f0aaca30a8d3218232a8bfe1829562c973690bacc3661f50d3c6cc857df440e2`。
- [Parrot 原始 PDF](</home/gaowei/projects/RAG_drift/data/Parrot无人机文档/anafi-ai/[图像_ 见原始网页] Download_7deb4150fce3.pdf>)。
  - 官方下载地址：[ANAFI Ai User Guide](https://www.parrot.com/assets/s3fs-public/2023-01/ANAFI-Ai-user-manual.pdf)。此地址取自本地索引，本次没有联网重新下载。
  - SHA-256：`78a771b86a8dda041fc77af8e3c3744f35cdba3e65585af6d76a50e0b08ff67b`。

两个文件均可正常打开，不需密码，文件哈希与本地索引一致。保留原始文件名，通过手册标题与哈希识别内容；Parrot 文件名中的“见原始网页”不表示这个 PDF 缺少图片，已确认 PDF 内的示意图完整可见。

## 首轮范围：8组材料、13页

以下全部采用**原始 PDF 从1开始的物理页码**。DJI 的印刷页码比这里少1，例如 PDF 第9页的页脚为8；Parrot 所选页的印刷页码与物理页码一致。

| 验证内容 | DJI Air 2S PDF页 | Parrot ANAFI Ai PDF页 | 主要复核点 |
|---|---:|---:|---|
| 部件编号与身份 | 9 | 17–18 | 连接图中编号、部件名称与所属机型；区分实体及标注区域 |
| 参数、单位与条件 | 50–53 | 14–15 | DJI参数表、Parrot多栏条目及尺寸图；保留测试条件和参数所属对象 |
| 操作步骤与图示 | 26 | 19 | 螺旋桨安装/拆卸；microSD及nano SIM安装、位置与方向 |
| 灯态符号与状态表 | 28 | 23 | 常亮/闪烁/熄灭、区间边界、充电与查看剩余电量的不同条件 |

这些页用于确定试点覆盖范围，不是已经人工确认的实体/关系答案。可先按8组材料进行抽取与人工复核，再决定扩展整本或增加软件界面、返航、故障处理等章节。

灯态表尤其适合验证图片利用：DJI 使用灰度圆点、闪烁图例；Parrot 使用绿色及斜线图标，并在同页分别描述充电与使用时的状态。纯文本提取会丢失这些视觉符号，不能把“两家都是四个LED”直接归一为同一套规则。Parrot尺寸图标注“Images not to scale”，不能从未标注的图形比例推算尺寸。

## 可复用数据与已发现的问题

DJI 可复用 `data/processed/drone-documents/air-2s-quality-20261027-v4/` 中**本手册对应的部分**：

- `document_version_id`：`5f2e348c-e64d-5cb9-ad69-914c208a019c`。
- `processing_id`：`ce6aa281-0786-5d7d-ab9b-366d24de1304`。
- 本手册现有142个切片，其中正文65、表格4、参数3、图示描述70；86个图示候选中12个描述被隔离。
- 这个数据包同时含其他PDF和技术参数网页，试点不能按整个数据包计入证据或混用其他资料。
- 已有图片描述是模型派生内容，不作为原文金标准；被隔离描述需要回原图复核。

现有解析包将 DJI 手册 `published_at` 记为2024-05-28，但 PDF封面和修订日志写的是2021.06；下载URL也含20240528。本次以封面的版本/年月固定来源，不把下载路径日期作为出版日期，不补造具体发布日期，未修改旧解析包或数据库。

Parrot URL含2023-01，但 PDF封面明确更新于2025-04-02。手册版本7.7.1.7也不能当作飞行器或遥控器固件版本；第2页另列了生态系统的软件版本。当前本地 `data/processed/parrot-documents/` 未发现可复用的解析数据包，下一阶段需要针对所选这一个PDF生成新的试点包。

## 其他候选为何暂缓

| 候选 | 页数 | 暂缓原因 |
|---|---:|---|
| DJI Mini 4 Pro 用户手册 v1.4 | 111 | 内容更长，首轮人工复核成本高于Air 2S |
| DJI Mavic 3 Pro 用户手册 v1.4 | 113 | 更长，遥控器/相机配置更复杂，适合后续泛化验证 |
| Parrot ANAFI User Guide | 100 | 本地PDF开头含空白页，版本信息不如ANAFI Ai封面明确 |
| Parrot ANAFI USA User Guide | 91 | 可作为后续验证集；先用ANAFI Ai验证通用结构，避免第一轮扩大产品范围 |

候选统计见 `data/document_graph_pilot/20261110/candidate-audit.json`；简洁的固定来源清单见 `selection.json`。8张实际查看的样页也保存在该目录。图形统计只表示页面存在栅格图或矢量绘制，可能包含页眉、线条和装饰，不能据此直接认定为有效技术图示。
