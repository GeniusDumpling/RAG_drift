#!/usr/bin/env python3
"""Collect public YouTube metadata and caption evidence without storing video media.

This script deliberately never requests a video format, never downloads video/audio,
and never exposes media through a public URL. It produces a JSON evidence record that
can be passed to a later RAG-ingestion or multimodal-summary stage.
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse
from urllib.request import Request, urlopen

YOUTUBE_HOSTS = {"youtube.com", "www.youtube.com", "m.youtube.com", "youtu.be"}
_TIMESTAMP_RE = re.compile(
    r"^(?:(?P<hours>\d{1,2}):)?(?P<minutes>\d{2}):(?P<seconds>\d{2})[.,](?P<millis>\d{3})$"
)
_TAG_RE = re.compile(r"<[^>]+>")


class EvidenceCollectionError(RuntimeError):
    """Raised when public YouTube metadata or captions cannot be collected."""


@dataclass(frozen=True)
class CaptionSegment:
    start_seconds: float
    end_seconds: float
    text: str


def normalize_youtube_url(url: str) -> tuple[str, str]:
    """Return a stable watch URL and video ID for a public YouTube URL."""
    parsed = urlparse(url.strip())
    if parsed.scheme not in {"http", "https"} or parsed.netloc.lower() not in YOUTUBE_HOSTS:
        raise EvidenceCollectionError("只接受公开的 YouTube HTTP(S) 视频 URL")

    host = parsed.netloc.lower()
    if host == "youtu.be":
        video_id = parsed.path.strip("/").split("/", 1)[0]
    else:
        video_id = parse_qs(parsed.query).get("v", [""])[0]
        if not video_id and parsed.path.startswith("/shorts/"):
            video_id = parsed.path.split("/", 3)[2]
    video_id = video_id.strip()
    if not video_id:
        raise EvidenceCollectionError("YouTube URL 缺少视频 ID")
    return f"https://www.youtube.com/watch?v={video_id}", video_id


def caption_frame_limit(duration_seconds: int | float | None) -> int:
    """Return the planned keyframe cap from the transcript/keyframe design."""
    duration = float(duration_seconds or 0)
    if duration <= 5 * 60:
        return 6
    if duration <= 15 * 60:
        return 10
    if duration <= 30 * 60:
        return 16
    if duration <= 60 * 60:
        return 24
    return 32


def choose_caption_track(
    info: dict[str, Any], language: str
) -> tuple[str, str, dict[str, Any]] | None:
    """Prefer public manual captions, then public automatic captions."""
    requested = language.casefold()
    candidates = (requested, requested.split("-", 1)[0])
    sources = (("subtitles", "manual_caption"), ("automatic_captions", "automatic_caption"))
    for source, source_name in sources:
        tracks = info.get(source)
        if not isinstance(tracks, dict):
            continue
        normalized_tracks = {
            key.casefold(): entries for key, entries in tracks.items() if isinstance(key, str)
        }
        for candidate in candidates:
            entries = normalized_tracks.get(candidate)
            if not isinstance(entries, list):
                continue
            for entry in entries:
                if isinstance(entry, dict) and isinstance(entry.get("url"), str):
                    ext = str(entry.get("ext") or "")
                    if ext == "vtt":
                        return source_name, candidate, entry
    return None


def _timestamp_to_seconds(value: str) -> float:
    match = _TIMESTAMP_RE.match(value.strip())
    if not match:
        raise EvidenceCollectionError(f"无法解析字幕时间戳: {value}")
    return (
        int(match.group("hours") or 0) * 3600
        + int(match.group("minutes")) * 60
        + int(match.group("seconds"))
        + int(match.group("millis")) / 1000
    )


def parse_webvtt(text: str) -> list[CaptionSegment]:
    """Parse a WebVTT caption document into clean, timed text segments."""
    segments: list[CaptionSegment] = []
    for block in re.split(r"\n\s*\n", text.replace("\r\n", "\n")):
        lines = [line.strip() for line in block.splitlines() if line.strip()]
        timing_index = next((i for i, line in enumerate(lines) if "-->" in line), None)
        if timing_index is None:
            continue
        timing = lines[timing_index].split("-->", 1)
        start_raw, end_raw = (part.strip().split(" ", 1)[0] for part in timing)
        end_raw = end_raw.split(" ", 1)[0]
        body = " ".join(lines[timing_index + 1 :])
        body = _TAG_RE.sub("", body).replace("&nbsp;", " ")
        body = re.sub(r"\s+", " ", body).strip()
        if not body:
            continue
        start_seconds = _timestamp_to_seconds(start_raw)
        end_seconds = _timestamp_to_seconds(end_raw)
        if end_seconds > start_seconds:
            segments.append(CaptionSegment(start_seconds, end_seconds, body))
    return segments


def _read_caption_text(url: str) -> str:
    request = Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urlopen(request, timeout=30) as response:  # noqa: S310 - URL comes from yt-dlp metadata.
        return response.read().decode("utf-8", errors="replace")


def _resolve_audio_stream_url(video_url: str) -> str:
    try:
        import yt_dlp
    except ImportError as exc:
        raise EvidenceCollectionError("需要安装 yt-dlp 才能提取临时音频") from exc
    options: dict[str, object] = {
        "format": "bestaudio[ext=m4a]/bestaudio/best",
        "skip_download": True,
        "noplaylist": True,
        "quiet": True,
        "no_warnings": True,
    }
    try:
        with yt_dlp.YoutubeDL(options) as ydl:
            info = ydl.extract_info(video_url, download=False)
    except Exception as exc:
        raise EvidenceCollectionError(f"yt-dlp 无法解析临时音频（{type(exc).__name__}）") from exc
    if not isinstance(info, dict) or not isinstance(info.get("url"), str):
        raise EvidenceCollectionError("yt-dlp 未返回临时音频流")
    media_url = str(info["url"])
    if urlparse(media_url).scheme not in {"http", "https"}:
        raise EvidenceCollectionError("yt-dlp 返回的音频流不是 HTTP(S) 地址")
    return media_url


def transcribe_with_local_whisper(
    video_url: str,
    *,
    model_name: str = "small",
    device: str = "auto",
    model_cache_dir: Path = Path("model-cache/whisper"),
) -> list[CaptionSegment]:
    """Temporarily extract mono audio and transcribe it with local faster-whisper.

    The temporary media directory is always removed. Only timestamped transcript text
    is returned; media URLs and temporary file paths are never returned or persisted.
    """
    if not shutil.which("ffmpeg"):
        raise EvidenceCollectionError("需要安装 ffmpeg 才能在无字幕时提取临时音频")
    try:
        from faster_whisper import WhisperModel
    except ImportError as exc:
        raise EvidenceCollectionError(
            "需要安装 faster-whisper 并下载本地模型才能在无字幕时转写"
        ) from exc

    audio_stream_url = _resolve_audio_stream_url(video_url)
    selected_device = device
    if selected_device == "auto":
        selected_device = "cuda" if shutil.which("nvidia-smi") else "cpu"
    compute_type = "float16" if selected_device == "cuda" else "int8"
    model_cache_dir.mkdir(parents=True, exist_ok=True)
    try:
        model = WhisperModel(
            model_name,
            device=selected_device,
            compute_type=compute_type,
            download_root=str(model_cache_dir),
        )
    except Exception as exc:
        message = f"本地 Whisper 模型初始化失败（{type(exc).__name__}）"
        raise EvidenceCollectionError(message) from exc

    with tempfile.TemporaryDirectory(prefix="intelligence-rag-whisper-") as temp_dir:
        audio_path = Path(temp_dir) / "audio.m4a"
        command = [
            "ffmpeg",
            "-nostdin",
            "-y",
            "-loglevel",
            "error",
            "-i",
            audio_stream_url,
            "-vn",
            "-ac",
            "1",
            "-ar",
            "16000",
            "-b:a",
            "32k",
            str(audio_path),
        ]
        try:
            subprocess.run(command, check=True, capture_output=True, timeout=300)
        except (OSError, subprocess.SubprocessError) as exc:
            raise EvidenceCollectionError(
                f"临时音频提取失败（{type(exc).__name__}）"
            ) from exc
        try:
            segments, _info = model.transcribe(str(audio_path), vad_filter=True)
            result = [
                CaptionSegment(float(segment.start), float(segment.end), segment.text.strip())
                for segment in segments
                if segment.text.strip() and float(segment.end) > float(segment.start)
            ]
        except Exception as exc:
            raise EvidenceCollectionError(f"本地 Whisper 转写失败（{type(exc).__name__}）") from exc
    if not result:
        raise EvidenceCollectionError("本地 Whisper 未生成有效转写")
    return result


def collect_public_evidence(
    video_url: str,
    language: str = "zh",
    *,
    allow_whisper_fallback: bool = True,
    whisper_model: str = "small",
    whisper_device: str = "auto",
    whisper_model_cache: Path = Path("model-cache/whisper"),
) -> dict[str, Any]:
    """Fetch metadata and a public caption track without downloading any media."""
    canonical_url, expected_video_id = normalize_youtube_url(video_url)
    try:
        import yt_dlp
    except ImportError as exc:
        raise EvidenceCollectionError("需要安装 yt-dlp 才能获取 YouTube 元数据") from exc

    options: dict[str, object] = {
        "skip_download": True,
        "noplaylist": True,
        "quiet": True,
        "no_warnings": True,
        "extract_flat": False,
    }
    try:
        with yt_dlp.YoutubeDL(options) as ydl:
            info = ydl.extract_info(canonical_url, download=False)
    except Exception as exc:
        message = f"yt-dlp 无法获取公开视频元数据（{type(exc).__name__}）"
        raise EvidenceCollectionError(message) from exc
    if not isinstance(info, dict) or info.get("_type") == "playlist":
        raise EvidenceCollectionError("仅支持单个公开视频")

    video_id = str(info.get("id") or expected_video_id).strip()
    if video_id != expected_video_id:
        raise EvidenceCollectionError("yt-dlp 返回的视频 ID 与输入 URL 不一致")
    track = choose_caption_track(info, language)
    if track is None:
        if not allow_whisper_fallback:
            return {
                "status": "no_public_captions",
                "video_page_url": canonical_url,
                "video_id": video_id,
                "title": str(info.get("title") or video_id),
                "description": str(info.get("description") or ""),
                "channel": str(info.get("channel") or info.get("uploader") or ""),
                "duration_seconds": info.get("duration"),
                "caption_source": None,
                "caption_language": None,
                "segments": [],
                "planned_keyframe_limit": caption_frame_limit(info.get("duration")),
                "media_downloaded": False,
                "next_step": "install_and_configure_local_whisper",
            }
        segments = transcribe_with_local_whisper(
            canonical_url,
            model_name=whisper_model,
            device=whisper_device,
            model_cache_dir=whisper_model_cache,
        )
        return {
            "status": "success",
            "video_page_url": canonical_url,
            "video_id": video_id,
            "title": str(info.get("title") or video_id),
            "description": str(info.get("description") or ""),
            "channel": str(info.get("channel") or info.get("uploader") or ""),
            "duration_seconds": info.get("duration"),
            "caption_source": "local_whisper",
            "caption_language": None,
            "segments": [segment.__dict__ for segment in segments],
            "planned_keyframe_limit": caption_frame_limit(info.get("duration")),
            "media_downloaded": False,
            "next_step": "optional_inline_keyframe_analysis",
        }

    caption_source, caption_language, entry = track
    ext = str(entry.get("ext") or "")
    if ext != "vtt":
        raise EvidenceCollectionError(f"当前脚本只支持 WebVTT 公开字幕，获得格式: {ext}")
    segments = parse_webvtt(_read_caption_text(str(entry["url"])))
    if not segments:
        raise EvidenceCollectionError("公开字幕为空或无法解析")
    return {
        "status": "success",
        "video_page_url": canonical_url,
        "video_id": video_id,
        "title": str(info.get("title") or video_id),
        "description": str(info.get("description") or ""),
        "channel": str(info.get("channel") or info.get("uploader") or ""),
        "duration_seconds": info.get("duration"),
        "caption_source": caption_source,
        "caption_language": caption_language,
        "segments": [segment.__dict__ for segment in segments],
        "planned_keyframe_limit": caption_frame_limit(info.get("duration")),
        "media_downloaded": False,
        "next_step": "optional_inline_keyframe_analysis",
    }


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--video-url", required=True, help="公开 YouTube 视频 URL")
    parser.add_argument("--language", default="zh", help="字幕语言优先级，默认 zh")
    parser.add_argument(
        "--no-whisper-fallback",
        action="store_true",
        help="无公开字幕时不运行本地 Whisper，直接输出 no_public_captions",
    )
    parser.add_argument("--whisper-model", default="small", help="faster-whisper 模型，默认 small")
    parser.add_argument(
        "--whisper-device",
        choices=("auto", "cuda", "cpu"),
        default="auto",
        help="Whisper 推理设备，默认自动选择",
    )
    parser.add_argument(
        "--whisper-model-cache",
        type=Path,
        default=Path("model-cache/whisper"),
        help="本地 Whisper 模型缓存目录",
    )
    parser.add_argument("--json", action="store_true", help="输出 JSON（默认）")
    return parser.parse_args(argv)


def main() -> int:
    args = parse_args()
    try:
        result = collect_public_evidence(
            args.video_url,
            args.language,
            allow_whisper_fallback=not args.no_whisper_fallback,
            whisper_model=args.whisper_model,
            whisper_device=args.whisper_device,
            whisper_model_cache=args.whisper_model_cache,
        )
    except EvidenceCollectionError as exc:
        error = json.dumps({"status": "failed", "error": str(exc)}, ensure_ascii=False)
        print(error, file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
