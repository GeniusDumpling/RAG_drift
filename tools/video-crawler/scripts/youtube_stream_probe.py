#!/usr/bin/env python3
"""诊断 YouTube 视频流（googlevideo.com/videoplayback）下载连通性。

对多个 player client，先用 yt-dlp 提取临时视频流地址，
再带 yt-dlp 提供的 HTTP 头直接发起 Range 请求，报告真实 HTTP 状态码，用于定位
是「请求头缺失」还是「出口 IP / 网络被封锁」。

用法示例：
    uv run python3 tools/video-crawler/scripts/youtube_stream_probe.py \
        --video-url "https://www.youtube.com/watch?v=-395AYO09T0" --json
"""

from __future__ import annotations

import argparse
import json
import os
from typing import Any

import requests

CLIENTS: dict[str, dict[str, Any]] = {
    "web_default": {},
    "android_vr": {"player_client": ["android_vr"]},
    "tv": {"player_client": ["tv"]},
    "ios": {"player_client": ["ios"]},
    "mweb": {"player_client": ["mweb"]},
}


def default_proxy() -> str | None:
    return (
        os.environ.get("HTTPS_PROXY")
        or os.environ.get("https_proxy")
        or os.environ.get("HTTP_PROXY")
        or os.environ.get("http_proxy")
    )


def _is_video_format(fmt: dict[str, Any]) -> bool:
    format_id = str(fmt.get("format_id") or "")
    vcodec = str(fmt.get("vcodec") or "")
    note = str(fmt.get("format_note") or "")
    if format_id.startswith("sb") or "storyboard" in note:
        return False
    if vcodec in ("", "none"):
        return False
    return bool(fmt.get("url"))


def extract_stream(
    url: str, client_args: dict[str, Any], proxy: str | None
) -> dict[str, Any]:
    import yt_dlp

    options: dict[str, Any] = {
        "skip_download": True,
        "noplaylist": True,
        "quiet": True,
        "no_warnings": True,
    }
    if client_args:
        options["extractor_args"] = {"youtube": client_args}
    if proxy:
        options["proxy"] = proxy
    try:
        with yt_dlp.YoutubeDL(options) as ydl:
            info = ydl.extract_info(url, download=False)
    except Exception as exc:
        return {"ok": False, "error": f"extract: {type(exc).__name__}: {exc}"}
    formats = info.get("formats") or [] if isinstance(info, dict) else []
    video_formats = [f for f in formats if isinstance(f, dict) and _is_video_format(f)]
    candidate = next(
        (f for f in video_formats if str(f.get("ext")) == "mp4" and str(f.get("acodec") or "") != "none"),
        video_formats[0] if video_formats else None,
    )
    if candidate is None:
        return {"ok": False, "error": "no video stream in formats"}
    return {
        "ok": True,
        "stream_url": str(candidate["url"]),
        "format_id": str(candidate.get("format_id")),
        "http_headers": {k: v for k, v in (candidate.get("http_headers") or {}).items()},
    }


def probe_stream(stream_url: str, http_headers: dict[str, str], proxy: str | None) -> dict[str, Any]:
    headers = dict(http_headers or {})
    headers.setdefault("Range", "bytes=0-65535")
    proxies = {"http": proxy, "https": proxy} if proxy else None
    try:
        resp = requests.get(stream_url, headers=headers, proxies=proxies, timeout=20, stream=True)
        try:
            chunk = next(resp.iter_content(16384), b"")
        except Exception:
            chunk = b""
        return {"ok": resp.status_code in (200, 206), "status_code": resp.status_code, "bytes": len(chunk)}
    except Exception as exc:
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--video-url", default="https://www.youtube.com/watch?v=-395AYO09T0")
    parser.add_argument("--clients", default=",".join(CLIENTS), help="逗号分隔的 client 列表")
    parser.add_argument("--proxy", default=default_proxy(), help="代理地址，默认读 HTTPS_PROXY；传空串关闭")
    parser.add_argument("--json", action="store_true", help="输出 JSON（默认）")
    args = parser.parse_args(argv)

    proxy = args.proxy or None
    results: list[dict[str, Any]] = []
    for client_name in args.clients.split(","):
        client_name = client_name.strip()
        if client_name not in CLIENTS:
            continue
        client_args = CLIENTS[client_name]
        extracted = extract_stream(args.video_url, client_args, proxy)
        if not extracted.get("ok"):
            results.append(
                {"client": client_name, "extract_ok": False, "error": extracted.get("error")}
            )
            continue
        probe = probe_stream(extracted["stream_url"], extracted["http_headers"], proxy)
        results.append(
            {
                "client": client_name,
                "format_id": extracted.get("format_id"),
                "extract_ok": True,
                **probe,
            }
        )

    summary = {
        "video_url": args.video_url,
        "proxy": proxy,
        "results": results,
        "success_count": sum(1 for r in results if r.get("ok")),
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())