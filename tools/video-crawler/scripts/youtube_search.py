#!/usr/bin/env python3
"""使用 YouTube Data API v3 搜索公开视频候选。

只负责「搜索 + 规范化候选」，不入库、不下载。输出为 JSON，便于人工核对后
再逐个喂给 youtube_transcript_evidence.py 手动入库。

用法示例：

    YOUTUBE_API_KEY=... python3 tools/video-crawler/scripts/youtube_search.py \
        --query "DJI 飞控 固件" \
        --caption-only \
        --max-results 20 \
        --order relevance \
        --json
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from config_loader import load_global_env

SEARCH_ENDPOINT = "https://www.googleapis.com/youtube/v3/search"
_VIDEO_ID_RE = re.compile(r"^[A-Za-z0-9_-]{11}$")


class SearchError(RuntimeError):
    """搜索失败或配置缺失。"""


def load_api_key() -> str:
    """从全局 .env（仓库根目录）读取 YOUTUBE_API_KEY。"""
    load_global_env()
    key = os.environ.get("YOUTUBE_API_KEY", "").strip()
    if not key:
        raise SearchError("缺少 YOUTUBE_API_KEY（请在仓库根目录 .env 中配置）")
    return key


def _http_get_json(url: str) -> dict[str, Any]:
    request = Request(url, headers={"Accept": "application/json"})
    try:
        with urlopen(request, timeout=30) as response:
            return json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        message = body
        try:
            error = json.loads(body).get("error", {})
            message = error.get("message") or body
        except Exception:
            pass
        raise SearchError(f"HTTP {exc.code}: {message}") from exc
    except URLError as exc:
        raise SearchError(f"网络错误：{exc.reason}") from exc


def _to_candidate(item: dict[str, Any]) -> dict[str, Any]:
    resource_id = item.get("id") or {}
    video_id = str(resource_id.get("videoId") or "")
    if not _VIDEO_ID_RE.fullmatch(video_id):
        raise SearchError(f"搜索结果包含非法视频 ID: {video_id!r}")
    snippet = item.get("snippet") or {}
    return {
        "video_id": video_id,
        "canonical_url": f"https://www.youtube.com/watch?v={video_id}",
        "title": str(snippet.get("title") or ""),
        "channel": str(snippet.get("channelTitle") or ""),
        "channel_id": str(snippet.get("channelId") or ""),
        "published_at": str(snippet.get("publishedAt") or ""),
        "description": str(snippet.get("description") or "")[:500],
    }


def search_youtube_videos(
    query: str,
    *,
    api_key: str,
    max_results: int = 10,
    order: str = "relevance",
    caption_only: bool = False,
    published_after: str | None = None,
    published_before: str | None = None,
    video_duration: str | None = None,
    relevance_language: str | None = None,
    region_code: str | None = None,
    page_token: str | None = None,
) -> dict[str, Any]:
    """调用 search.list 返回一页候选，附带分页 token 与页信息。"""
    if not api_key.strip():
        raise SearchError("缺少 YOUTUBE_API_KEY")
    params: dict[str, str] = {
        "part": "snippet",
        "type": "video",
        "q": query.strip(),
        "maxResults": str(max_results),
        "order": order,
        "key": api_key.strip(),
    }
    if caption_only:
        params["videoCaption"] = "closedCaption"
    if published_after:
        params["publishedAfter"] = published_after
    if published_before:
        params["publishedBefore"] = published_before
    if video_duration and video_duration != "any":
        params["videoDuration"] = video_duration
    if relevance_language:
        params["relevanceLanguage"] = relevance_language
    if region_code:
        params["regionCode"] = region_code
    if page_token:
        params["pageToken"] = page_token

    body = _http_get_json(f"{SEARCH_ENDPOINT}?{urlencode(params)}")
    items = body.get("items") or []
    candidates = [_to_candidate(item) for item in items if isinstance(item, dict)]
    return {
        "items": candidates,
        "next_page_token": body.get("nextPageToken"),
        "page_info": body.get("pageInfo") or {},
    }


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--query", required=True, help="YouTube 搜索词")
    parser.add_argument("--max-results", type=int, default=10, help="每页候选数（1-50），默认 10")
    parser.add_argument(
        "--order",
        choices=("date", "rating", "relevance", "title", "videoCount", "viewCount"),
        default="relevance",
        help="排序方式，默认 relevance",
    )
    parser.add_argument(
        "--caption-only",
        action="store_true",
        help="只返回带字幕（closedCaption）的视频",
    )
    parser.add_argument(
        "--published-after",
        default=None,
        help="发布时间下限（RFC3339，如 2026-01-01T00:00:00Z）",
    )
    parser.add_argument(
        "--published-before",
        default=None,
        help="发布时间上限（RFC3339）",
    )
    parser.add_argument(
        "--video-duration",
        choices=("any", "long", "medium", "short"),
        default=None,
        help="按时长过滤，默认不过滤",
    )
    parser.add_argument("--relevance-language", default=None, help="相关性语言，如 zh、en")
    parser.add_argument("--region-code", default=None, help="地区代码，如 CN、US")
    parser.add_argument("--page-token", default=None, help="上一页返回的 next_page_token")
    parser.add_argument("--json", action="store_true", help="输出 JSON（默认）")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if not 1 <= args.max_results <= 50:
        print("错误：--max-results 必须在 1 到 50 之间", file=sys.stderr)
        return 1
    try:
        api_key = load_api_key()
        result = search_youtube_videos(
            args.query,
            api_key=api_key,
            max_results=args.max_results,
            order=args.order,
            caption_only=args.caption_only,
            published_after=args.published_after,
            published_before=args.published_before,
            video_duration=args.video_duration,
            relevance_language=args.relevance_language,
            region_code=args.region_code,
            page_token=args.page_token,
        )
    except SearchError as exc:
        error = json.dumps({"status": "failed", "error": str(exc)}, ensure_ascii=False)
        print(error, file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())