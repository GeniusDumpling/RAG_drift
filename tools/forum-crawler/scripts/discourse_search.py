#!/usr/bin/env python3
"""对无人机相关的 Discourse 论坛执行搜索并规范化候选结果。

当前只负责「搜索 + 规范化候选」，不入库。调用各论坛公开的 `/search.json?q=...`
接口（无需鉴权），返回命中的帖子候选，便于人工核对后再规划入库。

用法示例：

    python3 tools/forum-crawler/scripts/discourse_search.py \
        --query "MAVLink security" \
        --forums ardupilot px4 \
        --json

    python3 tools/forum-crawler/scripts/discourse_search.py \
        --query "GPS spoofing" \
        --forum px4 \
        --category-id 4 \
        --max-results 20 \
        --json
"""

from __future__ import annotations

import argparse
import html
import json
import sys
from typing import Any
from urllib.parse import urlencode

from discourse_common import ALIASES, FORUMS, DiscourseClient, DiscourseError, canonical_forums


def _http_get_json(url: str) -> dict[str, Any]:
    from urllib.parse import urlsplit

    parsed = urlsplit(url)
    return DiscourseClient(f"{parsed.scheme}://{parsed.netloc}").get(
        parsed.path + (f"?{parsed.query}" if parsed.query else "")
    )


def _normalize_post(
    post: dict[str, Any], base: str, topics: dict[int, dict[str, Any]]
) -> dict[str, Any]:
    """把一个 Discourse search.json 返回的 post 对象规范化为候选。

    search.json 的 post 对象本身不含标题和 slug，这些位于响应顶层的
    ``topics`` 集合里，因此用 ``topics`` 按 topic_id 映射补全。
    """
    topic_id = int(post.get("topic_id") or 0)
    post_number = int(post.get("post_number") or 1)
    topic = topics.get(topic_id) or {}
    topic_slug = str(topic.get("slug") or "")
    canonical_url = f"{base}/t/{topic_slug}/{topic_id}/{post_number}".rstrip("/")
    return {
        "post_id": int(post.get("id") or 0),
        "topic_id": topic_id,
        "post_number": post_number,
        "canonical_url": canonical_url,
        "topic_title": str(topic.get("title") or "")[:300],
        "username": str(post.get("username") or ""),
        "category_id": int(topic.get("category_id") or post.get("category_id") or 0),
        "created_at": str(post.get("created_at") or ""),
        "like_count": int(post.get("like_count") or 0),
        "blurb": html.unescape(str(post.get("blurb") or ""))[:500],
    }


def search_forum(
    forum: str,
    query: str,
    *,
    base: str,
    max_results: int = 10,
    category_id: int | None = None,
    client: DiscourseClient | None = None,
    max_pages: int = 3,
) -> dict[str, Any]:
    """调用某论坛的 /search.json 返回一页规范化候选。"""
    if not query.strip() or max_results < 1 or max_pages < 1:
        raise ValueError("Query and positive result/page limits are required")
    client = client or DiscourseClient(base)
    search_query = query.strip() + (f" category:{category_id}" if category_id else "")
    candidates = []
    seen = set()
    has_more = False
    result = {}
    for page in range(1, max_pages + 1):
        result = client.get(f"/search.json?{urlencode({'q': search_query, 'page': page})}")
        topics = {int(t["id"]): t for t in result.get("topics", [])}
        posts = result.get("posts") or []
        for post in posts:
            if isinstance(post, dict) and post.get("id") not in seen:
                seen.add(post.get("id"))
                candidates.append(_normalize_post(post, base, topics))
        grouped = result.get("grouped_search_result") or {}
        has_more = bool(grouped.get("more_full_page_results") or grouped.get("more_posts"))
        if len(candidates) >= max_results:
            has_more = has_more or len(candidates) > max_results
            break
        if not posts or not has_more:
            break
    candidates = candidates[:max_results]
    return {
        "forum": forum,
        "base": base,
        "query": query.strip(),
        "max_results": max_results,
        "count": len(candidates),
        "has_more": has_more,
        "search_type": (result.get("grouped_search_result") or {}).get("type"),
        "items": candidates,
    }


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--query", required=True, help="Search 关键词（如 MAVLink security / GPS spoofing）"
    )
    parser.add_argument(
        "--forums",
        nargs="+",
        choices=tuple([*FORUMS, *ALIASES]),
        default=list(FORUMS),
        help="要搜索的论坛标识，空格分隔，默认全部（ardupilot px4）",
    )
    parser.add_argument(
        "--forum", dest="forums_override", choices=[*FORUMS, *ALIASES], help="单个论坛"
    )
    parser.add_argument("--max-pages", type=int, default=3)
    parser.add_argument(
        "--max-results", type=int, default=10, help="每个论坛返回的最大帖子数，默认 10"
    )
    parser.add_argument("--category-id", type=int, default=None, help="限定板块 category_id，可选")
    parser.add_argument("--json", action="store_true", help="输出 JSON（默认）")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if not 1 <= args.max_results <= 500 or args.max_pages < 1 or not args.query.strip():
        print("错误：需要非空关键词、正页数，max-results 范围 1..500", file=sys.stderr)
        return 1
    if args.forums_override:
        chosen = [args.forums_override]
    else:
        chosen = args.forums

    per_forum: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    for forum in canonical_forums(chosen):
        base = FORUMS[forum]
        try:
            per_forum.append(
                search_forum(
                    forum,
                    args.query,
                    base=base,
                    max_results=args.max_results,
                    category_id=args.category_id,
                    max_pages=args.max_pages,
                )
            )
        except DiscourseError as exc:
            errors.append({"forum": forum, "error": str(exc)})

    payload = {
        "status": "success" if (per_forum and not errors) else "failed",
        "total": sum(r["count"] for r in per_forum),
        "results": per_forum,
        "errors": errors,
    }
    print(json.dumps(payload, ensure_ascii=False, sort_keys=True))
    return 0 if payload["status"] == "success" else 1


if __name__ == "__main__":
    raise SystemExit(main())
