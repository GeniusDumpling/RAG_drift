# 视频图谱 v2：身份解析、语义审核与版本回补

v2 根据 [2026-11-02 数据审计](video-graph-quality-audit-2026-11-02.md) 修复全局泛称合并、名称变体分裂、无依据关系与旧版本缓存复用问题。原始视频与字幕不改写，只有通过来源和语义检查的结果进入图谱。

历史策略`148440447295`达到预定开发回归门槛，见[2026-11-02最终验收](video-graph-v2-release-2026-11-02.md)：正确目标保留27/33、已知错误拦截28/28，新边抽查54条支持、3条错误、3条待复核。它不是独立盲测或全库准确率；正式图谱未回补。[早期失败快照](video-graph-v2-acceptance-2026-11-02.md)保留供追溯。

当前工作区试验实体提取及审核、实体对关系分类和分批独立审核；本轮设计与实测记录见[流程改进](video-graph-staged-extraction.md)。历史验收结果不自动适用于新的规则指纹。 当前实体审核之后增加了不调用模型的[实体归一层](entity-normalization-2026-11-04.md)。 当前实体规则、领域词表和错误分类已补齐，见[最新实体实测](entity-extraction-domain-improvements-2026-11-04.md)。

## 抽取与身份

1. 读取当前字幕；没有字幕时仅对摘要提取实体提及。
2. 判断视频主题相关性，然后按切片识别实体与属性，保持原文提及。
3. 先独立审核实体的原文、类型、规范身份、归属、属性与检索价值；相同表面名称可对应不同设备部件，身份明确后再去重。原文明示的无法开机等故障可由规则增加候选，但仍需审核。
4. 按类型兼容性和原文位置产生有界候选实体对，逐对判断有无关系。候选关系再由独立小批次调用审核主体、对象、方向、否定和条件，允许在端点不变且引文支持时修正谓词及方向。
5. 程序验证原文位置、类型角色、身份依据、属性值及审核与输入的一致性。先对空白及排版标点做受限匹配，简繁体回退仅在字符位置可保留且唯一定位时接受；统一恢复为源文字符保存证据。不修补缺失词或拼接品牌名称。
6. 保存切片检查点与拒绝原因，按完整/部分完成策略原子发布。

`surface_text` 保持原始提及，新增 `canonical_name`、`identity_scope`、`identity_quote`、`identity_source` 表达规范身份及其依据。可明确消歧的 `drone`、`it`、`the app` 可以指向具名实体；其原始泛称别名只在当前视频有效。无法消歧的泛称和具体设备部件保留 `video` 作用域，不能跨视频聚合为同一设备。

实体 UUID 使用 v2 命名空间键、类型、规范名称和作用域生成。具名实体可以跨视频共享；视频内实体带视频 ID。短型号未明确制造商时保持局部作用域，不能从同视频其他段落里碰巧出现的品牌自动补全。型号的代际和 Pro/S/4K 等后缀保留。中英文、简繁体及明确的软件别名通过受控映射统一。

每个别名保存 `scope_key`；不同上下文里相同的提及可以指向不同实体。全局实体检索使用明确的全局别名，只有指定视频后才使用该视频的泛称别名，防止用 `drone` 触发跨视频错误扩展。

IP、数值、金额、单位和配置文件名不作为硬件或软件实体。通过审核的属性保留在原始提及上（Neo4j `VGMention.attributes_json`、PostgreSQL 提及 metadata），不把某次配置覆盖到所有同名型号。一个无依据属性只会被拒绝，不会连带删除有效实体及其关系。

## 关系语义

保留原有八种关系，并增加：

| 关系 | 方向 |
|---|---|
| `manufactured_by` | 产品/部件 → 制造组织 |
| `developed_by` | 产品/软件/技术等 → 开发组织/个人 |
| `installed_on` | 部件/产品 → 安装对象 |
| `distributed_via` | 软件 → 分发渠道 |
| `organizes` | 组织/个人 → 具名活动 |
| `affiliated_with` | 个人 → 任职/隶属组织 |
| `supports` | 设备/软件等 → 所支持的软件、协议、技术或方法 |
| `connected_to` | 设备/部件等 → 明确连接的设备/部件 |
| `controls` | 设备/软件/组织/个人 → 明确控制的设备或技术对象 |
| `packaged_with` | 产品 → 包装随附配件、工具或资料 |
| `offered_by` | 产品/软件/产品线 → 所属公司或品牌 |
| `has_model` | 产品线 → 具体型号 |
| `equipped_with` | 明确军事单位/编队 → 当前列举语境中的装备 |

增加 `person`、`event`、`data`、`document`、`product_family` 类型，区分人、事件、分析数据、资料和产品线。没有合适类型或关系时允许拒绝；比较、开发归属、软件分发和联合举办不得强行变成兼容、应用或使用关系。

审核返回单一关系谓词、事实适用范围 `modality`，以及条件、计划、假设或设计目标的逐字范围依据。程序从已审核谓词和端点类型派生内部来源校验类别`basis`，不让模型重复分类；该类别只用于校验凭据。程序拒绝包装清单冒充内部组成、模拟冒充兼容、参数冒充技术等。保护不等于包含，推出不等于研发。限定条件随证据返回并进入检索上下文。

`equipped_with` 是有限的来源规则投影，不在模型候选语言中：只对已获审核接受、且原文明示阅兵/军事语境及单位包括/装备对象的列举关系，纠正原来宽泛的contains，保存具体语境。它不证明常设编制或采购情况，不适用于公司产品目录。

审核按实体 key 和关系 index逐项处理；遗漏、重复或无效的审核项不能视为通过。有界修正后仍无效的项被隔离，其他合法决定继续保留；整体实体审核没有任何可用决定时仍失败。引文可以扩展为当前切片内的跨句范围，以覆盖先行词和限定条件。原文定位检查与模型语义审核分别执行，原文存在本身不表示关系成立。模型审核仍会有相关性误判和漏抽，因此还需要固定样本的保留率与新输出人工复核。

## 版本与检查点

默认版本为 `video-graph-v2:<model>:<policy-hash>-<extraction-effort>-<review-effort>`。规则指纹覆盖抽取请求、提示词、身份解析、结构与语义约束。模型或规则变化后生成不同构图版本，不复用 v1 结果；同名模型背后的服务改变时仍需手动升级基础版本。

每份 v2 结果包含审核记录，绑定规则指纹、实体关系结果哈希、视频 ID、标题、摘要上下文、当前切片内容/类型/时间和邻近上下文。保存、复用和发布时重验；仅当前切片文字不变，但标题或邻近上下文变化，也不能复用原审核结果。审核不是密码学签名，作用是防止内部流程把缺失、过期或被修改的缓存误当作已核验结果。

模型超时、连接故障、429/5xx 或响应 JSON 解析故障最多重试一次。实体识别、关系抽取或审核结构/覆盖不合法时最多各修正一次，只反馈字段路径、类型和漏审项。审核修正保留第一次已经合法的决定，不能用格式重试翻转语义拒绝。每次请求前续租；单次请求默认 120 秒，小于 300 秒任务租约。认证错误不重试；生产日志不保存服务响应全文、密钥或服务地址。

## 固定范围回补

先应用 PostgreSQL 迁移 `0010_video_graph_alias_scope`（可使用 `alembic upgrade head`），再运行新代码。Neo4j 不需要改写历史实体，按新命名空间写新版本即可。首次上线先完成固定样本验收，再选定少量视频做回补。

```bash
# 只读数据库、写本地清单；不排队，不调用模型。
.venv/bin/python tools/video-knowledge-graph/build_graph.py plan-backfill \
  --manifest data/video_graph_runs/v2-backfill.json \
  --content-id VIDEO_UUID

# 最多选择2个视频，每批只排队1个。不会把清单剩余视频全部排队。
.venv/bin/python tools/video-knowledge-graph/build_graph.py backfill \
  --manifest data/video_graph_runs/v2-backfill.json --limit 2 --batch-size 1

# 同一清单可继续；重新核对数据库状态，跳过已完成版本。
.venv/bin/python tools/video-knowledge-graph/build_graph.py backfill \
  --manifest data/video_graph_runs/v2-backfill.json --limit 2 --batch-size 1

# 明确重试清单中之前失败或部分完成的视频，复用仍有效的检查点。
.venv/bin/python tools/video-knowledge-graph/build_graph.py backfill \
  --manifest data/video_graph_runs/v2-backfill.json --limit 2 --retry-failed
```

不传 `--content-id` 的 `plan-backfill` 会盘点全部源视频并生成完整候选清单，仍不会调用模型或写图谱。清单固定目标版本、视频 ID、材料指纹和原有效版本，并用计划哈希防止误改。已完成项目以数据库事实为准，不能仅凭清单中的游标推断完成。

排队前再次检查源材料和有效版本，并在视频锁内检查基线版本；来源变化或其他版本抢先发布时拒绝旧计划。清单也会记录来源失效、撤回及版本冲突，必须查看这些状态，不能把命令退出当成全部完成证明。规则或配置改变后应重新生成清单，旧清单不能用于不同目标版本。

失败或新版本部分完成保留旧图谱；新版本全部适用切片通过后切换有效指针，历史证据保留。首次构图的部分结果按原有策略可以发布。回补命令遇到本轮失败、部分完成、租约丢失或任务过期返回非零退出码。旧的 `run/scan/watch` 仍有扫描整个范围的语义，`run --limit` 不能代替固定清单回补。上线回补时应先确认没有旧代码的 `watch` 进程持续写回旧目标版本。

## 配置与验收

```dotenv
VIDEO_GRAPH_REQUEST_TIMEOUT_SECONDS=120
VIDEO_GRAPH_EXTRACTION_EFFORT=none
VIDEO_GRAPH_REASONING_EFFORT=none
```

DeepSeek 初抽和独立审核默认均关闭思考；这两个档位均参与任务版本。参数使用模型提供方的 [thinking/reasoning 控制接口](https://console.volcengine.com/ark/region%3Acn-beijing/docs/deepseek-v4-1-flash)。其他模型不会自动附加 DeepSeek 专有参数。

固定验收集位于 `scripts/evaluation_data/video_graph_quality_cases.json`：原审计的60条随机关系与9条诊断关系，33个正确来源事实、28个已知错误、8个待复核样本。原文和人工标签固定，提示词不接收标签。验收同时记录命中的具体关系边。有限的等价关系映射、最多三跳的有向组成链以及同一主体的明确复合能力关系可以覆盖原事实；共同上级不能证明两个部件相互包含。已知错误不能靠改成 uncertain 通过。

```bash
# 查看范围，不调用模型。
.venv/bin/python scripts/evaluate_video_graph_quality.py \
  --output data/video_graph_runs/v2-acceptance.json --dry-run

# 实际模型运行；逐项存盘，重复命令复用相同版本已完成结果。
.venv/bin/python scripts/evaluate_video_graph_quality.py \
  --output data/video_graph_runs/v2-acceptance.json --workers 4

# 仅重试真实运行错误。
.venv/bin/python scripts/evaluate_video_graph_quality.py \
  --output data/video_graph_runs/v2-acceptance.json --workers 4 --retry-errors
```

常规每段最多三次模型调用：实体识别、关系抽取、独立审核；摘要或没有实体锚点时跳过关系阶段，完整视频另有一次主题判断。关系阶段的必要端点补充仍须通过独立审核。

69个回归目标来自66份不同输入；同一来源只抽取一次，所有目标共享完全相同的输出。来源、标签和有限匹配注释固定，不能为不同目标挑选不同结果。

评测可显式使用`--response-cache data/video_graph_runs/responses-20261102`，记录精确请求和模型响应。缓存键绑定请求正文、模型参数及服务/账号的哈希；不保存明文凭据。后处理规则变化可复用相同响应，但必须重新验证当前策略及生成审核凭据。提示词或来源变化需要新请求；`--cache-only`只允许精确重放，缺失时失败。每份结果记录实时/重放阶段和请求哈希。`--retry-errors`仅针对执行错误开启新尝试，不能重试成功但漏抽的案例来挑结果。

新增关系来源复核由`scripts/review_video_graph_quality.py sample/check`固定种子抽取60条，并校验来源、版本、样本及门槛未被修改。要求至少51条来源支持、至多3条明确错误、至多6条待复核；旧目标回归过线不能替代此门槛。

自动门槛要求全部样本执行成功、正确事实保留率至少80%、已知错误拦截率至少90%。该门槛仅验证固定目标，不代表全库准确率；必须另行对新生成的关系进行原文复核，检查新增错误与实体身份，不能只看旧错误消失。
