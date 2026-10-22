# AI 问答实现与运行

AI 问答先检索知识库，再基于本轮证据生成带编号引用的答案。前端使用流式接口，原有 JSON `/answer` 保持可用。

## 证据上下文

- 型号查询保留完整短语（如 `Mavic 3 Pro`），英文词按词边界匹配；数字和通用型号后缀降权，避免 `3`、`pro` 子串主导结果。
- 关键词按查询覆盖度评分，同一词命中多个字段不重复累加；与向量合并时保留相似度差异，弱关键词命中不会直接将分数推满。
- SQL 候选每篇最多保留两个高分切片；普通检索先按内容去重再截取，问答先覆盖不同内容，再允许每篇第二个切片。
- 回答阶段召回 `3 × top_k` 条候选，最多 150 条，再选择最多 `min(top_k, 8)` 条生成证据。
- 先覆盖不同 `content_item_id`，再允许同一内容的第二条证据，避免单个视频或文档占满结果。
- 从 PostgreSQL 读取命中切片的完整正文；预算允许时补充前后相邻切片，排除 `obsolete` 切片。
- 编号证据上下文总上限为 12,000 字符；每条证据公平分配预算，长切片可能截断。
- 界面仍使用短 `snippet`。生成结果记录实际读取的 `context_chunk_ids` 和 `context_chars`，答案中的 `[n]` 对应本轮返回的证据顺序。

检索 trace 的 `ranking_version=phrase-coverage-v1` 标识本次排序规则，`model_phrases` 保存从实际检索问题识别的型号短语。旧视频向量缺少 `item_type` 时允许进入候选，随后仍按 PostgreSQL 的真实类型过滤；新采集视频会写入类型、来源、语言等过滤字段。详细修复与验证见 [检索排序修复记录](retrieval-ranking-fix-2026-10-21.md)。

## 状态追踪与降级

JSON 响应及流式 `done` 事件增加 `generation`：

| 字段 | 含义 |
| --- | --- |
| `status` | `generated`、`fallback`、`no_evidence`、`cancelled`、`error` |
| `reason` | 缺少密钥、模型请求失败、连接断开等原因代码 |
| `model` | 配置的生成模型 |
| `latency_ms` / `total_latency_ms` | 生成阶段 / 全流程耗时 |
| `evidence_count` / `context_chars` / `context_chunk_ids` | 实际证据与上下文统计 |
| `retrieval_query` / `rewrite_status` | 用于检索的独立问题及追问改写状态 |

最终答案、支持证据快照和生成信息保存在 `search_queries.result_summary_json`；生成信息也写入 `query_trace_json.generation`。服务端日志保留模型调用异常。

模型不可用时返回带编号的证据摘录，前端明确标注。没有证据时不调用生成模型。模型流中途失败会先发送 `reset` 替换未完成文本，再以降级结果结束，避免将部分模型输出和原文摘录混合。没有收到 `done` 的连接按未完成处理。

## 会话和追问

新增 `chat_sessions`、`chat_turns` 表，保存用户问题、答案、状态、支持证据和生成诊断。

- `POST /chat-sessions`：创建会话。
- `GET /chat-sessions?limit=50&offset=0`：按最近更新分页查询会话，`limit` 最大 100。
- `GET /chat-sessions/{id}`：读取会话及历史问答。
- `/answer` 和 `/answer/stream` 均接受可选 `session_id`；不提供时维持无会话的单轮行为。
- 每轮最多读取最近 6 条已完成问答，用户问题每条最多 1,000 字符，历史答案每条最多 2,000 字符。
- 追问先结合历史改写为独立问题，再检索；改写失败时拼接最近两条用户问题和当前问题进行检索，并记录降级状态。
- 历史只帮助理解意图，提示词要求事实仍由本轮证据支持，并禁止沿用历史引用编号。
- 同一会话只允许一条待完成请求；连接关闭时保存部分答案并标记 `cancelled`，未完成问答不进入后续模型历史。进程异常留下的待完成请求可在 10 分钟后由新请求回收。

浏览器只保存当前会话 ID，用于刷新或返回页面后从服务器恢复。新建对话不删除原有历史。生成中可停止，离开页面会取消请求。

## 流式协议

`POST /answer/stream` 接受与 `/answer` 相同的 JSON，请求头可设置 `Accept: text/event-stream`。响应关闭缓存及 Nginx 上游缓冲。

事件依次包含：

1. `status`：`retrieving` 或 `generating`。
2. `evidence`：检索记录 ID、问答 ID 和本轮支持证据。
3. `delta`：模型生成的文本增量，形如 `{"text":"内容"}`。
4. `reset`：出现降级时提供完整替换文本和生成状态。
5. `done`：完整 `AnswerResponse`，此时数据库写入已完成。

检索或会话处理失败会发送 `error` 事件，其 `message` 可展示给用户。流开始后 HTTP 状态码不能再变化，因此客户端必须处理事件内错误，不能只检查 HTTP 200。

流式响应自己管理数据库 session 生命周期，避免 FastAPI 请求依赖在响应体结束前关闭数据库连接。查询优化和同步向量检索放入线程池，模型流及追问改写使用异步 HTTP。

## 启用

本地源码运行前执行：

```bash
.venv/bin/alembic upgrade head
cd frontend
npm run build
```

然后重启后端或使用开发热重载。Docker 需要重新构建并更新 app 镜像；默认入口在启动时执行 `alembic upgrade head`。本次迁移新增两个表，不改动已有内容或向量数据。若使用反向代理，应允许 SSE 长连接且关闭响应缓冲。

已有运行镜像的依赖满足当前项目要求时，可复用依赖层并重新构建前端与后端：

```bash
docker build -f docker/Dockerfile.app-update \
  --build-arg APP_RUNTIME_IMAGE=<现有已验证镜像> \
  -t <新镜像标签> .
```

该构建执行 `pip check`；缺少依赖时构建会失败，此时应使用项目根目录的完整 Dockerfile 构建。

## 验证

新增测试覆盖完整切片与来源分散、上下文预算、模型 SSE、空响应与截断、部分输出降级替换、生成状态持久化、多轮追问、停止与断流清理、会话冲突，以及迁移升级/回退。模型调用使用模拟响应，持久化测试使用独立的 `intelligence_rag_test` 数据库。
