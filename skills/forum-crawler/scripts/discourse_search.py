#!/usr/bin/env python3
"""对无人机相关的 Discourse 论坛执行搜索并规范化候选结果。

当前只负责「搜索 + 规范化候选」，不入库。调用各论坛公开的 `/search.json?q=...`
接口（无需鉴权），返回命中的帖子候选，便于人工核对后再规划入库。

用法示例：

    python3 skills/video-crawler/scripts/discourse_search.py \
        --query "MAVLink security" \
        --forums ardupilot px4 \
        --json

    python3 skills/video-crawler/scripts/discourse_search.py \
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
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

USER_AGENT = "Mozilla/5.0 (compatible; IntelligenceRAGDiscourseSearch/0.1)"

# 论坛标识 → 根地址。dronecode 已合并进 px4，这里统一指向 px4 即可。
FORUMS: dict[str, str] = {
    "ardupilot": "https://discuss.ardupilot.org",
    "px4": "https://discuss.px4.io",
    "dronecode": "https://discuss.px4.io",  # 301 → PX4，不再单独采集
}


class DiscourseError(RuntimeError):
    """搜索失败或参数错误。"""


def _http_get_json(url: str) -> dict[str, Any]:
    request = Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
    try:
        with urlopen(request, timeout=30) as response:
            return json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        message = exc.headers.get("Location") or body
        raise DiscourseError(f"HTTP {exc.code}: {message}") from exc
    except URLError as exc:
        raise DiscourseError(f"网络错误：{exc.reason}") from exc


def _normalize_post(post: dict[str, Any], base: str, topics: dict[int, dict[str, Any]]) -> dict[str, Any]:
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
) -> dict[str, Any]:
    """调用某论坛的 /search.json 返回一页规范化候选。"""
    params: dict[str, str] = {"q": query.strip(), "limit": str(max_results)}
    if category_id:
        params["category"] = str(category_id)
    result = _http_get_json(f"{base}/search.json?{urlencode(params)}")
    topics = {(t.get("id") or 0): t for t in (result.get("topics") or []) if isinstance(t, dict)}
    posts = result.get("posts") or []
    # Discourse 的 search.json 会忽略单个请求的 limit 上限，这里在代码层面截断。
    candidates = [_normalize_post(p, base, topics) for p in posts[:max_results] if isinstance(p, dict)]
    return {
        "forum": forum,
        "base": base,
        "query": query.strip(),
        "max_results": max_results,
        "count": len(candidates),
        "has_more": bool(result.get("has_more")),
        "search_type": (result.get("grouped_search_result") or {}).get("type"),
        "items": candidates,
    }


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--query", required=True, help="Search 关键词（如 MAVLink security / GPS spoofing）")
    parser.add_argument(
        "--forums",
        nargs="+",
        choices=tuple(FORUMS),
        default=list(FORUMS),
        help="要搜索的论坛标识，空格分隔，默认全部（ardupilot px4）",
    )
    parser.add_argument("--forum", dest="forums_override", help="兼容单数参数，与 --forums 等价")
    parser.add_argument("--max-results", type=int, default=10, help="每个论坛返回的最大帖子数，默认 10")
    parser.add_argument("--category-id", type=int, default=None, help="限定板块 category_id，可选")
    parser.add_argument("--json", action="store_true", help="输出 JSON（默认）")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if not 1 <= args.max_results <= 50:
        print("错误：--max-results 必须在 1 到 50 之间", file=sys.stderr)
        return 1
    if args.forums_override:
        chosen = [args.forums_override]
    else:
        chosen = args.forums

    per_forum: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    for forum in chosen:
        base = FORUMS[forum]
        try:
            per_forum.append(
                search_forum(
                    forum,
                    args.query,
                    base=base,
                    max_results=args.max_results,
                    category_id=args.category_id,
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