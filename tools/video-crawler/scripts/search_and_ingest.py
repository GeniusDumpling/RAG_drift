#!/usr/bin/env python3
"""搜索公开 YouTube 视频并入库 RAG（搜索 → 去重 → 逐个入库）。

搜索复用 youtube_search.py，单视频入库复用 youtube_transcript_evidence.py 的
collect/keyframe/VLM/ingest 各阶段。每个候选独立处理，单个失败不影响整体。

用法示例：
    uv run python3 skills/video-crawler/scripts/search_and_ingest.py \
        --query "drone GPS spoofing" --caption-only --language en --video-limit 3 --json
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any

from youtube_search import SearchError, load_api_key, search_youtube_videos
from youtube_transcript_evidence import (
    DEFAULT_KEYFRAME_DOWNLOADS_DIR,
    EvidenceCollectionError,
    collect_public_evidence,
    extract_keyframes_as_jpegs,
    ingest_video_evidence,
    load_vlm_config,
    persist_keyframes,
    summarize_video_evidence_with_vlm,
)

MAX_ATTEMPTS = 2
RETRY_DELAY_SECONDS = 3.0


def load_existing_dedup_keys() -> set[str]:
    """返回已入库的 video-evidence dedup_key；后端不可用时返回空集（由 ingest 内部兜底去重）。"""
    backend_root = Path(__file__).resolve().parents[3] / "backend"
    if str(backend_root) not in sys.path:
        sys.path.insert(0, str(backend_root))
    try:
        from app.core.config import get_settings
        from app.models.content import ContentItem
        from sqlalchemy import create_engine, select
        from sqlalchemy.orm import Session
    except ImportError:
        return set()
    try:
        settings = get_settings()
        with Session(create_engine(settings.sync_database_url)) as session:
            return set(
                session.scalars(
                    select(ContentItem.dedup_key).where(ContentItem.dedup_key.like("video-evidence:%"))
                )
            )
    except Exception:
        return set()


def ingest_candidate(
    video_url: str,
    *,
    language: str,
    allow_whisper_fallback: bool,
    keyframes_dir: Path,
    env_file: Path,
) -> dict[str, Any]:
    """对单个候选执行完整入库；无字幕且关闭 ASR 时直接返回原始 evidence。"""
    result = collect_public_evidence(
        video_url, language, allow_whisper_fallback=allow_whisper_fallback
    )
    if result.get("status") != "success":
        return result
    keyframes = []
    duration = result.get("duration_seconds")
    if duration:
        keyframes = extract_keyframes_as_jpegs(video_url, duration_seconds=duration)
        result["keyframes"] = persist_keyframes(
            keyframes, result["video_id"], downloads_dir=keyframes_dir
        )
    config = load_vlm_config(env_file)
    result["video_summary"] = summarize_video_evidence_with_vlm(
        result, keyframes, base_url=config.base_url, api_key=config.api_key, model=config.model
    )
    result["ingestion"] = ingest_video_evidence(result)
    return result


def outcome_of(result: dict[str, Any]) -> str:
    ingestion = result.get("ingestion") or {}
    if ingestion.get("status") == "success":
        return "success"
    if ingestion.get("status") == "skipped":
        return "duplicate"
    if result.get("status") == "no_public_captions":
        return "no_captions"
    return "failed"


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--query", required=True, help="YouTube 搜索词")
    parser.add_argument("--max-results", type=int, default=10, help="搜索候选数（1-50），默认 10")
    parser.add_argument("--video-limit", type=int, default=3, help="最多入库的视频数，默认 3")
    parser.add_argument("--caption-only", action="store_true", help="只搜带字幕的视频")
    parser.add_argument(
        "--order",
        choices=("date", "rating", "relevance", "title", "videoCount", "viewCount"),
        default="relevance",
        help="排序方式，默认 relevance",
    )
    parser.add_argument("--language", default="en", help="字幕语言优先级，默认 en")
    parser.add_argument("--whisper-fallback", action="store_true", help="无字幕时本地 Whisper 转写")
    parser.add_argument("--keyframes-dir", type=Path, default=DEFAULT_KEYFRAME_DOWNLOADS_DIR)
    parser.add_argument("--env-file", type=Path, default=Path(".env"))
    parser.add_argument("--json", action="store_true", help="输出 JSON（默认）")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if not 1 <= args.max_results <= 50:
        print("错误：--max-results 必须在 1 到 50 之间", file=sys.stderr)
        return 1
    try:
        api_key = load_api_key(args.env_file)
        page = search_youtube_videos(
            args.query,
            api_key=api_key,
            max_results=args.max_results,
            order=args.order,
            caption_only=args.caption_only,
        )
    except SearchError as exc:
        print(json.dumps({"status": "failed", "error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 1

    candidates = page.get("items") or []
    existing = load_existing_dedup_keys()
    results: list[dict[str, Any]] = []
    for candidate in candidates[: args.video_limit]:
        url = candidate["canonical_url"]
        dedup_key = f"video-evidence:{url}"
        if dedup_key in existing:
            results.append(
                {"video_id": candidate["video_id"], "canonical_url": url, "outcome": "duplicate"}
            )
            continue
        result: dict[str, Any] | None = None
        last_error: str | None = None
        for attempt in range(MAX_ATTEMPTS):
            try:
                result = ingest_candidate(
                    url,
                    language=args.language,
                    allow_whisper_fallback=args.whisper_fallback,
                    keyframes_dir=args.keyframes_dir,
                    env_file=args.env_file,
                )
                break
            except EvidenceCollectionError as exc:
                last_error = str(exc)
                if attempt < MAX_ATTEMPTS - 1:
                    time.sleep(RETRY_DELAY_SECONDS)
        if result is not None:
            results.append(
                {
                    "video_id": candidate["video_id"],
                    "canonical_url": url,
                    "outcome": outcome_of(result),
                    "content_id": (result.get("ingestion") or {}).get("content_id"),
                }
            )
        else:
            results.append(
                {
                    "video_id": candidate["video_id"],
                    "canonical_url": url,
                    "outcome": "failed",
                    "error": last_error,
                }
            )

    summary = {
        "query": args.query,
        "candidates": len(candidates),
        "video_limit": args.video_limit,
        "success_count": sum(1 for r in results if r["outcome"] == "success"),
        "results": results,
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())