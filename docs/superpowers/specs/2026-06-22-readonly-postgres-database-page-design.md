# 只读 PostgreSQL 展示页与 psql 使用手册设计

## 背景

当前 `intelligence-rag` 原型已经接入 PostgreSQL 和 Qdrant：PostgreSQL 保存 source、run、raw page、content item、content chunk、search query 等事实数据；Qdrant 保存 chunk 向量索引。现有前端已经提供总览、数据源、运行记录、检索问答和内容详情，但缺少一个面向演示的数据库可视化入口，也缺少面向技术观众的 psql 命令手册。

本设计新增一个只读 `Database / 数据库` 页面，用于展示 PostgreSQL 接入状态、核心表内容、PostgreSQL ↔ Qdrant 对账状态，并提供可复制的 psql/curl 命令手册。

## 目标

1. 在项目前端提供一个只读数据库展示页，便于演示“数据确实落在 PostgreSQL”。
2. 可视化核心表计数、最新 run/search、chunk 向量化状态。
3. 展示 PostgreSQL 中 `content_chunks.vector_backend = 'qdrant'` 的数量与 Qdrant collection `points_count` 的对账结果。
4. 提供核心白名单表的只读浏览能力，支持分页和简单关键词搜索。
5. 在前端提供 psql 使用手册，便于技术同学复制命令在终端中验证。
6. 不暴露数据库密码，不提供任意 SQL 执行，不提供写入/删除/修改能力。

## 非目标

- 不做 pgAdmin 替代品。
- 不提供任意 SQL 控制台。
- 不支持 DDL、DML、备份、恢复、用户权限管理等数据库管理操作。
- 不暴露完整数据库连接串、密码、token、cookie 或其他敏感值。
- 不把 Qdrant 当作事实数据源；Qdrant 只展示索引状态和 point payload 对账摘要。

## 推荐方案

采用“演示总览 + 核心表浏览 + psql 手册”的单页布局：

```text
Database 页面
├── 顶部状态卡片
│   ├── PostgreSQL 连接状态
│   ├── 核心表总览
│   ├── 最新采集 run
│   └── 最新搜索 query
├── PostgreSQL ↔ Qdrant 对账区
│   ├── PostgreSQL qdrant chunk 数
│   ├── Qdrant points_count
│   ├── collection、vector size、distance
│   └── 对账状态：一致 / 不一致 / 未知
├── 核心表只读浏览器
│   ├── 表选择：source_sites、crawl_jobs、crawl_runs、crawl_run_events、raw_pages、content_items、content_chunks、search_queries
│   ├── 分页：limit、offset
│   ├── 简单关键词搜索：q
│   └── 行详情：字段预览、JSON 折叠、长文本截断
└── psql / Qdrant 命令手册
    ├── 连接 PostgreSQL
    ├── 查看表与结构
    ├── 常用 SQL 查询
    ├── 查看 Qdrant 健康和 collection
    └── 注意事项
```

这个方案比直接部署 pgAdmin 更适合公开演示：它是只读、业务语义明确、不会暴露数据库凭据，也能解释 PostgreSQL 和 Qdrant 在系统中的边界。

## 后端设计

### 新增模块

- `backend/app/api/database.py`
- `backend/app/repositories/database.py`
- `backend/app/schemas/database.py`
- 在 `backend/app/api/router.py` 中注册 `database.router`

### API 1：数据库总览

```http
GET /database/overview
```

返回内容：

```json
{
  "postgres": {
    "status": "ok",
    "database_name": "intelligence_rag",
    "server_version": "PostgreSQL ...",
    "checked_at": "2026-06-22T...Z",
    "table_counts": [
      { "table_name": "source_sites", "count": 3 },
      { "table_name": "crawl_runs", "count": 11 }
    ],
    "latest_run": {
      "id": "...",
      "status": "success",
      "created_at": "...",
      "parsed_count": 61,
      "embedded_count": 67
    },
    "latest_search": {
      "id": "...",
      "raw_query": "...",
      "result_count": 3,
      "created_at": "..."
    },
    "chunk_vector_status": [
      { "embed_status": "success", "vector_backend": "qdrant", "count": 67 }
    ]
  },
  "qdrant": {
    "status": "ok",
    "collection": "content_chunks_v1",
    "points_count": 67,
    "vector_size": 384,
    "distance": "Cosine",
    "error": null
  },
  "reconciliation": {
    "postgres_qdrant_chunk_count": 67,
    "qdrant_points_count": 67,
    "status": "matched"
  }
}
```

错误处理：

- PostgreSQL 查询失败时，接口返回 500；因为 API 本身依赖 PostgreSQL，不能隐藏事实库故障。
- Qdrant 查询失败时，接口仍返回 PostgreSQL 总览，但 `qdrant.status = "unavailable"`，`reconciliation.status = "unknown"`，并返回脱敏错误摘要。

### API 2：白名单表元数据

```http
GET /database/tables
```

返回允许浏览的表：

- `source_sites`
- `crawl_jobs`
- `crawl_runs`
- `crawl_run_events`
- `raw_pages`
- `content_items`
- `content_chunks`
- `search_queries`

每个表返回：

```json
{
  "table_name": "content_items",
  "label": "内容项 content_items",
  "description": "归一化后的 thread/comment/article 等展示与引用单位",
  "default_sort": "created_at desc",
  "preview_columns": ["id", "item_type", "title", "canonical_url", "published_at", "created_at"],
  "searchable_columns": ["title", "canonical_url", "cleaned_text"]
}
```

### API 3：白名单表行浏览

```http
GET /database/tables/{table_name}/rows?limit=50&offset=0&q=...
```

约束：

- `table_name` 必须命中后端硬编码白名单。
- `limit` 范围为 `1..100`。
- `offset >= 0`。
- `q` 仅应用到该表声明的 `searchable_columns`，使用参数化 SQLAlchemy 条件，不拼接原始 SQL。
- 只返回后端声明的 preview/detail 字段，不返回任意列。
- 长文本和大 JSON 默认截断为 preview，行详情中可展示结构化摘要。

返回：

```json
{
  "items": [
    {
      "id": "...",
      "table_name": "content_chunks",
      "preview": {
        "id": "...",
        "content_item_id": "...",
        "embed_status": "success",
        "vector_backend": "qdrant"
      },
      "detail": {
        "embed_text_preview": "Thread: ...",
        "chunk_metadata_json": { "chunker_version": "..." }
      }
    }
  ],
  "total": 67,
  "limit": 50,
  "offset": 0
}
```

### API 4：单行详情

```http
GET /database/tables/{table_name}/rows/{row_id}
```

用于点击行后展开详情，是本轮范围内需要实现的只读能力。仍然只允许白名单表，且只返回该表声明的安全字段。对 `content_items` 和 `content_chunks` 可以附带少量关系摘要，例如：

- `content_items`：关联 source、crawl_run、raw_page id。
- `content_chunks`：关联 content item title、qdrant point id、vector backend。
- `raw_pages`：展示 URL、状态码、fetched_at、raw_json/raw_text 预览。

## 前端设计

### 新增页面和导航

- 新增 `frontend/src/pages/DatabasePage.tsx`
- 在 `frontend/src/App.tsx` 中新增视图：`Database`
- 导航文案：`数据库 Database`

### 页面结构

1. **状态卡片区**
   - PostgreSQL：在线/异常、库名、核心表数量。
   - Qdrant：在线/异常、collection、points_count、vector size。
   - 对账：matched/mismatch/unknown。
   - 最新活动：最新 run、最新 search。

2. **核心表浏览区**
   - 表选择控件。
   - 搜索框。
   - 分页按钮。
   - 只读表格。
   - 行详情面板：展示字段、JSON、长文本 preview。

3. **psql 使用手册区**
   - 命令分组：连接、查看表、常用查询、Qdrant curl。
   - 每条命令用 `<pre>` 展示，并提供“复制”按钮。
   - 明确提示：命令会提示输入密码；前端不会展示密码。

### psql 手册默认命令

页面内置以下命令组：

```bash
pg_isready -h 127.0.0.1 -p 54329 -d intelligence_rag
```

```bash
psql -h 127.0.0.1 -p 54329 -U intelligence -d intelligence_rag
```

```sql
\dt
\d content_items
\x auto
\q
```

```sql
select 'source_sites' as table_name, count(*) from source_sites
union all select 'crawl_runs', count(*) from crawl_runs
union all select 'raw_pages', count(*) from raw_pages
union all select 'content_items', count(*) from content_items
union all select 'content_chunks', count(*) from content_chunks
union all select 'search_queries', count(*) from search_queries;
```

```sql
select embed_status, vector_backend, count(*) as chunk_count
from content_chunks
group by 1, 2
order by 1, 2;
```

```bash
curl -fsS http://127.0.0.1:6333/healthz
curl -fsS http://127.0.0.1:6333/collections/content_chunks_v1 | python3 -m json.tool
```

## 安全与权限

- 所有新 API 均为只读。
- 不提供任意 SQL 执行接口。
- 不返回数据库密码、完整连接串或环境变量。
- 表访问基于后端硬编码白名单，不接受任意表名。
- 搜索条件使用 SQLAlchemy 参数化表达式，不拼接用户输入。
- 大字段默认截断，避免前端渲染过量 raw payload。
- Qdrant 错误消息需要脱敏，只保留服务不可用、连接失败、collection 不存在等必要信息。

## 数据流

```text
DatabasePage
  -> GET /database/overview
      -> PostgreSQL: table counts, latest run/search, chunk vector status
      -> Qdrant HTTP API: collection status
      -> reconciliation summary

DatabasePage
  -> GET /database/tables
      -> backend whitelist metadata

DatabasePage
  -> GET /database/tables/{table}/rows
      -> PostgreSQL read-only queries
      -> frontend read-only table and detail panel
```

## 测试计划

### 后端测试

- `GET /database/overview` 正常返回 PostgreSQL 表计数和 Qdrant 对账信息。
- Qdrant 不可用时，overview 仍返回 PostgreSQL 信息，并把 Qdrant 标记为 unavailable。
- `GET /database/tables` 只返回白名单表。
- 非白名单表访问返回 404。
- `limit > 100` 返回 422。
- `q` 搜索只作用于声明的 searchable columns。
- API 不返回敏感配置字段。

### 前端测试

- `DatabasePage` 能渲染状态卡片、对账状态、表浏览器和 psql 手册。
- 表切换会触发行列表加载。
- 搜索框会传递 `q` 参数。
- 复制按钮在支持 `navigator.clipboard` 时调用复制接口；不支持时给出提示。
- Qdrant unavailable 时显示黄色/灰色告警，而不是页面崩溃。

### 手动验收

- 打开公网 demo 首页，导航中可见 `数据库 Database`。
- Database 页面能显示 PostgreSQL 在线和核心表行数。
- Qdrant 对账显示 matched，points_count 与 PostgreSQL qdrant chunk 数一致。
- 能浏览 `content_items`、`content_chunks` 和 `search_queries`。
- psql 手册命令可复制，并能在服务器终端执行。

## 实施顺序建议

1. 后端新增 database schemas/repository/API，并补充测试。
2. 前端新增 API 类型和 client 方法。
3. 新增 `DatabasePage`，接入导航。
4. 增加 psql 手册组件和复制按钮。
5. 运行后端测试、前端测试和前端 build。
6. 本地/公网 demo 手动验收。

## 风险与应对

- **风险：页面被误解为数据库管理后台。** 页面文案明确“只读展示，不支持 SQL 执行”。
- **风险：大 JSON/raw text 影响性能。** 默认截断，详情中也限制 preview 长度。
- **风险：Qdrant 暂时不可用导致演示失败。** overview 将 Qdrant 状态标记为 unavailable，但 PostgreSQL 信息仍可展示。
- **风险：未来表结构变化导致浏览器字段不一致。** 表元数据集中维护在 repository/schema 层，新增表或字段需要显式加入白名单。

## 开放问题

当前设计没有未决问题。默认采用推荐布局 A：演示总览 + 核心表浏览 + psql 使用手册。