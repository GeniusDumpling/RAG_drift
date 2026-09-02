#!/usr/bin/env python3
"""把 Discourse 论坛按关键词搜索命中的帖子导入 RAG。

流程：按关键词调用各论坛 `/search.json` 发现候选 → 查重 →
    对每个候选调用 `/posts/{id}.json` 取全文(raw) → 落四层存储
    （Source/Job/Run + RawPage + Author + ContentItem + ContentChunk）。

设计对齐 `ingest_dji_lito.py`：
- 原始 API JSON 保留在 `raw_pages.raw_json`；
- 每个命中帖子作为 `item_type="post"` 独立入库；
- 单个帖子失败不影响其余候选（每个候选在独立 try/except 中处理）；
- 去重键 = {source_id}:{canonical_url}:post:{content_hash}。

用法示例（仓库根目录）：

    python3 skills/forum-crawler/scripts/discourse_ingest.py \
        --query "GPS spoofing jamming" \
        --forums ardupilot px4 \
        --max-results 10 \
        --request-delay 0.3 \
        --json
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "backend"))
sys.path.insert(0, str(ROOT))

from sqlalchemy import create_engine, select, text  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

from app.core.config import get_settings  # noqa: E402
from app.db.base import utcnow  # noqa: E402
from app.models.content import Author, ContentChunk, ContentItem, RawPage  # noqa: E402
from app.models.control import CrawlJob, CrawlRun, SourceSite  # noqa: E402
from app.repositories.contents import stable_hash  # noqa: E402
from app.services.chunking import build_chunks  # noqa: E402

# 复用 DJI ingest 的通用工具（同目录，均为纯函数）。
# 以顶层模块运行时相对导入不可用，故用普通导入。
from ingest_dji_lito import add_event, compact_json, existing_item

USER_AGENT = "Mozilla/5.0 (compatible; IntelligenceRAGDiscourseIngest/0.1)"

# 论坛标识 → 根地址。dronecode 已合并进 px4。
FORUMS: dict[str, str] = {
    "ardupilot": "https://discuss.ardupilot.org",
    "px4": "https://discuss.px4.io",
    "dronecode": "https://discuss.px4.io",  # 301 → PX4
}


class DiscourseError(RuntimeError):
    """触发失败或网络错误。"""


def fetch_json(url: str) -> dict[str, Any]:
    request = Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
    try:
        with urlopen(request, timeout=30) as response:
            return json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        raise DiscourseError(f"HTTP {exc.code}") from exc
    except URLError as exc:
        raise DiscourseError(f"网络错误：{exc.reason}") from exc


def iso_to_dt(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).astimezone(UTC)
    except (ValueError, TypeError):
        return None


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--query", required=True, help="Discourse 搜索词（如 GPS spoofing / MAVLink security）")
    parser.add_argument(
        "--forums",
        nargs="+",
        choices=tuple(FORUMS),
        default=list(FORUMS),
        help="要采集的论坛标识，空格分隔，默认 all（ardupilot px4）",
    )
    parser.add_argument("--max-results", type=int, default=10, help="每个论坛最多入库的帖子数，默认 10")
    parser.add_argument("--category-id", type=int, default=None, help="限定搜索板块 category_id，可选")
    parser.add_argument("--request-delay", type=float, default=0.3, help="每次 API 请求间隔，默认 0.3s")
    parser.add_argument("--language", default="en", help="帖子默认语言，默认 en")
    parser.add_argument("--json", action="store_true", help="输出 JSON（默认）")
    return parser.parse_args(argv)


def get_source_and_job(session: Session, *, forum: str, base: str, query: str, language: str) -> tuple[SourceSite, CrawlJob]:
    host = base.removeprefix("https://").removeprefix("http://").split("/")[0]
    source_name = f"Discourse: {forum} ({host})"
    source = session.scalar(select(SourceSite).where(SourceSite.name == source_name).limit(1))
    if source is None:
        source = SourceSite(
            name=source_name,
            site_type="forum",
            base_url=base,
            allowed_domains=[host],
            fetch_mode="http_api",
            default_language=language,
            active=True,
            config_json={"kind": "discourse", "forum": forum, "base": base, "api_base": base},
        )
        session.add(source)
        session.flush()

    job_name = f"Discourse {forum} 关键词检索采集"
    job = session.scalar(select(CrawlJob).where(CrawlJob.source_site_id == source.id, CrawlJob.name == job_name).limit(1))
    if job is None:
        job = CrawlJob(
            source_site_id=source.id,
            name=job_name,
            trigger_mode="manual",
            cron_expr=None,
            seed_config_json={"query": query, "max_results": None, "category_id": None, "base": base},
            parser_profile="discourse_api",
            max_pages=None,
            enabled=True,
            agent_policy_json={"extraction_mode": "deterministic_api_mapping"},
            last_run_at=None,
            next_run_at=None,
        )
        session.add(job)
        session.flush()
    return source, job


def get_author(session: Session, source_id: Any, username: str, base: str) -> Author:
    external_id = username or "未知用户"
    author = session.scalar(
        select(Author).where(Author.source_site_id == source_id, Author.external_author_id == external_id).limit(1)
    )
    if author is not None:
        return author
    author = Author(
        source_site_id=source_id,
        display_name=username or "未知用户",
        handle=username or None,
        profile_url=f"{base}/u/{quote(username)}" if username else None,
        external_author_id=external_id,
        raw_json={"username": username},
    )
    session.add(author)
    session.flush()
    return author


def create_discourse_post(
    session: Session,
    *,
    source: SourceSite,
    run: CrawlRun,
    post_payload: dict[str, Any],
    candidate: dict[str, Any],
    base: str,
    query: str,
    language: str,
) -> tuple[ContentItem | None, RawPage | None, int]:
    post_id = int(post_payload.get("id") or candidate["post_id"])
    username = candidate["username"]
    title = candidate["topic_title"]
    canonical_url = f"{base}/t/{candidate.get('topic_slug') or candidate['topic_url_slug']}/{candidate['topic_id']}/{candidate['post_number']}".rstrip("/")
    raw = str(post_payload.get("raw") or candidate.get("blurb") or "").strip()
    if not raw:
        raw = f"[该帖子正文为空，原始 JSON 已保留在 Raw page 中。]"

    cleaned = "\n\n".join(
        part for part in [f"主题：{title}", f"作者：{username}", f"内容：{raw}", f"来源：keyword={query} | url={canonical_url}"] if part
    )

    if existing_item(session, source.id, canonical_url, "post", cleaned) is not None:
        return None, None, 0

    body_json = compact_json(post_payload)
    raw_page = RawPage(
        source_site_id=source.id,
        crawl_run_id=run.id,
        requested_url=f"{base}/posts/{post_id}.json",
        final_url=canonical_url,
        http_status=200,
        content_type="application/json",
        response_headers_json={"x-ingest-adapter": "discourse_api"},
        raw_html=body_json,
        raw_text=raw,
        raw_json=post_payload,
        fetched_at=utcnow(),
        fetch_error=None,
        parser_profile="discourse_api",
        extraction_method="deterministic_api_mapping",
        extraction_confidence=1.0,
        parse_status="parsed",
        parse_error=None,
        body_hash=stable_hash(body_json),
    )
    session.add(raw_page)
    session.flush()

    tags = [c.strip() for c in query.replace(",", " ").split() if c.strip()]
    content = ContentItem(
        source_site_id=source.id,
        raw_page_id=raw_page.id,
        crawl_run_id=run.id,
        author_id=get_author(session, source.id, username, base).id,
        parent_item_id=None,
        thread_root_id=None,
        item_type="post",
        title=title,
        canonical_url=canonical_url,
        source_url=f"{base}/posts/{post_id}.json",
        published_at=iso_to_dt(post_payload.get("created_at")),
        language=language,
        raw_text=raw,
        cleaned_text=cleaned,
        summary_text=raw[:240] or None,
        structured_by="discourse_api_importer",
        extraction_confidence=1.0,
        tags=tags or ["discourse"],
        metadata_json={
            "forum": candidate.get("forum"),
            "query": query,
            "post_id": post_id,
            "post_number": candidate.get("post_number"),
            "category_id": candidate.get("category_id"),
            "like_count": post_payload.get("like_count"),
            "base": base,
        },
        content_hash=stable_hash(cleaned),
        dedup_key=stable_hash(f"{source.id}:{canonical_url}:post:{stable_hash(cleaned)}"),
        search_tsv=None,
    )
    session.add(content)
    session.flush()

    chunk_count = 0
    for built in build_chunks(item_type="post", title=title, cleaned_text=cleaned, summary_text=raw[:240] or None, tags=tags, thread_title=title):
        session.add(
            ContentChunk(
                content_item_id=content.id,
                chunk_index=built.chunk_index,
                char_start=built.start_char,
                char_end=built.end_char,
                display_text=built.display_text,
                embed_text=built.embed_text,
                token_count=built.token_count,
                chunk_metadata_json={**built.chunk_metadata_json, "ingest_source": "discourse_api"},
                qdrant_point_id=None,
                vector_backend=None,
                vector_point_id=None,
                embedded_at=None,
                embed_status="pending",
                embed_error=None,
            )
        )
        chunk_count += 1
    session.flush()
    return content, raw_page, chunk_count


def ingest_forum(
    session: Session,
    *,
    forum: str,
    base: str,
    query: str,
    max_results: int,
    category_id: int | None,
    request_delay: float,
    language: str,
) -> dict[str, Any]:
    source, job = get_source_and_job(session, forum=forum, base=base, query=query, language=language)
    run = CrawlRun(
        source_site_id=source.id,
        crawl_job_id=job.id,
        trigger_type="manual",
        execution_mode="deterministic_api_import",
        seed_url=base,
        status="running",
        started_at=utcnow(),
        config_snapshot_json={"query": query, "forums": [forum], "max_results": max_results, "category_id": category_id, "language": language},
    )
    session.add(run)
    session.flush()
    add_event(session, run.id, stage="discover", event_type="run_started", message=f"开始采集 Discourse {forum}：keyword={query}", related_url=base, counters={"query": query, "max_results": max_results}, trace={"adapter": "discourse_api", "base": base})
    session.commit()

    # 1) search.json 发现候选
    params = {"q": query.strip(), "limit": max_results}
    if category_id:
        params["category"] = str(category_id)
    search_url = f"{base}/search.json?q={quote(query.strip())}" + (f"&category={category_id}" if category_id else "")
    search_payload = fetch_json(search_url)
    topics = {(t.get("id") or 0): t for t in (search_payload.get("topics") or []) if isinstance(t, dict)}
    posts = search_payload.get("posts") or []
    candidates: list[dict[str, Any]] = []
    for p in posts[:max_results]:
        if not isinstance(p, dict):
            continue
        topic = topics.get(int(p.get("topic_id") or 0)) or {}
        candidates.append(
            {
                "forum": forum,
                "post_id": int(p.get("id") or 0),
                "topic_id": int(p.get("topic_id") or 0),
                "post_number": int(p.get("post_number") or 1),
                "topic_url_slug": str(topic.get("slug") or ""),
                "topic_title": str(topic.get("title") or "")[:300],
                "username": str(p.get("username") or ""),
                "category_id": int(topic.get("category_id") or p.get("category_id") or 0),
                "created_at": str(p.get("created_at") or ""),
                "blurb": str(p.get("blurb") or "")[:500],
            }
        )
    add_event(session, run.id, stage="discover", event_type="search_fetched", message=f"keyword={query} 命中 {len(candidates)} 个帖子候选", related_url=search_url, counters={"discovered_posts": len(candidates)}, trace={"api": search_url})
    session.commit()

    stats = {"posts_created": 0, "raw_pages_created": 0, "chunks_created": 0, "deduped": 0, "fetched_count": 0, "failed": 0}
    errors: list[str] = []

    # 2) 逐个候选取全文并入库（独立 try/except，单条失败不影响其余）
    for index, candidate in enumerate(candidates, start=1):
        post_id = candidate["post_id"]
        try:
            time.sleep(request_delay)
            post_payload = fetch_json(f"{base}/posts/{post_id}.json")
            stats["fetched_count"] += 1
            content, _raw_page, chunks = create_discourse_post(
                session, source=source, run=run, post_payload=post_payload, candidate=candidate, base=base, query=query, language=language
            )
            if content is None:
                stats["deduped"] += 1
            else:
                stats["posts_created"] += 1
                stats["raw_pages_created"] += 1
                stats["chunks_created"] += chunks
                add_event(session, run.id, stage="parse", event_type="post_imported", message=f"已入库帖子 post={post_id}: {candidate['topic_title'][:60]}", related_url=None, content_item_id=content.id, counters={"post_index": index})
            session.commit()
        except Exception as exc:  # noqa: BLE001 - 单条失败应继续
            stats["failed"] += 1
            errors.append(f"post={post_id}: {exc}")
            session.rollback()
            add_event(session, run.id, stage="fetch", event_type="post_failed", level="error", message=f"帖子 post={post_id} 入库失败: {exc}")
            session.commit()

    run = session.get(CrawlRun, run.id)
    assert run is not None
    run.status = "success" if not errors else ("partial" if stats["posts_created"] else "failed")
    run.finished_at = utcnow()
    run.discovered_count = len(candidates)
    run.fetched_count = stats["fetched_count"]
    run.parsed_count = stats["posts_created"]
    run.extracted_count = stats["posts_created"]
    run.deduped_count = stats["deduped"]
    run.chunked_count = stats["chunks_created"]
    run.embedded_count = 0
    run.error_count = len(errors)
    run.error_message = "; ".join(errors) if errors else None
    job.last_run_at = run.finished_at
    add_event(session, run.id, stage="finalize", event_type="run_finished", message=f"Discourse {forum} 采集完成：posts {stats['posts_created']}，chunks {stats['chunks_created']}，dedup {stats['deduped']}，failed {stats['failed']}。", related_url=base, counters={**stats, "errors": len(errors)})
    session.commit()

    # 同步持久化的 chunk 计数，抵御 chunking 实现变更
    actual_chunks = session.execute(
        text("select count(*) from content_chunks cc join content_items ci on cc.content_item_id=ci.id where ci.crawl_run_id=:run"),
        {"run": str(run.id)},
    ).scalar_one()
    if int(actual_chunks) != run.chunked_count:
        run.chunked_count = int(actual_chunks)
        session.commit()

    return {"forum": forum, "base": base, "source_id": str(source.id), "job_id": str(job.id), "run_id": str(run.id), "status": run.status, "posts_discovered": len(candidates), **stats, "chunked_count": run.chunked_count, "errors": errors}


def ingest(args: argparse.Namespace) -> dict[str, Any]:
    if args.max_results < 1 or args.max_results > 50:
        raise SystemExit("--max-results 必须在 1 到 50 之间")
    engine = create_engine(get_settings().sync_database_url)
    per_forum: list[dict[str, Any]] = []
    with Session(engine) as session:
        for forum in args.forums:
            base = FORUMS[forum]
            result = ingest_forum(
                session,
                forum=forum,
                base=base,
                query=args.query,
                max_results=args.max_results,
                category_id=args.category_id,
                request_delay=args.request_delay,
                language=args.language,
            )
            per_forum.append(result)
    return {"status": "success", "query": args.query, "results": per_forum}


def main() -> None:
    args = parse_args()
    result = ingest(args)
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        for key, value in result.items():
            print(f"{key}: {value}")


if __name__ == "__main__":
    main()