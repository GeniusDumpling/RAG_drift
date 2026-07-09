# FlyForum Video RAG Demo Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 用最少代码打通 FlyForum 公网页面视频 URL 抓取、硅基流动 Qwen3-Omni 描述、PostgreSQL 持久化、`BAAI/bge-m3` 向量化、Qdrant 检索和前端展示完整视频证据的 demo 链路。

**Architecture:** 新增一个有严格数量上限的独立 demo 导入脚本，复用现有 `SourceSite`、`CrawlJob`、`CrawlRun`、`RawPage`、`ContentItem` 和 `ContentChunk`，不新增数据库表或迁移。每个视频保存为 `item_type="video_description"` 的 `ContentItem`，`canonical_url` 保存视频 URL，`cleaned_text` 保存完整描述；每个 Qdrant point ID 固定等于 PostgreSQL `ContentChunk.id`，并回写 `vector_point_id/qdrant_point_id`。查询接口继续先由 Qdrant 命中 chunk，再用 chunk ID 回查 PostgreSQL，返回视频 URL、完整描述和匹配片段。

**Tech Stack:** Python 3.11、httpx、SQLAlchemy/PostgreSQL、Qdrant、SiliconFlow OpenAI-compatible API、Qwen3-Omni、`BAAI/bge-m3`、FastAPI、React/Vite。

---

## 0. Demo 边界与最终文件清单

本计划有意选择“独立导入脚本 + 复用现有数据模型”，而不是扩展通用 worker。这样能少改文件、避免迁移，并且仍能完整展示 PostgreSQL 与 Qdrant 的对应关系。

只支持：

- `https://www.flyforum.cn/forum.php` 及同域公开、无需登录的论坛页/帖子页；
- HTML 中直接出现的 `<video src>`、`<source src>`，以及以 `.mp4`、`.webm`、`.m3u8` 结尾的链接；
- 默认最多 3 个页面、1 个视频，页面请求间隔至少 1 秒；
- 直接把公开视频 URL 传给 VLM；某 URL 不被模型接受时记录失败并继续，不下载视频、不调用 FFmpeg；
- API key 只从环境变量读取，日志和 JSON 输出不打印 key、Authorization header 或原始 API 响应。

明确不做：登录、验证码、反爬绕过、JS 播放器逆向、隐藏流解析、分页全站爬取、定时任务、重试队列、新视频表、生产级并发与媒体下载。

**Files:**

- Create: `scripts/ingest_flyforum_video_demo.py`
- Create: `backend/tests/test_flyforum_video_demo.py`
- Modify: `backend/app/core/config.py`
- Modify: `backend/app/services/embeddings.py`
- Modify: `backend/app/services/search.py`
- Modify: `backend/app/schemas/search.py`
- Modify: `backend/app/services/retrieval.py`
- Modify: `backend/tests/test_chunking_and_indexing.py`
- Modify: `backend/tests/test_search_pipeline.py`
- Modify: `frontend/src/api/types.ts`
- Modify: `frontend/src/components/EvidenceCard.tsx`
- Modify: `frontend/src/pages/SearchPage.tsx`
- Modify: `frontend/tests/evidence-card.test.tsx`
- Modify: `README.md`

不创建 Alembic migration，不修改现有表结构。

### 数据对应关系

| PostgreSQL | Qdrant | 用途 |
|---|---|---|
| `content_items.id` | payload `content_item_id` | 找到完整视频描述 |
| `content_items.canonical_url` | payload `video_url` | 返回/播放视频 URL |
| `content_items.cleaned_text` | 不重复保存全文 | PostgreSQL 中的完整描述 |
| `content_chunks.id` | point ID 与 payload `chunk_id` | 一一对应的主关联键 |
| `content_chunks.vector_point_id` | point ID | PostgreSQL 反向记录 Qdrant point |
| `content_chunks.qdrant_point_id` | point ID | 兼容现有 Qdrant 字段 |

## Task 1: 增加 SiliconFlow BGE-M3 embedding，并统一写入/查询模型

**Files:**

- Modify: `backend/app/core/config.py`
- Modify: `backend/app/services/embeddings.py`
- Modify: `backend/app/services/search.py`
- Modify: `backend/tests/test_chunking_and_indexing.py`

- [ ] **Step 1: 先写失败测试，固定 SiliconFlow 请求和返回契约**

在 `backend/tests/test_chunking_and_indexing.py` 增加：

```python
import json

import httpx

from app.core.config import Settings
from app.services.embeddings import SiliconFlowEmbeddingService, build_embedding_service


def test_siliconflow_embedding_uses_bge_m3_contract() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v1/embeddings"
        assert request.headers["Authorization"] == "Bearer test-key"
        assert json.loads(request.content) == {
            "model": "BAAI/bge-m3",
            "input": "无人机低空飞行",
        }
        return httpx.Response(200, json={"data": [{"embedding": [0.1, 0.2, 0.3]}]})

    service = SiliconFlowEmbeddingService(
        api_key="test-key",
        base_url="https://api.siliconflow.cn/v1",
        model="BAAI/bge-m3",
        dimension=3,
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    assert service.embed("无人机低空飞行") == [0.1, 0.2, 0.3]


def test_embedding_factory_keeps_fake_mode_for_tests() -> None:
    service = build_embedding_service(Settings(EMBEDDING_PROVIDER="fake"))
    assert service.model_name == "deterministic-hash-v1"
```

- [ ] **Step 2: 运行测试并确认失败**

Run: `pytest backend/tests/test_chunking_and_indexing.py -q`

Expected: FAIL，提示 `SiliconFlowEmbeddingService` 或 `build_embedding_service` 不存在。

- [ ] **Step 3: 增加最小配置和 embedding 实现**

在 `Settings` 中增加这些字段，值只从环境变量读取：

```python
embedding_provider: str = Field(default="fake", alias="EMBEDDING_PROVIDER")
siliconflow_api_key: str | None = Field(default=None, alias="SILICONFLOW_API_KEY")
siliconflow_base_url: str = Field(
    default="https://api.siliconflow.cn/v1", alias="SILICONFLOW_BASE_URL"
)
embedding_model: str = Field(default="BAAI/bge-m3", alias="EMBEDDING_MODEL")
embedding_dimension: int = Field(default=1024, alias="EMBEDDING_DIMENSION")
vlm_model: str = Field(
    default="Qwen/Qwen3-Omni-30B-A3B-Instruct", alias="VLM_MODEL"
)
```

在 `backend/app/services/embeddings.py` 保留原 deterministic 类，新增同步实现与工厂：

```python
import httpx

from app.core.config import Settings


class SiliconFlowEmbeddingService:
    def __init__(
        self,
        *,
        api_key: str,
        base_url: str,
        model: str,
        dimension: int,
        client: httpx.Client | None = None,
    ) -> None:
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.model_name = model
        self.dimension = dimension
        self.client = client or httpx.Client(timeout=60)

    def embed(self, text: str) -> list[float]:
        response = self.client.post(
            f"{self.base_url}/embeddings",
            headers={"Authorization": f"Bearer {self.api_key}"},
            json={"model": self.model_name, "input": text},
        )
        response.raise_for_status()
        vector = response.json()["data"][0]["embedding"]
        if len(vector) != self.dimension:
            raise ValueError(
                f"embedding dimension mismatch: expected {self.dimension}, got {len(vector)}"
            )
        return [float(value) for value in vector]


def build_embedding_service(settings: Settings) -> EmbeddingService:
    if settings.embedding_provider == "fake":
        return DeterministicEmbeddingService()
    if settings.embedding_provider != "siliconflow":
        raise ValueError(f"unsupported embedding provider: {settings.embedding_provider}")
    if not settings.siliconflow_api_key:
        raise RuntimeError("SILICONFLOW_API_KEY is required")
    return SiliconFlowEmbeddingService(
        api_key=settings.siliconflow_api_key,
        base_url=settings.siliconflow_base_url,
        model=settings.embedding_model,
        dimension=settings.embedding_dimension,
    )
```

在 `SearchService.__init__` 中把默认 embedding 改为：

```python
self.embedding = embedding or build_embedding_service(self.settings)
```

Task 2 的导入脚本也必须调用同一个 `build_embedding_service(settings)`；不要再复制一份 embedding HTTP client。这样写入和查询使用同一模型，同时不改通用 worker。

- [ ] **Step 4: 运行测试**

Run: `pytest backend/tests/test_chunking_and_indexing.py backend/tests/test_search_pipeline.py -q`

Expected: PASS；测试环境默认 `EMBEDDING_PROVIDER=fake`，不发真实网络请求。

- [ ] **Step 5: 提交**

```bash
git add backend/app/core/config.py backend/app/services/embeddings.py backend/app/services/search.py backend/tests/test_chunking_and_indexing.py
git commit -m "feat: add siliconflow bge embedding provider"
```

## Task 2: 新增单文件 FlyForum 视频导入脚本

**Files:**

- Create: `scripts/ingest_flyforum_video_demo.py`
- Create: `backend/tests/test_flyforum_video_demo.py`

- [ ] **Step 1: 写纯函数测试，固定 URL 发现、去重和 VLM 请求**

测试必须通过 `importlib.util.spec_from_file_location` 导入脚本，避免把 `scripts` 改成 package。核心断言：

```python
def test_extract_links_and_video_urls_resolves_and_deduplicates() -> None:
    html = """
    <a href="thread-1-1-1.html">帖子</a>
    <video src="/media/demo.mp4"></video>
    <video><source src="https://cdn.flyforum.cn/demo.webm#t=1"></video>
    <a href="/media/demo.mp4">重复视频</a>
    """
    result = demo.extract_page("https://www.flyforum.cn/forum.php", html)
    assert result.page_urls == ["https://www.flyforum.cn/thread-1-1-1.html"]
    assert result.video_urls == [
        "https://www.flyforum.cn/media/demo.mp4",
        "https://cdn.flyforum.cn/demo.webm",
    ]


def test_describe_video_sends_qwen_omni_video_url() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        assert payload["model"] == "Qwen/Qwen3-Omni-30B-A3B-Instruct"
        assert payload["messages"][0]["content"][1] == {
            "type": "video_url",
            "video_url": {"url": "https://cdn.flyforum.cn/demo.mp4"},
        }
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": "视频展示无人机缓慢起飞。"}}]},
        )

    description = demo.describe_video(
        client=httpx.Client(transport=httpx.MockTransport(handler)),
        base_url="https://api.siliconflow.cn/v1",
        api_key="test-key",
        model="Qwen/Qwen3-Omni-30B-A3B-Instruct",
        video_url="https://cdn.flyforum.cn/demo.mp4",
    )
    assert description == "视频展示无人机缓慢起飞。"
```

再用 fake SQLAlchemy session 和 fake indexer 验证持久化对象的关键关系：

```python
assert content.item_type == "video_description"
assert content.canonical_url == video_url
assert content.cleaned_text == description
assert content.metadata_json["source_page_url"] == source_page_url
assert chunk.vector_point_id == str(chunk.id)
assert chunk.qdrant_point_id == str(chunk.id)
assert indexed_payload["content_item_id"] == str(content.id)
assert indexed_payload["video_url"] == video_url
```

- [ ] **Step 2: 运行测试并确认失败**

Run: `pytest backend/tests/test_flyforum_video_demo.py -q`

Expected: FAIL，脚本文件或目标函数尚不存在。

- [ ] **Step 3: 实现最小导入脚本**

脚本使用 `html.parser.HTMLParser`，不新增 BeautifulSoup 依赖。公开接口固定为以下三个函数；函数体按本任务后续列出的抓取、VLM 和持久化规则实现：

```python
@dataclass(frozen=True)
class ExtractedPage:
    page_urls: list[str]
    video_urls: list[str]


def extract_page(page_url: str, html: str) -> ExtractedPage:
    """返回同域帖子/论坛页 URL 和页面中公开的直链视频 URL。"""


def describe_video(
    *, client: httpx.Client, base_url: str, api_key: str, model: str, video_url: str
) -> str:
    """调用 SiliconFlow chat completions 并返回非空中文描述。"""


def ingest(args: argparse.Namespace) -> dict[str, object]:
    """执行一次有界 demo run，并返回不含凭据的计数和记录 ID。"""
```

CLI 参数只保留 demo 必需项：

```python
parser.add_argument("--start-url", default="https://www.flyforum.cn/forum.php")
parser.add_argument("--page-limit", type=int, default=3)
parser.add_argument("--video-limit", type=int, default=1)
parser.add_argument("--request-delay", type=float, default=1.0)
parser.add_argument("--json", action="store_true")
```

抓取规则：

```python
ALLOWED_PAGE_HOST = "www.flyforum.cn"
VIDEO_SUFFIXES = (".mp4", ".webm", ".m3u8")


def is_allowed_page_url(url: str) -> bool:
    parsed = urlparse(url)
    return (
        parsed.scheme == "https"
        and parsed.hostname == ALLOWED_PAGE_HOST
        and (
            parsed.path.endswith("forum.php")
            or "forum-" in parsed.path
            or "thread-" in parsed.path
            or parse_qs(parsed.query).get("mod") == ["viewthread"]
        )
    )
```

VLM 请求只发送中文描述提示和 `video_url`，并校验返回非空：

```python
payload = {
    "model": model,
    "messages": [
        {
            "role": "user",
            "content": [
                {
                    "type": "text",
                    "text": "请用中文描述视频中的场景、主体、动作和关键细节，控制在300至800字。",
                },
                {"type": "video_url", "video_url": {"url": video_url}},
            ],
        }
    ],
}
```

持久化流程必须按以下顺序执行：

1. 创建或复用 demo-owned `SourceSite` 和 `CrawlJob`；
2. 创建 `CrawlRun(status="running")`；
3. 每抓取一页先保存一个 `RawPage(raw_html=page_html)`；
4. 对每个新视频 URL 调用一次 VLM；
5. 创建 `ContentItem(item_type="video_description")`，其中 `canonical_url=video_url`、`source_url=source_page_url`、`cleaned_text=description`、`metadata_json={"video_url": video_url, "source_page_url": source_page_url, "vlm_model": settings.vlm_model}`；
6. 调用现有 `build_chunks(item_type="video_description", title=title, cleaned_text=description, summary_text=description[:240], tags=["flyforum", "video"], thread_title=None)` 创建 chunks，先提交 PostgreSQL；
7. 用 `QdrantIndexer` 和 Task 1 的 `build_embedding_service(settings)` 写 Qdrant；
8. point ID 使用 `str(chunk.id)`，payload 至少含 `chunk_id`、`content_item_id`、`source_site_id`、`item_type`、`video_url`、`source_page_url`；
9. Qdrant 成功后回写 `vector_backend="qdrant"`、`vector_point_id`、`qdrant_point_id`、`embed_status="success"`；失败则 `embed_status="failed"`，run 标记 `partial`；
10. JSON 输出只包含 IDs、计数、status 和裁剪后的错误类型，不输出环境变量或 API 响应正文。

幂等键使用稳定的视频 URL，而不是描述内容：

```python
dedup_key = stable_hash(f"{source.id}:video_description:{normalized_video_url}")
```

如果该键已存在，本次 run 计入 `deduped_count` 并跳过 VLM，避免重复付费。

- [ ] **Step 4: 运行脚本单测**

Run: `pytest backend/tests/test_flyforum_video_demo.py -q`

Expected: PASS，且测试无外网访问。

- [ ] **Step 5: 提交**

```bash
git add scripts/ingest_flyforum_video_demo.py backend/tests/test_flyforum_video_demo.py
git commit -m "feat: add bounded flyforum video demo importer"
```

## Task 3: 让检索结果显式返回视频 URL 和完整描述

**Files:**

- Modify: `backend/app/schemas/search.py`
- Modify: `backend/app/services/retrieval.py`
- Modify: `backend/tests/test_search_pipeline.py`

- [ ] **Step 1: 写失败测试**

在检索测试中创建 `item_type="video_description"` 的内容并断言：

```python
assert evidence[0]["video_url"] == "https://cdn.flyforum.cn/demo.mp4"
assert evidence[0]["description_text"] == "完整的 VLM 视频描述文本。"
assert evidence[0]["canonical_url"] == evidence[0]["video_url"]
assert evidence[0]["snippet"]
```

- [ ] **Step 2: 运行测试并确认失败**

Run: `pytest backend/tests/test_search_pipeline.py -q`

Expected: FAIL，响应缺少 `video_url` 和 `description_text`。

- [ ] **Step 3: 最小扩展 EvidenceObject 和 hydration**

在 `EvidenceObject` 增加两个可空字段，保证旧内容兼容：

```python
video_url: str | None = None
description_text: str | None = None
```

在 `_hydrate_and_rank_evidence()` 现有 `EvidenceObject(...)` 构造调用中增加下面两个关键字参数，只对视频类型赋值：

```python
is_video = content_item.item_type == "video_description"
EvidenceObject(
    video_url=content_item.canonical_url if is_video else None,
    description_text=content_item.cleaned_text if is_video else None,
)
```

Qdrant 只决定命中的 `chunk_id` 和分数；完整描述始终从 PostgreSQL authoritative row 读取，不信任 Qdrant payload 中的全文。

- [ ] **Step 4: 运行后端检索测试**

Run: `pytest backend/tests/test_search_pipeline.py backend/tests/test_answer_pipeline.py -q`

Expected: PASS，旧 Evidence contract 不回归，视频 Evidence 多出两个字段。

- [ ] **Step 5: 提交**

```bash
git add backend/app/schemas/search.py backend/app/services/retrieval.py backend/tests/test_search_pipeline.py
git commit -m "feat: return video evidence from search"
```

## Task 4: 前端在现有 Search 页面展示视频与完整描述

**Files:**

- Modify: `frontend/src/api/types.ts`
- Modify: `frontend/src/components/EvidenceCard.tsx`
- Modify: `frontend/src/pages/SearchPage.tsx`
- Modify: `frontend/tests/evidence-card.test.tsx`

- [ ] **Step 1: 写失败组件测试**

```tsx
it('renders video evidence with controls and full description', () => {
  render(
    <EvidenceCard
      evidence={{
        ...evidence,
        item_type: 'video_description',
        canonical_url: 'https://cdn.flyforum.cn/demo.mp4',
        video_url: 'https://cdn.flyforum.cn/demo.mp4',
        description_text: '完整的无人机视频描述。',
      }}
    />,
  );

  expect(screen.getByText('完整的无人机视频描述。')).toBeInTheDocument();
  expect(screen.getByTestId('video-evidence')).toHaveAttribute(
    'src',
    'https://cdn.flyforum.cn/demo.mp4',
  );
});
```

- [ ] **Step 2: 运行测试并确认失败**

Run: `npm test -- --run tests/evidence-card.test.tsx`

Workdir: `frontend`

Expected: FAIL，类型或视频元素尚不存在。

- [ ] **Step 3: 增加两个可空类型字段并最小扩展 EvidenceCard**

在 `EvidenceObject` 增加：

```typescript
video_url: string | null;
description_text: string | null;
```

在 `EvidenceCard` 中复用 `safeExternalHref`；仅对安全的 HTTP(S) 视频 URL 渲染播放器：

```tsx
const videoHref = evidence.video_url ? safeExternalHref(evidence.video_url) : null;

{videoHref ? (
  <video controls preload="metadata" src={videoHref} data-testid="video-evidence">
    当前浏览器不支持视频播放。
  </video>
) : null}
{evidence.description_text ? (
  <p className="video-description">{evidence.description_text}</p>
) : null}
```

在 `SearchPage` 的 item type 下拉框增加：

```tsx
<option value="video_description">video_description</option>
```

不新增 Videos 页面、不新增路由、不增加新 API client 方法。

- [ ] **Step 4: 运行前端测试**

Run: `npm test -- --run`

Workdir: `frontend`

Expected: PASS。

- [ ] **Step 5: 提交**

```bash
git add frontend/src/api/types.ts frontend/src/components/EvidenceCard.tsx frontend/src/pages/SearchPage.tsx frontend/tests/evidence-card.test.tsx
git commit -m "feat: show video evidence in search"
```

## Task 5: 文档与本地端到端验证

**Files:**

- Modify: `README.md`

- [ ] **Step 1: 在 README 增加不含密钥值的 demo 命令**

```powershell
$env:EMBEDDING_PROVIDER='siliconflow'
$env:SILICONFLOW_API_KEY='<set-locally>'
$env:SILICONFLOW_BASE_URL='https://api.siliconflow.cn/v1'
$env:EMBEDDING_MODEL='BAAI/bge-m3'
$env:EMBEDDING_DIMENSION='1024'
$env:VLM_MODEL='Qwen/Qwen3-Omni-30B-A3B-Instruct'
$env:QDRANT_COLLECTION='flyforum_video_bge_m3'

docker compose up -d postgres qdrant
alembic upgrade head
python scripts/ingest_flyforum_video_demo.py --page-limit 3 --video-limit 1 --json
```

README 同时说明：公开页面没有直接视频 URL 时，脚本可能成功结束但 `video_discovered_count=0`；这表示当前采样页面没有满足 demo 规则的直链，不代表 PostgreSQL/Qdrant 故障。不要为了“找出视频”绕过登录、验证码或站点限制。

- [ ] **Step 2: 运行静态与单元测试**

Run:

```powershell
ruff check backend worker scripts
pytest -q
Set-Location frontend
npm test -- --run
npm run build
```

Expected: 全部 PASS；前端 build 成功。

- [ ] **Step 3: 在有本地密钥的环境执行一次最小 live 导入**

Run:

```powershell
python scripts/ingest_flyforum_video_demo.py --page-limit 3 --video-limit 1 --request-delay 1 --json
```

Expected: 输出只包含 `source_id`、`job_id`、`run_id`、`status` 和计数；不得出现 API key、Authorization header、完整请求/响应日志。

- [ ] **Step 4: 核对 PostgreSQL 与 Qdrant 一一对应**

```powershell
@'
select
  ci.id as content_item_id,
  ci.canonical_url as video_url,
  cc.id as chunk_id,
  cc.vector_point_id,
  cc.embed_status
from content_items ci
join content_chunks cc on cc.content_item_id = ci.id
where ci.item_type = 'video_description'
order by ci.created_at desc, cc.chunk_index;
'@ | psql $env:SYNC_DATABASE_URL
```

Expected: 每一行 `chunk_id::text = vector_point_id` 且 `embed_status=success`。再打开 Qdrant dashboard `http://localhost:6333/dashboard`，确认同 ID point 的 payload 含 `content_item_id` 和 `video_url`。

- [ ] **Step 5: 验证问答返回视频证据**

```powershell
$body = @{
  query = '无人机起飞过程和周围环境'
  mode = 'search'
  filters = @{ item_type = 'video_description' }
  top_k = 3
} | ConvertTo-Json -Depth 4
Invoke-RestMethod -Method Post -Uri 'http://127.0.0.1:8000/search' -ContentType 'application/json' -Body $body
```

Expected: 至少一个命中时，Evidence 同时含 `video_url`、`description_text`、`snippet`、`chunk_id` 和相似度 `score`；前端 Search 页面显示播放器、完整描述和匹配分数。

- [ ] **Step 6: 提交**

```bash
git add README.md
git commit -m "docs: add flyforum video rag demo steps"
```

## 最终验收清单

- [ ] 仅访问 FlyForum 公开、无需登录的页面，未绕过验证码或反爬限制。
- [ ] 默认最多抓 3 页、分析 1 个视频，请求间隔不少于 1 秒。
- [ ] 视频 URL 与完整描述存在 PostgreSQL `content_items`。
- [ ] 描述 chunks 存在 PostgreSQL `content_chunks` 和 Qdrant。
- [ ] PostgreSQL `content_chunks.id/vector_point_id` 与 Qdrant point ID 一致。
- [ ] 写入和查询都使用 SiliconFlow `BAAI/bge-m3`，没有混用 deterministic vector。
- [ ] `/search` 返回 PostgreSQL 中的视频 URL、完整描述以及 Qdrant 命中的 chunk/score。
- [ ] 前端现有 Search 页面能播放安全 URL，并展示完整描述和相似度。
- [ ] 无新表、无 migration、无新页面、无通用爬虫框架、无 FFmpeg。
- [ ] 测试日志、run event 和脚本 JSON 输出均不含任何密钥。
