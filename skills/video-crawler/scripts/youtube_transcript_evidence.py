#!/usr/bin/env python3
"""Collect YouTube transcript evidence without persisting media files.

The script prefers public captions. If no public caption exists, it creates a
short-lived local audio file for local Whisper transcription. Optional keyframes
are emitted by ffmpeg directly into process memory, never to disk.
"""

from __future__ import annotations

import argparse
import base64
import json
import os
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


@dataclass(frozen=True)
class Keyframe:
    timestamp_seconds: float
    jpeg_bytes: bytes


@dataclass(frozen=True)
class VlmConfig:
    base_url: str
    api_key: str
    model: str


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


def plan_keyframe_timestamps(duration_seconds: int | float, frame_count: int) -> list[float]:
    """Place keyframes evenly inside the video, excluding both endpoints."""
    duration = float(duration_seconds)
    if duration <= 0 or frame_count <= 0:
        return []
    return [round(duration * index / (frame_count + 1), 3) for index in range(1, frame_count + 1)]


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


def _resolve_video_stream_url(video_url: str) -> str:
    try:
        import yt_dlp
    except ImportError as exc:
        raise EvidenceCollectionError("需要安装 yt-dlp 才能提取临时关键帧") from exc
    options: dict[str, object] = {
        "format": "best[height<=480][ext=mp4]/best[height<=480]/best",
        "skip_download": True,
        "noplaylist": True,
        "quiet": True,
        "no_warnings": True,
    }
    try:
        with yt_dlp.YoutubeDL(options) as ydl:
            info = ydl.extract_info(video_url, download=False)
    except Exception as exc:
        message = f"yt-dlp 无法解析临时视频流（{type(exc).__name__}）"
        raise EvidenceCollectionError(message) from exc
    if not isinstance(info, dict) or not isinstance(info.get("url"), str):
        raise EvidenceCollectionError("yt-dlp 未返回临时视频流")
    media_url = str(info["url"])
    if urlparse(media_url).scheme not in {"http", "https"}:
        raise EvidenceCollectionError("yt-dlp 返回的视频流不是 HTTP(S) 地址")
    return media_url


def extract_keyframes_as_jpegs(
    video_url: str, *, duration_seconds: int | float, frame_count: int
) -> list[Keyframe]:
    """Extract evenly distributed JPEGs into memory without writing video or frames to disk."""
    if not shutil.which("ffmpeg"):
        raise EvidenceCollectionError("需要安装 ffmpeg 才能提取关键帧")
    timestamps = plan_keyframe_timestamps(duration_seconds, frame_count)
    if not timestamps:
        return []
    media_url = _resolve_video_stream_url(video_url)
    frames: list[Keyframe] = []
    for timestamp in timestamps:
        command = [
            "ffmpeg",
            "-nostdin",
            "-loglevel",
            "error",
            "-ss",
            str(timestamp),
            "-i",
            media_url,
            "-frames:v",
            "1",
            "-vf",
            "scale='min(768,iw)':-2",
            "-q:v",
            "4",
            "-f",
            "image2pipe",
            "-vcodec",
            "mjpeg",
            "pipe:1",
        ]
        try:
            result = subprocess.run(command, check=True, capture_output=True, timeout=90)
        except (OSError, subprocess.SubprocessError) as exc:
            raise EvidenceCollectionError(f"关键帧提取失败（{type(exc).__name__}）") from exc
        jpeg = result.stdout
        if not jpeg.startswith(b"\xff\xd8") or not jpeg.endswith(b"\xff\xd9"):
            raise EvidenceCollectionError("ffmpeg 未返回有效 JPEG 关键帧")
        frames.append(Keyframe(timestamp_seconds=timestamp, jpeg_bytes=jpeg))
    return frames


def add_keyframe_summary(evidence: dict[str, Any], video_url: str) -> dict[str, Any]:
    """Attach non-persistent keyframe metadata while retaining JPEG bytes only in process memory."""
    duration = evidence.get("duration_seconds")
    if not isinstance(duration, int | float) or duration <= 0:
        raise EvidenceCollectionError("缺少有效视频时长，无法规划关键帧")
    frames = extract_keyframes_as_jpegs(
        video_url,
        duration_seconds=duration,
        frame_count=caption_frame_limit(duration),
    )
    evidence["keyframes"] = [
        {"timestamp_seconds": frame.timestamp_seconds, "byte_size": len(frame.jpeg_bytes)}
        for frame in frames
    ]
    return evidence


def load_vlm_config(env_file: Path = Path(".env")) -> VlmConfig:
    """Read required VLM settings from environment, falling back to a local .env file."""
    values: dict[str, str] = {}
    if env_file.is_file():
        for raw_line in env_file.read_text(encoding="utf-8").splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            values[key.strip()] = value.strip().strip("\"'")
    resolved = {
        "VLM_BASE_URL": (
            os.environ.get("VLM_BASE_URL")
            or values.get("VLM_BASE_URL")
            or "https://api.siliconflow.cn/v1"
        ).strip(),
        "VLM_API_KEY": (os.environ.get("VLM_API_KEY") or values.get("VLM_API_KEY", "")).strip(),
        "VLM_MODEL": (
            os.environ.get("VLM_MODEL")
            or values.get("VLM_MODEL")
            or "Qwen/Qwen3-Omni-30B-A3B-Instruct"
        ).strip(),
    }
    if not resolved["VLM_API_KEY"]:
        raise EvidenceCollectionError(".env 中必须配置 VLM_API_KEY")
    return VlmConfig(
        base_url=resolved["VLM_BASE_URL"],
        api_key=resolved["VLM_API_KEY"],
        model=resolved["VLM_MODEL"],
    )


def build_video_summary_payload(
    evidence: dict[str, Any], keyframes: list[Keyframe], *, model: str
) -> dict[str, Any]:
    """Build an OpenAI-compatible image+transcript request with no external media URLs."""
    transcript_lines: list[str] = []
    for segment in evidence.get("segments", []):
        if not isinstance(segment, dict) or not isinstance(segment.get("text"), str):
            continue
        timestamp = segment.get("start_seconds", 0)
        transcript_lines.append(f"[{timestamp}s] {segment['text'].strip()}")
    transcript = "\n".join(line for line in transcript_lines if line)[:16000]
    if not transcript:
        raise EvidenceCollectionError("缺少真实字幕或 ASR 文本，不能生成视频摘要")
    title = str(evidence.get("title") or "未命名视频")
    content: list[dict[str, Any]] = [
        {
            "type": "text",
            "text": (
                "请根据下方真实转写文本和关键帧生成简洁、客观的中文视频摘要。"
                "只能陈述字幕或画面直接支持的信息；不确定时说明无法确认，"
                "不要用标题、简介或常识补充为视频事实。\n\n"
                f"视频标题（仅供识别，不能作为证据）：{title}\n"
                f"真实转写：\n{transcript}"
            ),
        }
    ]
    for frame in keyframes:
        encoded = base64.b64encode(frame.jpeg_bytes).decode("ascii")
        content.append(
            {
                "type": "image_url",
                "image_url": {"url": f"data:image/jpeg;base64,{encoded}"},
            }
        )
    return {"model": model, "messages": [{"role": "user", "content": content}]}


def summarize_video_evidence_with_vlm(
    evidence: dict[str, Any],
    keyframes: list[Keyframe],
    *,
    base_url: str,
    api_key: str,
    model: str,
    post: Any = None,
) -> str:
    """Submit real transcript and inline keyframes to an OpenAI-compatible VLM."""
    if not base_url or not api_key or not model:
        raise EvidenceCollectionError("VLM_BASE_URL、VLM_API_KEY 和 VLM_MODEL 必须配置")
    if post is None:
        try:
            import requests
        except ImportError as exc:
            raise EvidenceCollectionError("需要安装 requests 才能调用 VLM") from exc
        post = requests.post
    payload = build_video_summary_payload(evidence, keyframes, model=model)
    try:
        response = post(
            f"{base_url.rstrip('/')}/chat/completions",
            headers={"Authorization": f"Bearer {api_key}"},
            json=payload,
            timeout=120,
        )
        response.raise_for_status()
        data = response.json()
    except Exception as exc:
        raise EvidenceCollectionError(f"VLM 视频摘要请求失败（{type(exc).__name__}）") from exc
    summary = (data.get("choices") or [{}])[0].get("message", {}).get("content", "")
    if not isinstance(summary, str) or not summary.strip():
        raise EvidenceCollectionError("VLM 未返回有效视频摘要")
    return summary.strip()


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
            raise EvidenceCollectionError(f"临时音频提取失败（{type(exc).__name__}）") from exc
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


def ingest_video_evidence(evidence: dict[str, Any]) -> dict[str, Any]:
    """Persist a completed transcript/keyframe/VLM evidence record and index its chunks."""
    summary = str(evidence.get("video_summary") or "").strip()
    canonical_url = str(evidence.get("video_page_url") or "").strip()
    if not summary or not canonical_url:
        raise EvidenceCollectionError("入库需要 video_summary 和 video_page_url")
    repository_root = Path(__file__).resolve().parents[3]
    backend_root = repository_root / "backend"
    if str(backend_root) not in sys.path:
        sys.path.insert(0, str(backend_root))
    try:
        from app.core.config import get_settings
        from app.db.base import utcnow
        from app.models.content import ContentChunk, ContentItem, RawPage
        from app.models.control import CrawlJob, CrawlRun, SourceSite
        from app.services.chunking import build_chunks
        from app.services.embeddings import build_embedding_service
        from app.services.retrieval import QdrantIndexer
        from sqlalchemy import create_engine, select
        from sqlalchemy.orm import Session
    except ImportError as exc:
        raise EvidenceCollectionError("项目后端依赖不可用，无法入库") from exc

    settings = get_settings()
    transcript = "\n".join(
        str(segment.get("text") or "").strip()
        for segment in evidence.get("segments", [])
        if isinstance(segment, dict)
    ).strip()
    cleaned_text = f"视频摘要：\n{summary}\n\n真实转写：\n{transcript}".strip()
    content_hash = __import__("hashlib").sha256(cleaned_text.encode("utf-8")).hexdigest()
    with Session(create_engine(settings.sync_database_url)) as session:
        source = session.scalar(
            select(SourceSite).where(SourceSite.name == "YouTube Transcript Evidence")
        )
        if source is None:
            source = SourceSite(
                name="YouTube Transcript Evidence",
                site_type="video_aggregator",
                base_url="https://www.youtube.com",
                allowed_domains=["youtube.com", "youtu.be"],
                fetch_mode="http_api",
                default_language="zh",
                active=True,
                config_json={"kind": "transcript_keyframe_vlm"},
            )
            session.add(source)
            session.flush()
        job = session.scalar(
            select(CrawlJob).where(
                CrawlJob.name == "YouTube Transcript Evidence Ingest",
                CrawlJob.source_site_id == source.id,
            )
        )
        if job is None:
            job = CrawlJob(
                source_site_id=source.id,
                name="YouTube Transcript Evidence Ingest",
                trigger_mode="manual",
                parser_profile="video_transcript_evidence",
                max_pages=1,
                enabled=True,
                seed_config_json={},
                agent_policy_json={"extraction_mode": "transcript_keyframe_vlm"},
            )
            session.add(job)
            session.flush()
        existing = session.scalar(
            select(ContentItem).where(ContentItem.dedup_key == f"video-evidence:{canonical_url}")
        )
        if existing is not None:
            return {"status": "skipped", "reason": "duplicate", "content_id": str(existing.id)}
        run = CrawlRun(
            source_site_id=source.id,
            crawl_job_id=job.id,
            trigger_type="manual",
            execution_mode="transcript_keyframe_vlm",
            seed_url=canonical_url,
            status="running",
            started_at=utcnow(),
        )
        session.add(run)
        session.flush()
        snapshot = {key: value for key, value in evidence.items() if key != "segments"}
        snapshot["transcript_segment_count"] = len(evidence.get("segments", []))
        raw = RawPage(
            source_site_id=source.id,
            crawl_run_id=run.id,
            requested_url=canonical_url,
            final_url=canonical_url,
            http_status=200,
            content_type="application/vnd.youtube-transcript-evidence+json",
            response_headers_json={},
            raw_html=None,
            raw_text=transcript,
            raw_json=snapshot,
            fetched_at=utcnow(),
            fetch_error=None,
            parser_profile="video_transcript_evidence",
            extraction_method="transcript_keyframe_vlm",
            extraction_confidence=None,
            parse_status="parsed",
            parse_error=None,
            body_hash=content_hash,
        )
        session.add(raw)
        session.flush()
        content = ContentItem(
            source_site_id=source.id,
            raw_page_id=raw.id,
            crawl_run_id=run.id,
            item_type="video_description",
            title=str(evidence.get("title") or "YouTube video"),
            canonical_url=canonical_url,
            source_url=canonical_url,
            language="zh",
            raw_text=transcript or None,
            cleaned_text=cleaned_text,
            summary_text=summary,
            structured_by="transcript_keyframe_vlm",
            extraction_confidence=None,
            tags=["video", "youtube", "transcript", "keyframe", "vlm"],
            metadata_json=snapshot,
            content_hash=content_hash,
            dedup_key=f"video-evidence:{canonical_url}",
        )
        session.add(content)
        session.flush()
        chunks = []
        for built in build_chunks(
            item_type="video_description",
            title=content.title,
            cleaned_text=cleaned_text,
            summary_text=summary,
            tags=content.tags,
        ):
            chunk = ContentChunk(
                content_item_id=content.id,
                chunk_index=built.chunk_index,
                char_start=built.start_char,
                char_end=built.end_char,
                display_text=built.display_text,
                embed_text=built.embed_text,
                token_count=built.token_count,
                chunk_metadata_json=built.chunk_metadata_json,
                embed_status="pending",
            )
            session.add(chunk)
            chunks.append(chunk)
        session.flush()
        indexer = QdrantIndexer(
            url=settings.qdrant_url,
            collection=settings.qdrant_collection,
            embedding=build_embedding_service(settings),
        )
        indexer.ensure_collection()
        for chunk in chunks:
            point_id = indexer.upsert_chunk(
                chunk_id=chunk.id,
                embed_text=chunk.embed_text,
                payload={
                    "content_item_id": str(content.id),
                    "video_url": canonical_url,
                    "description_text": summary[:500],
                },
            )
            chunk.vector_backend = indexer.backend_name
            chunk.vector_point_id = point_id
            chunk.embed_status = "success"
            chunk.embedded_at = utcnow()
        run.status = "success"
        run.finished_at = utcnow()
        run.discovered_count = run.fetched_count = run.parsed_count = 1
        run.chunked_count = run.embedded_count = len(chunks)
        session.commit()
        return {"status": "success", "content_id": str(content.id), "chunks": len(chunks)}


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
    parser.add_argument(
        "--extract-keyframes",
        action="store_true",
        help="流式提取关键帧为内存 JPEG；JSON 仅输出时间戳与字节数",
    )
    parser.add_argument(
        "--summarize-with-vlm",
        action="store_true",
        help="将真实字幕/ASR 与内存关键帧提交给 .env 配置的 VLM 生成摘要",
    )
    parser.add_argument(
        "--env-file",
        type=Path,
        default=Path(".env"),
        help="VLM 配置文件路径，默认 .env",
    )
    parser.add_argument(
        "--ingest",
        action="store_true",
        help="将 VLM 视频摘要、转写和关键帧元数据写入 PostgreSQL 并索引至 Qdrant",
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
        keyframes: list[Keyframe] = []
        if args.extract_keyframes or args.summarize_with_vlm:
            duration = result.get("duration_seconds")
            if not isinstance(duration, int | float) or duration <= 0:
                raise EvidenceCollectionError("缺少有效视频时长，无法提取关键帧")
            keyframes = extract_keyframes_as_jpegs(
                args.video_url,
                duration_seconds=duration,
                frame_count=caption_frame_limit(duration),
            )
            result["keyframes"] = [
                {"timestamp_seconds": frame.timestamp_seconds, "byte_size": len(frame.jpeg_bytes)}
                for frame in keyframes
            ]
        if args.summarize_with_vlm:
            config = load_vlm_config(args.env_file)
            result["video_summary"] = summarize_video_evidence_with_vlm(
                result,
                keyframes,
                base_url=config.base_url,
                api_key=config.api_key,
                model=config.model,
            )
        if args.ingest:
            result["ingestion"] = ingest_video_evidence(result)
    except EvidenceCollectionError as exc:
        error = json.dumps({"status": "failed", "error": str(exc)}, ensure_ascii=False)
        print(error, file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
