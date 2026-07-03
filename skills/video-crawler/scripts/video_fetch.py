#!/usr/bin/env python3
"""Fetch a video URL, optionally download via yt-dlp, call VLM to generate description, store into RAG."""

from __future__ import annotations

import argparse
import hashlib
import logging
import pathlib
import uuid
from typing import Any
from urllib.parse import urlparse

import httpx
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker

import app.models  # noqa: F401 — register ORM metadata
from app.core.config import Settings, get_settings
from app.db.base import utcnow
from app.models.content import ContentChunk, ContentItem
from app.models.control import CrawlJob, CrawlRun, CrawlRunEvent, SourceSite
from app.services.chunking import build_chunks
from app.services.embeddings import build_embedding_service
from app.services.retrieval import QdrantIndexer

FILE_SERVER_BASE = "http://127.0.0.1:18999"
COOKIES_PATH_DEFAULT = str(pathlib.Path(__file__).resolve().parent.parent / "cookies_www.youtube.com.txt")

logger = logging.getLogger(__name__)

VLM_TIMEOUT = 120
DOWNLOAD_DIR = pathlib.Path(__file__).resolve().parent.parent / "downloads"
_VIDEO_EXTENSIONS = (".mp4", ".webm", ".mov", ".m3u8", ".flv")


def stable_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _is_direct_video_url(url: str) -> bool:
    """Check if the URL points directly to a playable video file."""
    try:
        path = urlparse(url).path.lower()
    except Exception:
        return False
    return any(path.endswith(ext) for ext in _VIDEO_EXTENSIONS)

def _build_ytdlp_opts(cookies_path: str | None = None) -> dict[str, object]:
    """Build yt-dlp options dict with optional cookies."""
    opts: dict[str, object] = {
        "format": "best[height<=720]",
        "quiet": True,
        "no_warnings": True,
        "extract_flat": False,
        "http_headers": {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/143.0.0.0 Safari/537.36",
            "Accept-Language": "en-US,en;q=0.9",
        },
    }
    if cookies_path and pathlib.Path(cookies_path).is_file():
        opts["cookiefile"] = cookies_path
    return opts



def _resolve_via_ytdlp_extract(video_url: str, cookies_path: str | None = None) -> str | None:
    """Try yt-dlp extract_info to get the best direct streaming URL (no download)."""
    try:
        import yt_dlp

        ydl_opts = {
            "format": "best[height<=720]",
            "quiet": True,
            "no_warnings": True,
            "extract_flat": False,
        }
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(video_url, download=False)
            url = info.get("url")
            if url:
                logger.info("yt-dlp resolved direct URL: %s …", url[:80])
                return url
    except Exception as exc:
        logger.warning("yt-dlp extract failed for %s: %s", video_url, exc)
    return None


def _resolve_via_ytdlp_download(video_url: str, cookies_path: str | None = None) -> str | None:
    """
    Fallback: download the video to local disk via yt-dlp and return the local
    file path (as a ``file://`` URL).  The VLM must be able to read local files
    or a local HTTP file server must be running to serve them.
    """
    DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)
    try:
        import yt_dlp

        safe_name = hashlib.md5(video_url.encode()).hexdigest()[:16]
        output_template = str(DOWNLOAD_DIR / f"{safe_name}.%(ext)s")

        opts = _build_ytdlp_opts(cookies_path)
        opts["format"] = "best[height<=480]"
        opts["outtmpl"] = output_template
        opts["max_filesize"] = 200_000_000  # 200 MB
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(video_url, download=True)
            downloaded_path = ydl.prepare_filename(info)
            # yt-dlp may add an extra extension; find the actual file
            actual = next(DOWNLOAD_DIR.glob(f"{safe_name}.*"), None)
            if actual and actual.is_file():
                # 替换为文件服务器的 HTTP URL（VLM 无法访问 file://）
                file_server_url = f"{FILE_SERVER_BASE}/{actual.name}"
                logger.info("yt-dlp downloaded to %s, server URL: %s", actual, file_server_url)
                return file_server_url
    except Exception as exc:
        logger.warning("yt-dlp download failed for %s: %s", video_url, exc)
    return None


def resolve_video_url(video_url: str, cookies_path: str | None = None) -> tuple[str, bool, str]:
    """
    Return (resolved_or_fallback_url, can_be_used_by_vlm, hint).

    Three-tier fallback:
      1. Direct video URL (.mp4/.webm/…)              → VLM 可直接访问 ✅
      2. yt-dlp extract (no download)                  → VLM 可直接访问 ✅
      3. yt-dlp download to local file                 → VLM 需要本地文件服务 ⚠️
      4. All failed                                    → 原 URL, VLM 很可能不可用 ❌
    """
    # Tier 1: 已经是直接视频 URL
    if _is_direct_video_url(video_url):
        logger.info("Tier 1: 直接视频 URL")
        return video_url, True, "direct"

    # Tier 2: yt-dlp 解析直链（不下）
    logger.info("Tier 2: yt-dlp 解析直链 …")
    direct = _resolve_via_ytdlp_extract(video_url, cookies_path)
    if direct:
        return direct, True, "ytdlp_extracted"

    # Tier 3: yt-dlp 下载到本地
    logger.info("Tier 3: yt-dlp 下载到本地 …")
    local = _resolve_via_ytdlp_download(video_url, cookies_path)
    if local:
        return local, True, "ytdlp_downloaded"

    # Tier 4: 全部失败，返回原 URL
    logger.warning("所有解析方式均失败，返回原始 URL")
    return video_url, False, "failed"


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


def ingest_video(
    *,
    video_url: str,
    title: str | None,
    source_name: str,
    source_page_url: str | None = None,
    settings: Settings,
    vlm_http: httpx.Client,
    cookies_path: str | None = None,
) -> dict[str, Any]:
    """Ingest a single video: resolve URL -> VLM -> chunk -> embed -> store."""
    resolved_url, vlm_ok, hint = resolve_video_url(video_url, cookies_path)
    logger.info("resolve result: vlm_ok=%s hint=%s resolved=%s …", vlm_ok, hint, resolved_url[:80])

    # 决定送给 VLM 的 URL：优先用解析后的直链，否则退回原 URL
    vlm_input_url = resolved_url if vlm_ok else video_url
    if not vlm_ok:
        logger.warning("VLM 可能无法处理该视频 URL: %s", video_url)

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
        canonical_url = source_page_url or video_url
        dedup_key = stable_hash(f"{source.id}:{video_url}:video_description:{description[:100]}")
        existing = session.scalar(select(ContentItem).where(ContentItem.dedup_key == dedup_key).limit(1))
        if existing is not None:
            logger.info("Duplicate video, skipping: %s", video_url)
            run.status = "success"
            run.finished_at = utcnow()
            session.commit()
            return {"status": "skipped", "reason": "duplicate"}

        content = ContentItem(
            source_site_id=source.id,
            crawl_run_id=run.id,
            item_type="video_description",
            title=title,
            canonical_url=canonical_url,
            source_url=video_url,
            language="zh",
            cleaned_text=description,
            summary_text=description[:240],
            tags=[source_name, "video", "crawled"],
            structured_by="qwen3_omni_video_crawler",
            metadata_json={
                "video_url": video_url,
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
                point_id = indexer.index_chunk(
                    chunk_id=str(chunk.id),
                    content_item_id=str(content.id),
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
            "video_url": video_url,
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
    return parser.parse_args()


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    args = parse_args()
    settings = get_settings()

    vlm_http = httpx.Client(timeout=VLM_TIMEOUT, follow_redirects=True)

    result = ingest_video(
        cookies_path=args.cookies,
        video_url=args.video_url,
        title=args.title,
        source_name=args.source,
        source_page_url=args.source_page_url or args.video_url,
        settings=settings,
        vlm_http=vlm_http,
    )

    import json as json_mod

    print(json_mod.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
