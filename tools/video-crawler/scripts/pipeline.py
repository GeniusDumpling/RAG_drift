"""采集编排：搜索 → 去重 → 逐个入库。

所有参数来自 conf.yaml（经 config_loader），API Key 来自仓库根 .env（全局）。
单个候选独立处理并带重试，单个失败不影响整体。
"""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path
from typing import Any

from config_loader import SCRIPT_DIR, conf_get, load_conf, load_global_env
from youtube_search import SearchError, load_api_key, search_youtube_videos
from youtube_transcript_evidence import (
    EvidenceCollectionError,
    collect_public_evidence,
    extract_keyframes_as_jpegs,
    ingest_video_evidence,
    load_vlm_config,
    persist_keyframes,
    summarize_video_evidence_with_vlm,
)


def select_rotating_entry(conf: dict) -> dict:
    """按时间桶轮换主题条目；条目为字符串（无自带语言）或 {query, language}。"""
    entries = list(conf_get(conf, "search", "queries", []) or [])
    if not entries:
        raise ValueError("conf.yaml 的 search.queries 不能为空")
    interval = int(conf_get(conf, "search", "rotation_interval_seconds", 7200) or 7200)
    entry = entries[int(time.time()) // interval % len(entries)]
    if isinstance(entry, str):
        return {"query": entry}
    if isinstance(entry, dict) and entry.get("query"):
        return entry
    raise ValueError(f"search.queries 条目格式错误: {entry!r}")


def select_rotating_query(conf: dict) -> str:
    """兼容旧调用：仅返回轮换查询词。"""
    return select_rotating_entry(conf)["query"]


def apply_proxy(conf: dict) -> None:
    """从 conf.yaml 注入下载代理；已存在环境变量优先。"""
    proxy = str(conf_get(conf, "download", "proxy", "") or "").strip()
    if proxy:
        os.environ.setdefault("HTTPS_PROXY", proxy)
        os.environ.setdefault("HTTP_PROXY", proxy)
        os.environ.setdefault("https_proxy", proxy)
        os.environ.setdefault("http_proxy", proxy)


def resolve_keyframes_dir(conf: dict) -> Path:
    value = str(conf_get(conf, "download", "keyframes_dir", "downloads") or "downloads")
    path = Path(value)
    return path if path.is_absolute() else SCRIPT_DIR / path


def resolve_whisper_model_cache(conf: dict) -> Path:
    value = str(conf_get(conf, "whisper", "model_cache", "model-cache/whisper") or "model-cache/whisper")
    path = Path(value)
    return path if path.is_absolute() else SCRIPT_DIR.parents[2] / path


def load_existing_dedup_keys() -> set[str]:
    """返回已入库的 video-evidence dedup_key；后端不可用时返回空集（由 ingest 内部兜底去重）。"""
    backend_root = SCRIPT_DIR.parents[2] / "backend"
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
    whisper_model: str = "small",
    whisper_device: str = "auto",
    whisper_model_cache: Path = Path("model-cache/whisper"),
) -> dict[str, Any]:
    """对单个候选执行完整入库；无字幕且关闭 ASR 时直接返回原始 evidence。"""
    result = collect_public_evidence(
        video_url,
        language,
        allow_whisper_fallback=allow_whisper_fallback,
        whisper_model=whisper_model,
        whisper_device=whisper_device,
        whisper_model_cache=whisper_model_cache,
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
    config = load_vlm_config()
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


def run_search_and_ingest(
    conf: dict,
    *,
    query: str,
    max_results: int | None = None,
    video_limit: int | None = None,
    language: str | None = None,
    order: str | None = None,
    caption_only: bool | None = None,
    whisper_fallback: bool | None = None,
) -> dict[str, Any]:
    """执行一轮搜索 → 去重 → 入库，返回 JSON 汇总。None 表示用 conf.yaml 默认值。"""
    search = conf.get("search") or {}
    max_results = max_results or int(search.get("max_results", 10))
    video_limit = video_limit if video_limit is not None else int(search.get("video_limit", 3))
    language = language or str(search.get("language", "en"))
    order = order or str(search.get("order", "relevance"))
    caption_only = search.get("caption_only", True) if caption_only is None else caption_only
    whisper_fallback = search.get("whisper_fallback", False) if whisper_fallback is None else whisper_fallback
    retry_attempts = int(conf_get(conf, "retry", "attempts", 2) or 2)
    retry_delay = float(conf_get(conf, "retry", "delay_seconds", 3.0) or 3.0)
    keyframes_dir = resolve_keyframes_dir(conf)
    whisper_model = str(conf_get(conf, "whisper", "model", "small") or "small")
    whisper_device = str(conf_get(conf, "whisper", "device", "auto") or "auto")
    whisper_model_cache = resolve_whisper_model_cache(conf)

    apply_proxy(conf)
    load_global_env()
    try:
        page = search_youtube_videos(
            query,
            api_key=load_api_key(),
            max_results=max_results,
            order=order,
            caption_only=caption_only,
        )
    except SearchError as exc:
        return {"status": "failed", "query": query, "error": str(exc)}

    existing = load_existing_dedup_keys()
    results: list[dict[str, Any]] = []
    for candidate in (page.get("items") or [])[:video_limit]:
        url = candidate["canonical_url"]
        if f"video-evidence:{url}" in existing:
            results.append(
                {"video_id": candidate["video_id"], "canonical_url": url, "outcome": "duplicate"}
            )
            continue
        result: dict[str, Any] | None = None
        last_error: str | None = None
        for attempt in range(retry_attempts):
            try:
                result = ingest_candidate(
                    url,
                    language=language,
                    allow_whisper_fallback=whisper_fallback,
                    keyframes_dir=keyframes_dir,
                    whisper_model=whisper_model,
                    whisper_device=whisper_device,
                    whisper_model_cache=whisper_model_cache,
                )
                break
            except EvidenceCollectionError as exc:
                last_error = str(exc)
                if attempt < retry_attempts - 1:
                    time.sleep(retry_delay)
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

    return {
        "status": "success",
        "query": query,
        "candidates": len(page.get("items") or []),
        "video_limit": video_limit,
        "success_count": sum(1 for r in results if r["outcome"] == "success"),
        "results": results,
    }


def load_conf_with_env(config_path: Path) -> dict:
    """加载 conf.yaml 并载入全局 .env（供各模块使用）。"""
    load_global_env()
    return load_conf(config_path)


def emit_json(payload: dict[str, Any]) -> None:
    print(json.dumps(payload, ensure_ascii=False, indent=2))
