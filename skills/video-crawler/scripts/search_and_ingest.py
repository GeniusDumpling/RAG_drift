#!/usr/bin/env python3
"""Search YouTube for short drone videos (≤3min), ingest top N into RAG."""

from __future__ import annotations

import argparse
import json
import logging
import os
import random
import subprocess
import time
from pathlib import Path

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("search_and_ingest")

ROOT = Path(__file__).resolve().parents[3]
SKILL_DIR = ROOT / "skills" / "video-crawler"
VENV_PYTHON = ROOT / ".venv" / "bin" / "python"
FETCH_SCRIPT = SKILL_DIR / "scripts" / "video_fetch.py"
CACHE_FILE = SKILL_DIR / "downloads" / ".ingested_urls.json"
COOKIES = SKILL_DIR / "cookies_www.youtube.com.txt"
PUBLIC_MEDIA_BASE = "https://respect-infringement-phantom-consumer.trycloudflare.com"

SEARCH_QUERIES = [
    "无人机 开箱 评测", "无人机 飞行 测试", "无人机 对比",
    "无人机 新手 推荐", "DJI 无人机 体验",
    "drone unboxing review", "drone flight test",
    "best drone 2025", "mini drone review", "drone beginners guide",
]

HTTP_PROXY = "http://192.168.78.1:7890"


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--max-videos", type=int, default=5)
    p.add_argument("--max-duration", type=int, default=180)
    p.add_argument("--json", action="store_true")
    return p.parse_args()


def run_ytdlp_code(code: str, timeout: int = 30) -> str:
    env = os.environ.copy()
    env["http_proxy"] = HTTP_PROXY
    env["https_proxy"] = HTTP_PROXY
    env["no_proxy"] = ""
    cmd = [str(VENV_PYTHON), "-c", code]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, env=env)
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip()[:300])
    return result.stdout.strip()


def search_youtube(query: str, max_results: int = 10) -> list[dict]:
    safe_q = json.dumps(query)
    code = f"""
import json, yt_dlp
opts = {{'quiet': True, 'no_warnings': True, 'extract_flat': 'in_playlist',
         'default_search': 'ytsearch{max_results}', 'playlistend': {max_results}}}
with yt_dlp.YoutubeDL(opts) as ydl:
    info = ydl.extract_info('ytsearch{max_results}:{safe_q}', download=False)
    entries = info.get('entries') or []
    results = [{{'id': e['id'], 'title': e.get('title'), 'duration': e.get('duration'),
                 'url': f'https://www.youtube.com/watch?v={{e[\"id\"]}}'}}
               for e in entries if e]
    print(json.dumps(results, ensure_ascii=False))
"""
    try:
        return json.loads(run_ytdlp_code(code, timeout=30))
    except Exception as exc:
        logger.warning("搜索 '%s' 失败: %s", query, exc)
        return []


def load_ingested_urls() -> set[str]:
    if CACHE_FILE.exists():
        try:
            return set(json.loads(CACHE_FILE.read_text()).get("urls", []))
        except (json.JSONDecodeError, KeyError):
            pass
    return set()


def save_ingested_url(url: str) -> None:
    ingested = load_ingested_urls()
    ingested.add(url)
    CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
    CACHE_FILE.write_text(json.dumps({"urls": sorted(ingested)}, ensure_ascii=False, indent=2))


def ingest_video(url: str, title: str) -> dict:
    """Run video_fetch.py with multiple retry strategies and exponential backoff.
    Strategies in order: proxy+download, proxy+resolve, no-proxy+download, no-proxy+resolve.
    """
    strategies = [
        (True, True),   # proxy + download-for-vlm (最可能成功)
        (True, False),  # proxy + resolve-only
        (False, True),  # no proxy + download-for-vlm
        (False, False), # no proxy + resolve-only
    ]
    last_error = ""

    for strat_idx, (use_proxy, download_mode) in enumerate(strategies):
        for attempt in range(3):  # 3 attempts per strategy
            env = os.environ.copy()
            if use_proxy:
                env["http_proxy"] = HTTP_PROXY
                env["https_proxy"] = HTTP_PROXY
            else:
                for k in ("http_proxy", "https_proxy", "HTTP_PROXY", "HTTPS_PROXY"):
                    env.pop(k, None)
            env["no_proxy"] = "localhost,127.0.0.1,::1,postgres,qdrant"

            cmd = [
                str(VENV_PYTHON), str(FETCH_SCRIPT),
                "--video-url", url,
                "--title", (title or "Crawled Drone Video")[:120],
                "--source", "youtube",
                "--json",
            ]
            if download_mode:
                cmd += ["--download-for-vlm", "--public-media-base-url", PUBLIC_MEDIA_BASE]
            if COOKIES.exists():
                cmd += ["--cookies", str(COOKIES)]

            try:
                result = subprocess.run(
                    cmd, capture_output=True, text=True, timeout=300, env=env
                )
                if result.returncode == 0:
                    output = json.loads(result.stdout.strip())
                    if output.get("status") == "success":
                        return {
                            "url": url, "title": title,
                            "status": "success",
                            "content_id": output.get("content_id"),
                            "chunks": output.get("chunks", 0),
                            "mode": "download" if download_mode else "resolve",
                        }
                err_lines = result.stderr.strip().split("\n")[-3:]
                last_error = "; ".join(l for l in err_lines if l)[:200]
            except subprocess.TimeoutExpired:
                last_error = "timeout"
            except json.JSONDecodeError:
                last_error = "JSON parse error"
                break

            # Exponential backoff: 5s, 10s, 20s between attempts
            backoff = min(5 * (2 ** attempt), 20)
            time.sleep(backoff)

    return {"url": url, "title": title, "status": "failed", "error": last_error}


def main() -> None:
    args = parse_args()
    queries = list(SEARCH_QUERIES)
    random.shuffle(queries)

    all_candidates: list[dict] = []
    seen_ids: set[str] = set()

    for query in queries:
        if len(all_candidates) >= args.max_videos * 5:
            break
        results = search_youtube(query)
        for r in results:
            vid = r.get("id")
            if not vid or vid in seen_ids:
                continue
            seen_ids.add(vid)
            dur = r.get("duration")
            if dur is not None and 0 < dur <= args.max_duration:
                all_candidates.append(r)
        time.sleep(0.5)

    logger.info("候选视频 %d 个（≤%ds）", len(all_candidates), args.max_duration)
    ingested = load_ingested_urls()
    fresh = [v for v in all_candidates if v["url"] not in ingested]
    logger.info("未处理: %d 个", len(fresh))

    if not fresh:
        msg = "没有新的可处理视频"
        if args.json:
            print(json.dumps({"status": "skipped", "reason": msg}, ensure_ascii=False))
        else:
            logger.info(msg)
        return

    selected = sorted(fresh, key=lambda v: v["duration"])[: args.max_videos]
    logger.info("尝试处理 %d 个", len(selected))

    results: list[dict] = []
    for idx, video in enumerate(selected, 1):
        title = video.get("title", "")
        dur = video.get("duration", "?")
        logger.info("[%d/%d] %s (%ss)", idx, len(selected), title[:60], dur)
        r = ingest_video(video["url"], title)
        results.append(r)
        if r.get("status") == "success" and r.get("content_id"):
            save_ingested_url(video["url"])
            logger.info("  ✅ content_id=%s, chunks=%s", r["content_id"][:8], r.get("chunks"))
        else:
            logger.warning("  ❌ %s", r.get("error", "unknown")[:120])
        time.sleep(1)

    ok = sum(1 for r in results if r.get("status") == "success")
    out = {
        "status": "completed",
        "candidates_found": len(all_candidates),
        "fresh_candidates": len(fresh),
        "attempted": len(selected),
        "succeeded": ok,
        "failed": len(selected) - ok,
        "results": results,
    }
    if args.json:
        print(json.dumps(out, ensure_ascii=False, indent=2))
    else:
        logger.info("完成: %d/%d 成功", ok, len(selected))


if __name__ == "__main__":
    main()
