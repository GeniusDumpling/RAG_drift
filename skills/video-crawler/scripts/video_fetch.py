#!/usr/bin/env python3
"""Fetch a video URL, optionally download via yt-dlp, call VLM to generate description, store into RAG."""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import pathlib
import uuid
from dataclasses import asdict, dataclass
from typing import Any
from urllib.parse import quote, urlparse

import httpx
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker

import app.models  # noqa: F401 — register ORM metadata
from app.core.config import Settings, get_settings
from app.db.base import utcnow
from app.models.content import ContentChunk, ContentItem, RawPage
from app.models.control import CrawlJob, CrawlRun, CrawlRunEvent, SourceSite
from app.services.chunking import build_chunks
from app.services.embeddings import build_embedding_service
from app.services.retrieval import QdrantIndexer

COOKIES_PATH_DEFAULT = str(pathlib.Path(__file__).resolve().parent.parent / "cookies_www.youtube.com.txt")

logger = logging.getLogger(__name__)

VLM_TIMEOUT = 120
DOWNLOAD_DIR = pathlib.Path(__file__).resolve().parent.parent / "downloads"


class VideoResolutionError(RuntimeError):
    """Raised when a video page cannot produce a safe VLM media input."""


@dataclass(frozen=True)
class VideoInput:
    page_url: str
    video_id: str
    title: str
    media_url: str
    duration_seconds: int | None
    extractor: str


@dataclass(frozen=True)
class DownloadedVideoInput:
    video: VideoInput
    local_path: pathlib.Path
    public_url: str
    format_id: str
    ext: str


def _require_http_url(url: str, *, label: str) -> None:
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise VideoResolutionError(f"{label}必须是有效的 HTTP(S) URL")


def build_public_media_url(public_media_base_url: str, local_path: pathlib.Path) -> str:
    """Build the stable media URL that the remote VLM service can fetch."""
    _require_http_url(public_media_base_url, label="公网媒体基础地址")
    filename = quote(local_path.name)
    return f"{public_media_base_url.rstrip('/')}/{filename}"


def _normalize_youtube_page_url(video_url: str, extractor: str, video_id: str) -> str:
    if extractor.casefold() == "youtube":
        return f"https://www.youtube.com/watch?v={video_id}"
    return video_url


def resolve_video_input(
    video_url: str,
    cookies_path: str | None = None,
) -> VideoInput:
    """Resolve a public video page into stable metadata and an HTTPS VLM input URL."""
    _require_http_url(video_url, label="视频页面")
    try:
        import yt_dlp

        options: dict[str, object] = {
            "format": "best[ext=mp4][height<=480]/best[height<=480]/best",
            "quiet": True,
            "no_warnings": True,
            "extract_flat": False,
            "skip_download": True,
            "noplaylist": True,
            "js_runtimes": {"node": {}},
            "remote_components": {"ejs": "github"},
        }
        if cookies_path and pathlib.Path(cookies_path).is_file():
            options["cookiefile"] = cookies_path
        with yt_dlp.YoutubeDL(options) as ydl:
            info = ydl.extract_info(video_url, download=False)
    except VideoResolutionError:
        raise
    except Exception as exc:
        raise VideoResolutionError(
            f"yt-dlp 无法解析视频（{type(exc).__name__}）"
        ) from exc

    if not isinstance(info, dict) or info.get("_type") == "playlist":
        raise VideoResolutionError("仅支持单个视频页面")
    video_id = str(info.get("id") or "").strip()
    title = str(info.get("title") or "").strip()
    media_url = str(info.get("url") or "").strip()
    if not video_id:
        raise VideoResolutionError("yt-dlp 未返回视频 ID")
    if not media_url:
        raise VideoResolutionError("yt-dlp 未返回可供 VLM 读取的媒体地址")
    _require_http_url(media_url, label="媒体地址")

    extractor = str(info.get("extractor_key") or info.get("extractor") or "unknown")
    page_url = _normalize_youtube_page_url(video_url, extractor, video_id)
    raw_duration = info.get("duration")
    duration = int(raw_duration) if isinstance(raw_duration, int | float) else None
    logger.info(
        "yt-dlp 已解析视频: extractor=%s id=%s duration=%s",
        extractor,
        video_id,
        duration,
    )
    return VideoInput(
        page_url=page_url,
        video_id=video_id,
        title=title or video_id,
        media_url=media_url,
        duration_seconds=duration,
        extractor=extractor,
    )


def download_video_input_for_vlm(
    *,
    video_url: str,
    public_media_base_url: str,
    cookies_path: str | None = None,
    download_dir: pathlib.Path = DOWNLOAD_DIR,
) -> DownloadedVideoInput:
    """Download a <=720p single-file media input and expose its configured public URL."""
    _require_http_url(video_url, label="视频页面")
    _require_http_url(public_media_base_url, label="公网媒体基础地址")
    download_dir.mkdir(parents=True, exist_ok=True)
    try:
        import yt_dlp

        safe_seed = hashlib.sha256(video_url.encode("utf-8")).hexdigest()[:16]
        output_template = str(download_dir / f"{safe_seed}.%(ext)s")
        options: dict[str, object] = {
            "format": (
                "18/"
                "best[ext=mp4][height<=480][acodec!=none][vcodec!=none]/"
                "best[height<=480][acodec!=none][vcodec!=none]/"
                "best[ext=mp4][height<=720][acodec!=none][vcodec!=none]/"
                "best[height<=720][acodec!=none][vcodec!=none]/"
                "best[height<=720]/best"
            ),
            "quiet": True,
            "no_warnings": True,
            "noprogress": True,
            "extract_flat": False,
            "noplaylist": True,
            "outtmpl": output_template,
            "restrictfilenames": True,
            "overwrites": True,
            "max_filesize": 200_000_000,
            "js_runtimes": {"node": {}},
            "remote_components": {"ejs": "github"},
        }
        if cookies_path and pathlib.Path(cookies_path).is_file():
            options["cookiefile"] = cookies_path
        with yt_dlp.YoutubeDL(options) as ydl:
            info = ydl.extract_info(video_url, download=True)
            prepared = pathlib.Path(ydl.prepare_filename(info))
    except VideoResolutionError:
        raise
    except Exception as exc:
        raise VideoResolutionError(
            f"yt-dlp 无法下载视频（{type(exc).__name__}）"
        ) from exc

    if not isinstance(info, dict) or info.get("_type") == "playlist":
        raise VideoResolutionError("仅支持单个视频页面")
    actual = (
        prepared
        if prepared.is_file()
        else next(download_dir.glob(f"{safe_seed}.*"), None)
    )
    if actual is None or not actual.is_file():
        raise VideoResolutionError("yt-dlp 下载完成后未找到媒体文件")

    video_id = str(info.get("id") or "").strip()
    if not video_id:
        raise VideoResolutionError("yt-dlp 未返回视频 ID")
    title = str(info.get("title") or video_id).strip()
    extractor = str(info.get("extractor_key") or info.get("extractor") or "unknown")
    raw_duration = info.get("duration")
    duration = int(raw_duration) if isinstance(raw_duration, int | float) else None
    format_id = str(info.get("format_id") or "").strip()
    video = VideoInput(
        page_url=_normalize_youtube_page_url(video_url, extractor, video_id),
        video_id=video_id,
        title=title or video_id,
        media_url=build_public_media_url(public_media_base_url, actual),
        duration_seconds=duration,
        extractor=extractor,
    )
    logger.info(
        "yt-dlp 已下载视频: extractor=%s id=%s duration=%s file=%s",
        extractor,
        video_id,
        duration,
        actual.name,
    )
    return DownloadedVideoInput(
        video=video,
        local_path=actual,
        public_url=video.media_url,
        format_id=format_id,
        ext=actual.suffix.lstrip("."),
    )


def stable_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _get_or_create_source(session: Session, settings: Settings) -> SourceSite:
    name = "Video Crawler (Auto)"
    source = session.scalar(select(SourceSite).where(SourceSite.name == name).limit(1))
    if source is None:
        source = SourceSite(
            name=name,
            site_type="video_aggregator",
            base_url="https://www.youtube.com",
            allowed_domains=["youtube.com", "youtu.be", "bilibili.com", "douyin.com"],
            fetch_mode="http_api",
            default_language="en",
            active=True,
            config_json={"kind": "video_crawler"},
        )
        session.add(source)
        session.flush()
    return source


def _get_or_create_job(session: Session, source: SourceSite) -> CrawlJob:
    name = "Video Crawler Cron"
    job = session.scalar(select(CrawlJob).where(CrawlJob.name == name, CrawlJob.source_site_id == source.id).limit(1))
    if job is None:
        job = CrawlJob(
            source_site_id=source.id,
            name=name,
            trigger_mode="scheduled",
            cron_expr="0 */8 * * *",
            seed_config_json={"kind": "video_crawler"},
            parser_profile="video_crawler",
            max_pages=5,
            enabled=True,
            agent_policy_json={"extraction_mode": "vlm_video_analysis"},
        )
        session.add(job)
        session.flush()
    return job


def describe_video(
    *,
    client: httpx.Client,
    base_url: str,
    api_key: str,
    model: str,
    video_url: str,
) -> str:
    """Call VLM (SiliconFlow-compatible) to generate a Chinese video description."""
    payload = {
        "model": model,
        "messages": [
            {
                "role": "user",
                "content": [
                    {
                        "type": "text",
                        "text": (
                            "# Role\n"
                            "你是一位专业的无人机安全分析师与渗透测试专家（UAV Security Analyst & Penetration Tester）。\n"
                            "你的任务是通过分析以下无人机运行、展示或实验的视频内容，评估并总结该无人机系统（UAS）在物理、通信、导航及操作层面潜在的安全脆弱性（Vulnerabilities）。\n"
                            "\n"
                            "# Reference Framework\n"
                            "评估时，请参考以下无人机安全脆弱性框架，从视频画面的视觉线索中捕捉相关信息：\n"
                            "1. 物理与硬件安全：设备暴露的物理接口（如USB、调试串口）、天线类型与布局、物理防篡改能力。\n"
                            "2. 传感器与导航安全：GPS天线的可见性、是否在强电磁/干扰风险区域飞行、有无冗余导航传感器。\n"
                            "3. 通信与地面站控制（若画面中出现地面站屏幕、FPV画面或遥控器）：遥控信号指示、是否显示明文遥测数据、链路连接状态指标、有无未加密/未授权连接风险。\n"
                            "4. 运行环境安全：操作员与无人机的物理距离、视距（VLOS）情况、操作环境对视线或信号的阻挡。\n"
                            "\n"
                            "# Instructions\n"
                            "请仔细观看并分析视频，完成以下任务：\n"
                            "1. **视频内容描述**：描述你观察到的视频内容。\n"
                            "2. **视觉线索提取**：描述你在视频中观察到的无人机型号、硬件配置、运行环境以及地面控制站（如有）的关键视觉细节。\n"
                            "3. **脆弱性识别与分析**：结合观察到的线索，总结出视频中所展示的安全脆弱性。请重点关注：\n"
                            "   - 是否容易受到 GPS 干扰/欺骗（GPS Jamming/Spoofing）？\n"
                            "   - 无线通信链路（如 MAVlink）是否存在被监听（Eavesdropping）或信号注入（Injection）的风险？\n"
                            "   - 是否存在容易被物理捕获或近距离物理接触的脆弱性？\n"
                            "\n"
                            "# 输出要求\n"
                            "- 用中文回答，500字以内。\n"
                            "- **严格基于视频画面中的实际视觉线索**，不要编造视频中未出现的信息。\n"
                            "- 如果视频中没有足够明显的信息来判断某个脆弱性，请明确说明「视频画面中未观察到，无法判断」。\n"
                            "- 不要假设视频之外的内容（如无人机的品牌、型号、通信协议），只描述实际看到的东西。"
                        ),
                    },
                    {"type": "video_url", "video_url": {"url": video_url}},
                ],
            }
        ],
    }
    response = client.post(
        f"{base_url.rstrip('/')}/chat/completions",
        headers={"Authorization": f"Bearer {api_key}"},
        json=payload,
    )
    response.raise_for_status()
    data = response.json()
    description = (data.get("choices") or [{}])[0].get("message", {}).get("content", "")
    if not description or not description.strip():
        raise ValueError("VLM returned empty description")
    return description.strip()


def analyze_video_only(
    *,
    video_url: str,
    base_url: str,
    api_key: str,
    model: str,
    client: Any,
    cookies_path: str | None = None,
    download_for_vlm: bool = False,
    public_media_base_url: str | None = None,
) -> dict[str, Any]:
    """Resolve and describe one video without initializing persistence services."""
    downloaded_input: DownloadedVideoInput | None = None
    if download_for_vlm:
        if not public_media_base_url:
            raise VideoResolutionError("--download-for-vlm 需要 --public-media-base-url")
        downloaded_input = download_video_input_for_vlm(
            video_url=video_url,
            public_media_base_url=public_media_base_url,
            cookies_path=cookies_path,
        )
        video_input = downloaded_input.video
    else:
        video_input = resolve_video_input(video_url, cookies_path)
    description = describe_video(
        client=client,
        base_url=base_url,
        api_key=api_key,
        model=model,
        video_url=downloaded_input.public_url if downloaded_input else video_input.media_url,
    )
    if not description.strip():
        raise ValueError("VLM returned empty description")
    safe_metadata = asdict(video_input)
    safe_metadata.pop("media_url")
    return {
        "status": "success",
        "video_page_url": safe_metadata.pop("page_url"),
        **safe_metadata,
        "model": model,
        "vlm_input_kind": (
            "downloaded_public_media" if downloaded_input else "resolved_media_url"
        ),
        **(
            {
                "media_filename": downloaded_input.local_path.name,
                "media_public_url": downloaded_input.public_url,
                "media_format_id": downloaded_input.format_id,
                "media_ext": downloaded_input.ext,
            }
            if downloaded_input
            else {}
        ),
        "description": description.strip(),
    }


def ingest_video(
    *,
    video_url: str,
    title: str | None,
    source_name: str,
    source_page_url: str | None = None,
    settings: Settings,
    vlm_http: httpx.Client,
    cookies_path: str | None = None,
    download_for_vlm: bool = False,
    public_media_base_url: str | None = None,
) -> dict[str, Any]:
    """Ingest a single video: resolve URL -> VLM -> chunk -> embed -> store."""
    downloaded_input = None
    if download_for_vlm:
        if not public_media_base_url:
            raise VideoResolutionError("--download-for-vlm 需要 --public-media-base-url")
        downloaded_input = download_video_input_for_vlm(
            video_url=video_url,
            public_media_base_url=public_media_base_url,
            cookies_path=cookies_path,
        )
        video_input = downloaded_input.video
        resolved_url = downloaded_input.public_url
        vlm_input_url = downloaded_input.public_url
        hint = "downloaded_public_media"
    else:
        video_input = resolve_video_input(video_url, cookies_path)
        resolved_url = video_input.media_url
        vlm_input_url = video_input.media_url
        hint = f"{video_input.extractor.lower()}_resolved_media_url"
    logger.info(
        "yt-dlp 已解析入库视频: extractor=%s id=%s duration=%s",
        video_input.extractor,
        video_input.video_id,
        video_input.duration_seconds,
    )

    # VLM description
    description = describe_video(
        client=vlm_http,
        base_url=settings.vlm_base_url,
        api_key=settings.vlm_api_key,
        model=settings.vlm_model,
        video_url=vlm_input_url,
    )

    engine = create_engine(settings.sync_database_url)
    with Session(engine) as session:
        source = _get_or_create_source(session, settings)
        job = _get_or_create_job(session, source)

        # Create run
        run = CrawlRun(
            source_site_id=source.id,
            crawl_job_id=job.id,
            trigger_type="scheduled",
            execution_mode="vlm_video_analysis",
            seed_url=video_url,
            status="running",
            started_at=utcnow(),
        )
        session.add(run)
        session.flush()

        # Create content item
        canonical_url = source_page_url or video_input.page_url
        dedup_key = stable_hash(f"{source.id}:{video_url}:video_description:{description[:100]}")
        existing = session.scalar(select(ContentItem).where(ContentItem.dedup_key == dedup_key).limit(1))
        if existing is not None:
            logger.info("Duplicate video, skipping: %s", video_url)
            run.status = "success"
            run.finished_at = utcnow()
            session.commit()
            return {"status": "skipped", "reason": "duplicate"}

        raw_snapshot = {
            "kind": "video_crawler_vlm_analysis",
            "video_url": video_url,
            "video_page_url": video_input.page_url,
            "video_id": video_input.video_id,
            "video_title": video_input.title,
            "video_duration_seconds": video_input.duration_seconds,
            "video_extractor": video_input.extractor,
            "resolve_hint": hint,
            "vlm_model": settings.vlm_model,
            "description": description,
        }
        raw_page = RawPage(
            source_site_id=source.id,
            crawl_run_id=run.id,
            requested_url=video_url,
            final_url=video_input.page_url,
            http_status=200,
            content_type="application/vnd.video-crawler+json",
            response_headers_json={},
            raw_html=None,
            raw_text=description,
            raw_json=raw_snapshot,
            fetched_at=utcnow(),
            fetch_error=None,
            parser_profile="video_crawler",
            extraction_method="vlm_video_analysis",
            extraction_confidence=None,
            parse_status="parsed",
            parse_error=None,
            body_hash=stable_hash(json.dumps(raw_snapshot, ensure_ascii=False, sort_keys=True)),
        )
        session.add(raw_page)
        session.flush()

        content = ContentItem(
            source_site_id=source.id,
            raw_page_id=raw_page.id,
            crawl_run_id=run.id,
            item_type="video_description",
            title=title or video_input.title,
            canonical_url=canonical_url,
            source_url=video_input.page_url,
            language="zh",
            cleaned_text=description,
            summary_text=description[:240],
            tags=[source_name, "video", "crawled"],
            structured_by="qwen3_omni_video_crawler",
            metadata_json={
                "video_url": video_url,
                "video_page_url": video_input.page_url,
                "video_id": video_input.video_id,
                "video_duration_seconds": video_input.duration_seconds,
                "video_extractor": video_input.extractor,
                "resolved_url": resolved_url,
                "resolve_hint": hint,
                "vlm_input_url": vlm_input_url,
                "source_page_url": source_page_url,
                "source_name": source_name,
                "vlm_model": settings.vlm_model,
            },
            content_hash=stable_hash(description),
            dedup_key=dedup_key,
        )
        session.add(content)
        session.flush()

        # Build chunks
        built_chunks = build_chunks(
            item_type="video_description",
            title=title or "Crawled Video",
            cleaned_text=description,
            summary_text=description[:240],
            tags=[source_name, "video"],
            thread_title=None,
        )

        chunks: list[ContentChunk] = []
        for chunk_data in built_chunks:
            chunk = ContentChunk(
                content_item_id=content.id,
                chunk_index=chunk_data.chunk_index,
                char_start=chunk_data.start_char,
                char_end=chunk_data.end_char,
                display_text=chunk_data.display_text,
                embed_text=chunk_data.embed_text,
                token_count=chunk_data.token_count,
                chunk_metadata_json=chunk_data.chunk_metadata_json,
                embed_status="pending",
            )
            session.add(chunk)
            chunks.append(chunk)

        session.flush()

        # Qdrant index
        embedding = build_embedding_service(settings)
        indexer = QdrantIndexer(
            url=settings.qdrant_url,
            collection=settings.qdrant_collection,
            embedding=embedding,
        )
        try:
            indexer.ensure_collection()
            for chunk in chunks:
                point_id = indexer.upsert_chunk(
                    chunk_id=chunk.id,
                    embed_text=chunk.embed_text,
                    payload={
                        "chunk_id": str(chunk.id),
                        "content_item_id": str(content.id),
                        "video_url": video_url,
                        "description_text": description[:500],
                    },
                )
                chunk.vector_backend = "qdrant"
                chunk.vector_point_id = point_id
                chunk.embed_status = "success"
                chunk.embedded_at = utcnow()
        except Exception as exc:
            for chunk in chunks:
                chunk.embed_status = "failed"
                chunk.embed_error = str(exc)[:500]
            logger.error("Qdrant indexing failed: %s", exc)

        run.status = "success"
        run.finished_at = utcnow()
        run.discovered_count = 1
        run.fetched_count = 1
        run.parsed_count = 1
        run.chunked_count = len(chunks)
        session.commit()

        return {
            "status": "success",
            "content_id": str(content.id),
            "chunks": len(chunks),
            "video_url": video_input.page_url,
            "resolve_hint": hint,
        }





def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--video-url", required=True, help="Video page URL or direct video URL")
    parser.add_argument("--title", default=None, help="Optional video title")
    parser.add_argument(
        "--source",
        default="youtube",
        choices=["youtube", "bilibili", "douyin", "other"],
        help="Video source platform",
    )
    parser.add_argument("--source-page-url", default=None, help="Original page URL if different from video URL")
    parser.add_argument(
        "--cookies",
        default=None,
        help="Path to cookies.txt file (default: auto-detect cookies_www.youtube.com.txt in skill dir)",
    )
    parser.add_argument(
        "--analyze-only",
        action="store_true",
        help="Resolve metadata and call the VLM without writing PostgreSQL or Qdrant",
    )
    parser.add_argument(
        "--download-for-vlm",
        action="store_true",
        help=(
            "In analyze-only mode, download <=720p media locally and send its "
            "public URL to VLM"
        ),
    )
    parser.add_argument(
        "--public-media-base-url",
        default=None,
        help="Public HTTP(S) base URL serving skills/video-crawler/downloads files",
    )
    parser.add_argument("--json", action="store_true", help="Print the safe result as JSON")
    return parser.parse_args()


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    args = parse_args()
    settings = get_settings()

    vlm_http = httpx.Client(timeout=VLM_TIMEOUT, follow_redirects=True)

    if args.analyze_only:
        if not settings.vlm_api_key:
            raise SystemExit("缺少环境变量 VLM_API_KEY")
        try:
            result = analyze_video_only(
                cookies_path=args.cookies,
                video_url=args.video_url,
                base_url=settings.vlm_base_url,
                api_key=settings.vlm_api_key,
                model=settings.vlm_model,
                client=vlm_http,
                download_for_vlm=args.download_for_vlm,
                public_media_base_url=args.public_media_base_url,
            )
        except Exception as exc:
            logger.error("视频分析失败（%s）", type(exc).__name__)
            raise SystemExit(1) from exc
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return

    result = ingest_video(
        cookies_path=args.cookies,
        video_url=args.video_url,
        title=args.title,
        source_name=args.source,
        source_page_url=args.source_page_url or args.video_url,
        settings=settings,
        vlm_http=vlm_http,
        download_for_vlm=args.download_for_vlm,
        public_media_base_url=args.public_media_base_url,
    )

    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
