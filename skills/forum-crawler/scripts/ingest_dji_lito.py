#!/usr/bin/env python3
"""Import public DJI BBS device-series threads and replies into intelligence-rag."""

from __future__ import annotations

import argparse
import html
import json
import re
import sys
import time
import urllib.request
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "backend"))
sys.path.insert(0, str(ROOT))

from sqlalchemy import create_engine, select, text  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

from app.core.config import get_settings  # noqa: E402
from app.db.base import utcnow  # noqa: E402
from app.models.content import Author, ContentChunk, ContentItem, RawPage  # noqa: E402
from app.models.control import CrawlJob, CrawlRun, CrawlRunEvent, SourceSite  # noqa: E402
from app.repositories.contents import stable_hash  # noqa: E402
from app.services.chunking import build_chunks  # noqa: E402

USER_AGENT = "Mozilla/5.0 (compatible; IntelligenceRAGDemo/0.1; +https://example.local)"
BBS_BASE = "https://bbs.dji.com"
TAG_RE = re.compile(r"<[^>]+>")
WS_RE = re.compile(r"\s+")
SMILEY_RE = re.compile(r"\{:\d+_\d+:\}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--series", default="lito")
    parser.add_argument("--thread-limit", type=int, default=5)
    parser.add_argument("--reply-limit", type=int, default=20)
    parser.add_argument("--sort-type", default="latest_posts")
    parser.add_argument("--request-delay", type=float, default=0.2)
    parser.add_argument("--json", action="store_true", help="Print compact JSON summary")
    return parser.parse_args()


def fetch_json(url: str, referer: str) -> dict[str, Any]:
    request = urllib.request.Request(
        url,
        headers={"User-Agent": USER_AGENT, "Accept": "application/json,text/plain,*/*", "Referer": referer},
    )
    with urllib.request.urlopen(request, timeout=20) as response:
        return json.loads(response.read().decode("utf-8"))


def clean_text(value: Any) -> str:
    if value is None:
        return ""
    text_value = html.unescape(str(value))
    text_value = text_value.replace("<br>", "\n").replace("<br/>", "\n").replace("<br />", "\n")
    text_value = TAG_RE.sub(" ", text_value)
    text_value = SMILEY_RE.sub(lambda match: f"[表情 {match.group(0)[2:-1]}]", text_value)
    return WS_RE.sub(" ", text_value).strip()


def dji_message_text(value: Any) -> str:
    if not value:
        return ""
    if isinstance(value, str):
        stripped = value.strip()
        if stripped.startswith("[") or stripped.startswith("{"):
            try:
                return dji_message_text(json.loads(stripped))
            except json.JSONDecodeError:
                return clean_text(value)
        return clean_text(value)
    if isinstance(value, dict):
        parts: list[str] = []
        desc = clean_text(value.get("desc"))
        if desc:
            parts.append(desc)
        media = value.get("imgsrc") or value.get("url")
        if media:
            parts.append(f"[媒体] {media}")
        return "\n".join(parts)
    if isinstance(value, list):
        return "\n".join(part for part in (dji_message_text(item) for item in value) if part)
    return clean_text(value)


def compact_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def dt_from_unix(value: Any) -> datetime | None:
    if value in (None, ""):
        return None
    try:
        return datetime.fromtimestamp(int(value), tz=UTC)
    except (ValueError, TypeError, OSError):
        return None


def author_name(author: dict[str, Any] | None) -> str:
    if not isinstance(author, dict):
        return "未知用户"
    return str(author.get("name") or author.get("uuid") or author.get("id") or "未知用户")


def get_author(session: Session, source_id: Any, payload: dict[str, Any] | None) -> Author:
    payload = payload if isinstance(payload, dict) else {}
    external_id = str(payload.get("id") or payload.get("uuid") or author_name(payload))
    existing = session.scalar(
        select(Author).where(Author.source_site_id == source_id, Author.external_author_id == external_id).limit(1)
    )
    if existing is not None:
        return existing
    author = Author(
        source_site_id=source_id,
        display_name=author_name(payload),
        handle=str(payload.get("uuid")) if payload.get("uuid") is not None else None,
        profile_url=None,
        external_author_id=external_id,
        raw_json=payload,
    )
    session.add(author)
    session.flush()
    return author


def urls(series: str, thread_limit: int, reply_limit: int, sort_type: str) -> dict[str, Any]:
    source_page = f"{BBS_BASE}/pro/devices?series={series}"
    thread_list = (
        f"{BBS_BASE}/api/v2/forum/thread/list?channel_slug={series}"
        f"&limit={thread_limit}&offset=0&sort_type={sort_type}&device=desktop"
    )
    return {
        "source_page": source_page,
        "thread_list_api": thread_list,
        "thread_url": lambda tid: f"{BBS_BASE}/pro/detail?tid={tid}",
        "reply_url": lambda tid, pid: f"{BBS_BASE}/pro/detail?tid={tid}#reply-{pid}",
        "detail_api": lambda tid: f"{BBS_BASE}/api/v2/forum/thread/info/{tid}?device=desktop",
        "reply_api": lambda tid: f"{BBS_BASE}/api/v2/forum/thread/{tid}/reply?limit={reply_limit}&offset=0&device=desktop",
    }


def get_source_and_job(session: Session, *, series: str, thread_limit: int, reply_limit: int, sort_type: str) -> tuple[SourceSite, CrawlJob]:
    u = urls(series, thread_limit, reply_limit, sort_type)
    source_name = f"DJI 论坛 {series.upper() if series != 'lito' else 'Lito'} 系列"
    source = session.scalar(select(SourceSite).where(SourceSite.name == source_name).limit(1))
    if source is None:
        source = SourceSite(
            name=source_name,
            site_type="forum",
            base_url=u["source_page"],
            allowed_domains=["bbs.dji.com"],
            fetch_mode="http_api",
            default_language="zh",
            active=True,
            config_json={"kind": "dji_bbs", "series": series, "source_page": u["source_page"], "api_base": f"{BBS_BASE}/api/v2"},
        )
        session.add(source)
        session.flush()

    job_name = f"DJI {series} 最新主题与评论抓取"
    job = session.scalar(select(CrawlJob).where(CrawlJob.source_site_id == source.id, CrawlJob.name == job_name).limit(1))
    if job is None:
        job = CrawlJob(
            source_site_id=source.id,
            name=job_name,
            trigger_mode="manual",
            cron_expr=None,
            seed_config_json={"series": series, "thread_limit": thread_limit, "reply_limit": reply_limit, "sort_type": sort_type, "thread_list_api": u["thread_list_api"]},
            parser_profile="dji_forum_api",
            max_pages=thread_limit,
            enabled=True,
            agent_policy_json={"extraction_mode": "deterministic_api_mapping"},
            last_run_at=None,
            next_run_at=None,
        )
        session.add(job)
        session.flush()
    return source, job


def add_event(session: Session, run_id: Any, *, stage: str, event_type: str, message: str, level: str = "info", related_url: str | None = None, raw_page_id: Any = None, content_item_id: Any = None, counters: dict[str, Any] | None = None, trace: dict[str, Any] | None = None) -> None:
    session.add(
        CrawlRunEvent(
            crawl_run_id=run_id,
            stage=stage,
            level=level,
            event_type=event_type,
            message=message,
            related_url=related_url,
            related_raw_page_id=raw_page_id,
            related_content_item_id=content_item_id,
            counters_json=counters or {},
            agent_trace_json=trace or {},
        )
    )


def existing_item(session: Session, source_id: Any, canonical_url: str, item_type: str, cleaned_text: str) -> ContentItem | None:
    content_hash = stable_hash(cleaned_text)
    dedup_key = stable_hash(f"{source_id}:{canonical_url}:{item_type}:{content_hash}")
    return session.scalar(select(ContentItem).where(ContentItem.dedup_key == dedup_key).limit(1))


def create_item(session: Session, *, source: SourceSite, run: CrawlRun, raw_payload: dict[str, Any], requested_url: str, final_url: str, raw_text: str, item_type: str, title: str | None, cleaned_text: str, summary_text: str | None, tags: list[str], author_payload: dict[str, Any] | None, published_at: datetime | None, metadata: dict[str, Any], parent_item_id: Any = None, thread_root_id: Any = None, thread_title: str | None = None) -> tuple[ContentItem | None, RawPage | None, int]:
    if (existing := existing_item(session, source.id, final_url, item_type, cleaned_text)) is not None:
        return None, None, 0

    body_json = compact_json(raw_payload)
    raw_page = RawPage(
        source_site_id=source.id,
        crawl_run_id=run.id,
        requested_url=requested_url,
        final_url=final_url,
        http_status=200,
        content_type="application/json",
        response_headers_json={"x-ingest-adapter": "dji_forum_api"},
        raw_html=body_json,
        raw_text=raw_text,
        raw_json=raw_payload,
        fetched_at=utcnow(),
        fetch_error=None,
        parser_profile="dji_forum_api",
        extraction_method="deterministic_api_mapping",
        extraction_confidence=1.0,
        parse_status="parsed",
        parse_error=None,
        body_hash=stable_hash(body_json),
    )
    session.add(raw_page)
    session.flush()

    content_hash = stable_hash(cleaned_text)
    content = ContentItem(
        source_site_id=source.id,
        raw_page_id=raw_page.id,
        crawl_run_id=run.id,
        author_id=get_author(session, source.id, author_payload).id,
        parent_item_id=parent_item_id,
        thread_root_id=thread_root_id,
        item_type=item_type,
        title=title,
        canonical_url=final_url,
        source_url=requested_url,
        published_at=published_at,
        language="zh",
        raw_text=raw_text,
        cleaned_text=cleaned_text,
        summary_text=summary_text,
        structured_by="dji_forum_api_importer",
        extraction_confidence=1.0,
        tags=tags,
        metadata_json=metadata,
        content_hash=content_hash,
        dedup_key=stable_hash(f"{source.id}:{final_url}:{item_type}:{content_hash}"),
        search_tsv=None,
    )
    session.add(content)
    session.flush()

    chunk_count = 0
    for built in build_chunks(item_type=item_type, title=title, cleaned_text=cleaned_text, summary_text=summary_text, tags=tags, thread_title=thread_title):
        session.add(
            ContentChunk(
                content_item_id=content.id,
                chunk_index=built.chunk_index,
                char_start=built.start_char,
                char_end=built.end_char,
                display_text=built.display_text,
                embed_text=built.embed_text,
                token_count=built.token_count,
                chunk_metadata_json={**built.chunk_metadata_json, "ingest_source": "dji_forum_api"},
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


def ingest(args: argparse.Namespace) -> dict[str, Any]:
    if args.thread_limit < 1 or args.reply_limit < 0:
        raise SystemExit("thread-limit must be >= 1 and reply-limit must be >= 0")

    u = urls(args.series, args.thread_limit, args.reply_limit, args.sort_type)
    engine = create_engine(get_settings().sync_database_url)
    errors: list[str] = []

    with Session(engine) as session:
        source, job = get_source_and_job(session, series=args.series, thread_limit=args.thread_limit, reply_limit=args.reply_limit, sort_type=args.sort_type)
        run = CrawlRun(
            source_site_id=source.id,
            crawl_job_id=job.id,
            trigger_type="manual",
            execution_mode="deterministic_api_import",
            seed_url=u["source_page"],
            status="running",
            started_at=utcnow(),
            config_snapshot_json={"job_name": job.name, "series": args.series, "thread_limit": args.thread_limit, "reply_limit": args.reply_limit, "sort_type": args.sort_type, "thread_list_api": u["thread_list_api"]},
        )
        session.add(run)
        session.flush()
        add_event(session, run.id, stage="discover", event_type="run_started", message=f"开始抓取 DJI 论坛 {args.series}：最新 {args.thread_limit} 个主题，每个主题最多 {args.reply_limit} 条评论。", related_url=u["source_page"], counters={"thread_limit": args.thread_limit, "reply_limit": args.reply_limit}, trace={"adapter": "dji_forum_api"})
        session.commit()

        list_payload = fetch_json(u["thread_list_api"], u["source_page"])
        threads = list_payload.get("data", {}).get("items", [])[: args.thread_limit]
        add_event(session, run.id, stage="discover", event_type="thread_list_fetched", message=f"发现 {len(threads)} 个 DJI {args.series} 主题。", related_url=u["thread_list_api"], counters={"discovered_threads": len(threads)}, trace={"api": u["thread_list_api"]})
        session.commit()

        stats = {"threads_created": 0, "comments_created": 0, "raw_pages_created": 0, "chunks_created": 0, "deduped": 0, "fetched_count": 0}

        for index, list_item in enumerate(threads, start=1):
            tid = int(list_item["tid"])
            thread_url = u["thread_url"](tid)
            try:
                time.sleep(args.request_delay)
                detail_payload = fetch_json(u["detail_api"](tid), u["source_page"])
                time.sleep(args.request_delay)
                replies_payload = fetch_json(u["reply_api"](tid), u["source_page"])
                stats["fetched_count"] += 2

                detail_item = detail_payload.get("data", {}).get("item") or list_item
                replies = replies_payload.get("data", {}).get("items", [])[: args.reply_limit]
                title = str(detail_item.get("subject") or list_item.get("subject") or f"DJI thread {tid}")
                body = dji_message_text(detail_item.get("message")) or clean_text(detail_item.get("summary") or list_item.get("summary")) or f"{title}\n[该主题正文主要为图片或附件，原始 JSON 已保留在 Raw page 中。]"
                summary = (clean_text(detail_item.get("summary") or list_item.get("summary")) or body or title)[:240]
                cleaned_thread = "\n\n".join(part for part in [f"主题：{title}", body, f"浏览：{detail_item.get('views') or list_item.get('views')}", f"回复：{detail_item.get('replies') or list_item.get('replies')}"] if part)

                thread_content, thread_raw, thread_chunks = create_item(
                    session,
                    source=source,
                    run=run,
                    raw_payload={"thread_list_item": list_item, "thread_detail": detail_payload},
                    requested_url=u["detail_api"](tid),
                    final_url=thread_url,
                    raw_text=cleaned_thread,
                    item_type="thread",
                    title=title,
                    cleaned_text=cleaned_thread,
                    summary_text=summary,
                    tags=["dji", "bbs", args.series, "thread"],
                    author_payload=detail_item.get("author") or list_item.get("author"),
                    published_at=dt_from_unix(detail_item.get("dateline") or list_item.get("dateline")),
                    metadata={"tid": tid, "views": detail_item.get("views") or list_item.get("views"), "replies": detail_item.get("replies") or list_item.get("replies"), "series": args.series, "source_page": u["source_page"], "detail_api_url": u["detail_api"](tid)},
                )
                if thread_content is None:
                    stats["deduped"] += 1
                    existing = existing_item(session, source.id, thread_url, "thread", cleaned_thread)
                    thread_root_id = existing.id if existing is not None else None
                else:
                    stats["threads_created"] += 1
                    stats["raw_pages_created"] += 1
                    stats["chunks_created"] += thread_chunks
                    thread_root_id = thread_content.id
                    add_event(session, run.id, stage="parse", event_type="thread_imported", message=f"已导入主题 {tid}: {title}", related_url=thread_url, raw_page_id=thread_raw.id if thread_raw else None, content_item_id=thread_content.id, counters={"thread_index": index, "reply_count": len(replies)})

                for reply in replies:
                    pid = int(reply["pid"])
                    message = clean_text(reply.get("message")) or "[空评论]"
                    reply_author = author_name(reply.get("author"))
                    cleaned_comment = f"主题：{title}\n评论作者：{reply_author}\n评论内容：{message}"
                    comment_content, _comment_raw, comment_chunks = create_item(
                        session,
                        source=source,
                        run=run,
                        raw_payload={"thread_tid": tid, "reply": reply, "reply_api_url": u["reply_api"](tid)},
                        requested_url=u["reply_api"](tid),
                        final_url=u["reply_url"](tid, pid),
                        raw_text=cleaned_comment,
                        item_type="comment",
                        title=f"评论 {pid} · {title}",
                        cleaned_text=cleaned_comment,
                        summary_text=message[:240],
                        tags=["dji", "bbs", args.series, "comment"],
                        author_payload=reply.get("author"),
                        published_at=dt_from_unix(reply.get("dateline")),
                        metadata={"tid": tid, "pid": pid, "position": reply.get("position"), "likes": reply.get("likes"), "parent_id": reply.get("parent_id"), "series": args.series, "thread_title": title, "reply_api_url": u["reply_api"](tid)},
                        parent_item_id=thread_root_id,
                        thread_root_id=thread_root_id,
                        thread_title=title,
                    )
                    if comment_content is None:
                        stats["deduped"] += 1
                    else:
                        stats["comments_created"] += 1
                        stats["raw_pages_created"] += 1
                        stats["chunks_created"] += comment_chunks
                session.commit()
            except Exception as exc:  # noqa: BLE001 - importer should finish remaining threads.
                errors.append(f"tid={tid}: {exc}")
                session.rollback()
                add_event(session, run.id, stage="fetch", event_type="thread_failed", level="error", message=f"主题 {tid} 导入失败: {exc}", related_url=thread_url)
                session.commit()

        run = session.get(CrawlRun, run.id)
        assert run is not None
        run.status = "success" if not errors else ("partial" if stats["threads_created"] or stats["comments_created"] else "failed")
        run.finished_at = utcnow()
        run.discovered_count = len(threads)
        run.fetched_count = stats["fetched_count"]
        run.parsed_count = stats["threads_created"] + stats["comments_created"]
        run.extracted_count = run.parsed_count
        run.deduped_count = stats["deduped"]
        run.chunked_count = stats["chunks_created"]
        run.embedded_count = 0
        run.error_count = len(errors)
        run.error_message = "; ".join(errors) if errors else None
        job.last_run_at = run.finished_at
        add_event(session, run.id, stage="finalize", event_type="run_finished", message=f"DJI {args.series} 导入完成：主题 {stats['threads_created']}，评论 {stats['comments_created']}，chunks {stats['chunks_created']}，dedup {stats['deduped']}。", related_url=u["source_page"], counters={**stats, "errors": len(errors)})
        session.commit()

        # Guard against future chunking implementation changes by syncing persisted count.
        actual_chunks = session.execute(
            text("select count(*) from content_chunks cc join content_items ci on cc.content_item_id=ci.id where ci.crawl_run_id=:run"),
            {"run": str(run.id)},
        ).scalar_one()
        if int(actual_chunks) != run.chunked_count:
            run.chunked_count = int(actual_chunks)
            session.commit()

        return {"source_id": str(source.id), "job_id": str(job.id), "run_id": str(run.id), "status": run.status, "threads_discovered": len(threads), **stats, "chunked_count": run.chunked_count, "errors": errors}


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
