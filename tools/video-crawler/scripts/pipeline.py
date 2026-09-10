"""采集编排：搜索 → 去重 → 逐个入库。

所有参数来自 conf.yaml（经 config_loader），API Key 来自仓库根 .env（全局）。
单个候选独立处理并带重试，单个失败不影响整体。
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
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

DEFAULT_SEARCH_QUERIES = [
    {"query": "drone GPS spoofing jamming demonstration", "language": "en"},
    {"query": "drone MAVLink telemetry security vulnerability", "language": "en"},
    {"query": "drone flight controller firmware hardware test", "language": "en"},
    {"query": "drone lost signal flyaway GPS failure", "language": "en"},
    {"query": "无人机 GPS 欺骗 干扰 演示", "language": "zh"},
    {"query": "无人机 飞控 固件 硬件 拆解", "language": "zh"},
]


def default_conf() -> dict[str, Any]:
    """Return a runnable crawler configuration when the optional YAML file is absent."""
    return {
        "search": {
            "queries": DEFAULT_SEARCH_QUERIES,
            "state_path": "search-state.json",
            "max_results": 25,
            "max_pages": 3,
            "success_target": 1,
            "caption_only": True,
            "order": "date",
            "language": "en",
            "whisper_fallback": False,
        },
        "retry": {"attempts": 2, "delay_seconds": 3.0},
        "download": {"keyframes_dir": "downloads"},
        "whisper": {"model": "small", "device": "auto", "model_cache": "model-cache/whisper"},
    }


def resolve_search_state_path(conf: dict) -> Path:
    value = os.environ.get("VIDEO_SEARCH_STATE_PATH") or str(
        conf_get(conf, "search", "state_path", "search-state.json") or "search-state.json"
    )
    path = Path(value)
    return path if path.is_absolute() else SCRIPT_DIR / path


def _write_json_atomically(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        mode="w", encoding="utf-8", dir=path.parent, prefix=f".{path.name}.", delete=False
    ) as temporary:
        json.dump(payload, temporary, ensure_ascii=False, sort_keys=True)
        temporary.write("\n")
        temporary_path = Path(temporary.name)
    temporary_path.replace(path)


def select_next_query_entry(conf: dict) -> dict[str, Any]:
    """Return the next configured query and atomically persist the next position."""
    entries = list(conf_get(conf, "search", "queries", DEFAULT_SEARCH_QUERIES) or [])
    if not entries:
        raise ValueError("conf.yaml 的 search.queries 不能为空")
    state_path = resolve_search_state_path(conf)
    try:
        state = json.loads(state_path.read_text(encoding="utf-8")) if state_path.is_file() else {}
    except (OSError, json.JSONDecodeError):
        state = {}
    next_index = int(state.get("next_query_index", 0)) % len(entries)
    entry = entries[next_index]
    if isinstance(entry, str):
        normalized = {"query": entry}
    elif isinstance(entry, dict) and isinstance(entry.get("query"), str) and entry["query"].strip():
        normalized = dict(entry)
    else:
        raise ValueError(f"search.queries 条目格式错误: {entry!r}")
    _write_json_atomically(
        state_path,
        {"next_query_index": (next_index + 1) % len(entries), "last_query": normalized["query"]},
    )
    return normalized


def select_rotating_entry(conf: dict) -> dict:
    """Backward-compatible name for persisted query rotation."""
    return select_next_query_entry(conf)


def select_rotating_query(conf: dict) -> str:
    """兼容旧调用：仅返回轮换查询词。"""
    return select_rotating_entry(conf)["query"]


def apply_proxy(conf: dict) -> None:
    """Environment (including empty/direct) wins over YAML; normalize aliases.

    Precedence: HTTPS_PROXY, https_proxy, HTTP_PROXY, http_proxy,
    ALL_PROXY, all_proxy, then download.proxy. NO_PROXY stays untouched.
    """
    keys = ("HTTPS_PROXY", "https_proxy", "HTTP_PROXY", "http_proxy", "ALL_PROXY", "all_proxy")
    proxy = next((os.environ[key] for key in keys if key in os.environ), None)
    if proxy is None:
        proxy = str(conf_get(conf, "download", "proxy", "") or "").strip()
    for key in keys:
        os.environ[key] = proxy


def resolve_keyframes_dir(conf: dict) -> Path:
    value = str(conf_get(conf, "download", "keyframes_dir", "downloads") or "downloads")
    path = Path(value)
    return path if path.is_absolute() else SCRIPT_DIR / path


def resolve_whisper_model_cache(conf: dict) -> Path:
    value = str(
        conf_get(conf, "whisper", "model_cache", "model-cache/whisper") or "model-cache/whisper"
    )
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


def classify_failure(error: str | None) -> str:
    """Classify known collection failures for operator-visible run summaries."""
    message = (error or "").casefold()
    if any(token in message for token in ("字幕", "caption", "webvtt", "http 429")):
        return "caption_failure"
    if any(token in message for token in ("关键帧", "视频流", "ffmpeg", "temporary stream")):
        return "temporary_stream_failure"
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
    max_results = max_results or int(search.get("max_results", 25))
    success_target = (
        video_limit
        if video_limit is not None
        else int(search.get("success_target", 1))
    )
    max_pages = int(search.get("max_pages", 3))
    if success_target < 1:
        raise ValueError("success_target/video_limit 必须至少为 1")
    if max_pages < 1:
        raise ValueError("search.max_pages 必须至少为 1")
    language = language or str(search.get("language", "en"))
    order = order or str(search.get("order", "relevance"))
    caption_only = search.get("caption_only", True) if caption_only is None else caption_only
    whisper_fallback = (
        search.get("whisper_fallback", False) if whisper_fallback is None else whisper_fallback
    )
    retry_attempts = int(conf_get(conf, "retry", "attempts", 2) or 2)
    retry_delay = float(conf_get(conf, "retry", "delay_seconds", 3.0) or 3.0)
    keyframes_dir = resolve_keyframes_dir(conf)
    whisper_model = str(conf_get(conf, "whisper", "model", "small") or "small")
    whisper_device = str(conf_get(conf, "whisper", "device", "auto") or "auto")
    whisper_model_cache = resolve_whisper_model_cache(conf)

    apply_proxy(conf)
    load_global_env()
    existing = load_existing_dedup_keys()
    results: list[dict[str, Any]] = []
    seen_urls: set[str] = set()
    page_token: str | None = None
    pages_searched = 0
    candidates_seen = 0
    search_error: str | None = None
    while (
        pages_searched < max_pages
        and sum(r["outcome"] == "success" for r in results) < success_target
    ):
        try:
            page = search_youtube_videos(
                query,
                api_key=load_api_key(),
                max_results=max_results,
                order=order,
                caption_only=caption_only,
                page_token=page_token,
            )
        except SearchError as exc:
            search_error = str(exc)
            break
        pages_searched += 1
        for candidate in page.get("items") or []:
            if sum(r["outcome"] == "success" for r in results) >= success_target:
                break
            url = candidate["canonical_url"]
            candidates_seen += 1
            if url in seen_urls or f"video-evidence:{url}" in existing:
                results.append(
                    {
                        "video_id": candidate["video_id"],
                        "canonical_url": url,
                        "outcome": "duplicate",
                    }
                )
                continue
            seen_urls.add(url)
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
                        "outcome": classify_failure(last_error),
                        "error": last_error,
                    }
                )
        page_token = page.get("next_page_token")
        if not page_token:
            break

    outcome_counts = {
        outcome: sum(1 for result in results if result["outcome"] == outcome)
        for outcome in (
            "duplicate",
            "no_captions",
            "caption_failure",
            "temporary_stream_failure",
            "failed",
            "success",
        )
    }

    return {
        "status": "failed" if search_error and not results else "success",
        "query": query,
        "success_target": success_target,
        "pages_searched": pages_searched,
        "candidates_seen": candidates_seen,
        "success_count": outcome_counts["success"],
        "outcome_counts": outcome_counts,
        **({"error": search_error} if search_error else {}),
        "results": results,
    }


def load_conf_with_env(config_path: Path) -> dict:
    """加载 conf.yaml 并载入全局 .env（供各模块使用）。"""
    load_global_env()
    if config_path == SCRIPT_DIR / "conf.yaml" and not config_path.is_file():
        return default_conf()
    return load_conf(config_path)


def emit_json(payload: dict[str, Any]) -> None:
    print(json.dumps(payload, ensure_ascii=False, indent=2))
