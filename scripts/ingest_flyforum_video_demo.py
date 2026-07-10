import argparse
import hashlib
import logging
import random
import re
import time
import uuid
from collections import deque
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urldefrag, urljoin, urlparse, urlunparse

import app.models  # noqa: F401 — registers ORM metadata
import httpx
import requests
from app.core.config import Settings, get_settings
from app.db.base import utcnow
from app.models.content import ContentChunk, ContentItem
from app.models.control import CrawlJob, CrawlRun, CrawlRunEvent, SourceSite
from app.services.chunking import build_chunks
from app.services.embeddings import build_embedding_service
from app.services.retrieval import QdrantIndexer
from bs4 import BeautifulSoup
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker

logger = logging.getLogger(__name__)

ALLOWED_PAGE_HOST = "www.flyforum.cn"
DEFAULT_CHUNKER_TITLE = "FlyForum Video"
VIDEO_EXTENSIONS = (".mp4", ".m3u8", ".webm", ".mov", ".flv")
BLOCKED_PAGE_EXTENSIONS = (
    ".jpg",
    ".jpeg",
    ".png",
    ".gif",
    ".webp",
    ".svg",
    ".css",
    ".js",
    ".ico",
    ".pdf",
    ".zip",
    ".rar",
    *VIDEO_EXTENSIONS,
)
VIDEO_CONTENT_TYPES = (
    "video/",
    "application/vnd.apple.mpegurl",
    "application/x-mpegurl",
    "application/octet-stream",
    "binary/octet-stream",
)
HEAD_REJECT_STATUS_CODES = {403, 405, 501}


@dataclass(frozen=True)
class VideoValidation:
    url: str
    ok: bool
    method: str
    status_code: int | None
    content_type: str | None
    content_length: int | None
    error: str | None = None


def stable_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def normalize_url(url: str) -> str:
    """Remove fragment, standardize trailing slash."""
    url, _ = urldefrag(url)
    return url.strip()


_VIDEO_PATH_RE = re.compile(
    r"^(?P<video_path>.*\.(?:mp4|m3u8|webm|mov|flv))(?:/.*)?$",
    re.IGNORECASE,
)


def normalize_video_url(url: str) -> str:
    """Convert FlyForum poster/frame URLs to the underlying video URL."""
    cleaned = normalize_url(url)
    parsed = urlparse(cleaned)
    path_match = _VIDEO_PATH_RE.match(parsed.path)
    if not path_match:
        return cleaned

    video_path = path_match.group("video_path")
    query = "" if parsed.query.lower().startswith("vframe/") else parsed.query
    return urlunparse((parsed.scheme, parsed.netloc, video_path, "", query, ""))


def is_internal_url(url: str) -> bool:
    parsed = urlparse(url)
    return parsed.netloc == ALLOWED_PAGE_HOST or parsed.netloc == ""


def looks_like_html_page(url: str) -> bool:
    lowered = urlparse(url).path.lower()
    return not lowered.endswith(BLOCKED_PAGE_EXTENSIONS)


def is_video_url(url: str) -> bool:
    path = urlparse(normalize_video_url(url)).path.lower()
    return path.endswith(VIDEO_EXTENSIONS)


_ABSOLUTE_VIDEO_RE = re.compile(
    r"""https?://[^\s'"<>\\]+\.(?:mp4|m3u8|webm|mov|flv)(?:[/?][^\s'"<>\\]*)?""",
    re.IGNORECASE,
)
_RELATIVE_VIDEO_RE = re.compile(
    r"""(?:["'])([^"']+\.(?:mp4|m3u8|webm|mov|flv)(?:[/?][^"']*)?)(?:["'])""",
    re.IGNORECASE,
)


def extract_links_and_videos(page_url: str, html: str) -> tuple[set[str], set[str]]:
    """Return (internal_page_links, video_urls) found in HTML using BeautifulSoup + regex."""
    soup = BeautifulSoup(html, "lxml")
    page_links: set[str] = set()
    video_urls: set[str] = set()

    # 1. <video src>, <source src>, <iframe src>, <embed src>
    for tag in soup.find_all(["video", "source", "iframe", "embed"]):
        src = tag.get("src")
        if src:
            abs_url = normalize_url(urljoin(page_url, src))
            if is_video_url(abs_url):
                video_urls.add(normalize_video_url(abs_url))

    # 2. <a href>
    for a in soup.find_all("a", href=True):
        href = normalize_url(urljoin(page_url, a["href"]))
        if not href.startswith("http"):
            continue
        if is_video_url(href):
            video_urls.add(normalize_video_url(href))
        elif is_internal_url(href) and looks_like_html_page(href):
            page_links.add(href)

    # 3. Regex: absolute video URLs in raw HTML/JS
    for match in _ABSOLUTE_VIDEO_RE.findall(html):
        video_urls.add(normalize_video_url(match))

    # 4. Regex: relative video URLs (quoted)
    for match in _RELATIVE_VIDEO_RE.findall(html):
        abs_url = normalize_video_url(urljoin(page_url, match))
        video_urls.add(abs_url)

    return page_links, video_urls


def _content_length(headers: httpx.Headers) -> int | None:
    value = headers.get("Content-Length")
    if not value:
        return None
    try:
        return int(value)
    except ValueError:
        return None


def _is_usable_video_response(url: str, response: httpx.Response) -> bool:
    if response.status_code not in {200, 206}:
        return False
    content_type = response.headers.get("Content-Type", "").split(";", 1)[0].strip().lower()
    if any(content_type.startswith(prefix) for prefix in VIDEO_CONTENT_TYPES):
        return True
    return is_video_url(str(response.url or url))


def _validation_from_response(
    *, url: str, method: str, response: httpx.Response
) -> VideoValidation:
    return VideoValidation(
        url=url,
        ok=_is_usable_video_response(url, response),
        method=method,
        status_code=response.status_code,
        content_type=response.headers.get("Content-Type"),
        content_length=_content_length(response.headers),
    )


def validate_video_url(client: httpx.Client, url: str) -> VideoValidation:
    """Return whether a candidate video URL is directly usable before VLM analysis."""
    try:
        head_response = client.head(url, follow_redirects=True)
    except httpx.HTTPError as exc:
        logger.debug("HEAD failed for %s: %s", url, exc)
    else:
        if head_response.status_code not in HEAD_REJECT_STATUS_CODES:
            result = _validation_from_response(url=url, method="HEAD", response=head_response)
            retryable_status_codes = {400, 404, 410, 429, 500, 502, 503, 504}
            if result.ok or head_response.status_code not in retryable_status_codes:
                return result

    try:
        get_response = client.get(url, headers={"Range": "bytes=0-0"}, follow_redirects=True)
    except httpx.HTTPError as exc:
        return VideoValidation(
            url=url,
            ok=False,
            method="GET",
            status_code=None,
            content_type=None,
            content_length=None,
            error=str(exc),
        )

    return _validation_from_response(url=url, method="GET", response=get_response)


# ---------------------------------------------------------------------------
# VLM description
# ---------------------------------------------------------------------------


def describe_video(
    *,
    client: httpx.Client,
    base_url: str,
    api_key: str,
    model: str,
    video_url: str,
) -> str:
    """Call SiliconFlow chat completions and return non-empty Chinese description."""
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
                            # "- 如果视频内容只是风景画面，请严格且只输出以下固定句子（不要添加任何其他文字）：\n"
                            # "（该视频内容不涉及无人机脆弱性）\n"
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


# ---------------------------------------------------------------------------
# Ingest orchestration
# ---------------------------------------------------------------------------


def _sync_session(settings: Settings) -> Session:
    engine = create_engine(settings.sync_database_url)
    return sessionmaker(engine, expire_on_commit=False)()


def _source_site_label(settings: Settings) -> str:
    return f"flyforum-demo-{settings.embedding_model}"


def _job_name(settings: Settings) -> str:
    return f"flyforum-video-demo-{settings.embedding_model}"


def _build_http_session() -> requests.Session:
    http = requests.Session()
    http.headers.update({
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/124.0 Safari/537.36"
        ),
        "Accept-Language": "zh-CN,zh;q=0.9",
    })
    return http


def _clean_html(html: str) -> str:
    """Remove NUL (0x00) bytes that PostgreSQL text fields cannot store."""
    return html.replace("\x00", "")


def ingest(args: argparse.Namespace) -> dict[str, object]:
    """Crawl flyforum.cn pages, extract videos, describe via VLM, ingest into RAG."""
    settings = get_settings()

    embedding_service = build_embedding_service(settings)
    vlm_http = httpx.Client(timeout=120, follow_redirects=True)
    validation_http = httpx.Client(
        timeout=15,
        follow_redirects=True,
        headers={
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0 Safari/537.36"
            ),
            "Accept-Language": "zh-CN,zh;q=0.9",
        },
    )
    crawl_http = _build_http_session()

    session = _sync_session(settings)

    try:
        # 1. Create or reuse SourceSite / CrawlJob
        source_label = _source_site_label(settings)
        source = session.scalar(
            select(SourceSite).where(SourceSite.name == source_label)
        )
        if source is None:
            source = SourceSite(
                id=uuid.uuid4(),
                name=source_label,
                site_type="flyforum_demo",
                base_url="https://www.flyforum.cn/",
                allowed_domains=[ALLOWED_PAGE_HOST],
                fetch_mode="http",
                default_language="zh",
                active=True,
                config_json={"demo": True, "embedding_model": settings.embedding_model},
            )
            session.add(source)
            session.flush()

        job_name = _job_name(settings)
        job = session.scalar(
            select(CrawlJob).where(
                CrawlJob.source_site_id == source.id,
                CrawlJob.name == job_name,
            )
        )
        if job is None:
            job = CrawlJob(
                id=uuid.uuid4(),
                source_site_id=source.id,
                name=job_name,
                trigger_mode="manual",
                seed_config_json={
                    "start_urls": [args.start_url],
                    "page_limit": args.page_limit,
                    "video_limit": args.video_limit,
                },
                parser_profile="flyforum_video_demo",
                max_pages=args.page_limit,
                enabled=True,
                agent_policy_json={"demo": True},
            )
            session.add(job)
            session.flush()

        # 2. Create run
        run = CrawlRun(
            id=uuid.uuid4(),
            source_site_id=source.id,
            crawl_job_id=job.id,
            trigger_type="manual",
            execution_mode="demo",
            seed_url=args.start_url,
            status="running",
            started_at=utcnow(),
        )
        session.add(run)
        session.flush()

        run_id = run.id

        # 3. BFS crawl with requests + BeautifulSoup
        queue: deque[str] = deque([normalize_url(args.start_url)])
        visited_pages: set[str] = set()
        video_candidates: list[tuple[str, str]] = []  # (video_url, source_page_url)
        discovered_count = 0
        fetched_count = 0
        parsed_count = 0
        deduped_count = 0
        error_count = 0
        video_discovered_count = 0
        video_analyzed_count = 0
        video_skipped_count = 0
        video_failed_count = 0

        while queue and discovered_count < args.page_limit:
            page_url = queue.popleft()
            if page_url in visited_pages:
                continue
            visited_pages.add(page_url)

            logger.info("Fetching page: %s", page_url)
            try:
                resp = crawl_http.get(page_url, timeout=15)
                resp.raise_for_status()
                html_text = _clean_html(resp.text)
            except Exception as exc:
                error_count += 1
                logger.warning("Failed to fetch %s: %s", page_url, exc)
                session.add(
                    CrawlRunEvent(
                        crawl_run_id=run_id,
                        stage="fetch",
                        level="error",
                        event_type="page_fetch_failed",
                        message=str(exc)[:500],
                        related_url=page_url,
                    )
                )
                session.commit()
                continue

            # Check content type
            content_type = resp.headers.get("Content-Type", "")
            if "text/html" not in content_type and "application/xhtml" not in content_type:
                continue

            # Persist RawPage
            raw_page_id = uuid.uuid4()
            session.raw_page_id = raw_page_id  # type: ignore[attr-defined]
            from app.models.content import RawPage as RawPageModel

            raw_page = RawPageModel(
                id=raw_page_id,
                source_site_id=source.id,
                crawl_run_id=run_id,
                requested_url=page_url,
                final_url=str(resp.url),
                http_status=resp.status_code,
                content_type=content_type[:255],
                raw_html=html_text,
                fetched_at=datetime.now(UTC),
                parser_profile="flyforum_video_demo",
                parse_status="pending",
            )
            session.add(raw_page)
            session.flush()
            fetched_count += 1

            # Extract links & videos via BeautifulSoup + regex
            page_links, found_videos = extract_links_and_videos(page_url, html_text)
            discovered_count += 1

            # Enqueue new page links (generous lookahead buffer)
            for link in page_links:
                if link not in visited_pages and len(visited_pages) < args.page_limit * 3:
                    if link not in queue:
                        queue.append(link)

            # Record video candidates
            for vurl in found_videos:
                video_candidates.append((vurl, page_url))

            # Mark page as parsed
            raw_page.parse_status = "parsed"
            parsed_count += 1

            # Request delay with jitter
            time.sleep(random.uniform(args.delay_min, args.delay_max))

        # 4. Deduplicate video candidates by URL
        seen_videos: dict[str, str] = {}
        for vurl, source_page_url in video_candidates:
            if vurl not in seen_videos:
                seen_videos[vurl] = source_page_url

        video_discovered_count = len(seen_videos)

        # 5. Validate each unique video before sending it to VLM
        validated_videos: list[tuple[str, str]] = []
        for video_url, source_page_url in seen_videos.items():
            if len(validated_videos) >= args.video_limit:
                break

            validation = validate_video_url(validation_http, video_url)
            if not validation.ok:
                video_skipped_count += 1
                logger.info(
                    "Skipping unusable video URL: %s status=%s content_type=%s method=%s",
                    video_url,
                    validation.status_code,
                    validation.content_type,
                    validation.method,
                )
                continue

            validated_videos.append((video_url, source_page_url))

        # 6. Process each usable video
        videos_to_process = validated_videos
        chunked_count = 0
        embedded_count = 0

        for video_url, source_page_url in videos_to_process:
            # Check dedup against previously imported
            dedup_key = stable_hash(
                f"{source.id}:video_description:{video_url}"
            )
            existing_content = session.scalar(
                select(ContentItem).where(ContentItem.dedup_key == dedup_key)
            )
            if existing_content is not None:
                deduped_count += 1
                video_skipped_count += 1
                logger.info("Skipping already-imported video: %s", video_url)
                continue

            # 7. Call VLM
            logger.info("Describing video: %s", video_url)
            try:
                description = describe_video(
                    client=vlm_http,
                    base_url=settings.vlm_base_url,
                    api_key=settings.vlm_api_key or "",
                    model=settings.vlm_model,
                    video_url=video_url,
                )
            except Exception as exc:
                video_failed_count += 1
                error_count += 1
                logger.warning("VLM failed for %s: %s", video_url, exc)
                session.add(
                    CrawlRunEvent(
                        crawl_run_id=run_id,
                        stage="vlm",
                        level="error",
                        event_type="vlm_failed",
                        message=str(exc)[:500],
                        related_url=video_url,
                    )
                )
                session.commit()
                continue

            video_analyzed_count += 1

            # 8. Create ContentItem
            content_id = uuid.uuid4()
            content = ContentItem(
                id=content_id,
                source_site_id=source.id,
                raw_page_id=getattr(session, "raw_page_id", uuid.uuid4()),
                crawl_run_id=run_id,
                item_type="video_description",
                title=None,
                canonical_url=video_url,
                source_url=source_page_url,
                language="zh",
                cleaned_text=description,
                summary_text=description[:240],
                tags=["flyforum", "video"],
                structured_by="qwen3_omni_video_demo",
                metadata_json={
                    "video_url": video_url,
                    "source_page_url": source_page_url,
                    "vlm_model": settings.vlm_model,
                },
                content_hash=stable_hash(description),
                dedup_key=dedup_key,
            )
            session.add(content)
            session.flush()

            # 9. Build chunks
            built_chunks = build_chunks(
                item_type="video_description",
                title=DEFAULT_CHUNKER_TITLE,
                cleaned_text=description,
                summary_text=description[:240],
                tags=["flyforum", "video"],
                thread_title=None,
            )

            chunks: list[ContentChunk] = []
            for built_chunk in built_chunks:
                chunk = ContentChunk(
                    id=uuid.uuid4(),
                    content_item_id=content.id,
                    chunk_index=built_chunk.chunk_index,
                    char_start=built_chunk.start_char,
                    char_end=built_chunk.end_char,
                    display_text=built_chunk.display_text,
                    embed_text=built_chunk.embed_text,
                    token_count=built_chunk.token_count,
                    chunk_metadata_json=built_chunk.chunk_metadata_json,
                    embed_status="pending",
                )
                session.add(chunk)
                chunks.append(chunk)

            session.flush()
            chunked_count += len(chunks)

            # 10. Commit PostgreSQL before Qdrant
            session.commit()

            # 11. Index into Qdrant
            qdrant_url = settings.qdrant_url
            qdrant_collection = settings.qdrant_collection
            indexer = QdrantIndexer(
                url=qdrant_url,
                collection=qdrant_collection,
                embedding=embedding_service,
            )

            try:
                indexer.ensure_collection()
            except Exception as exc:
                for chunk in chunks:
                    chunk.embed_status = "failed"
                    chunk.embed_error = str(exc)[:500]
                video_failed_count += 1
                error_count += 1
                logger.warning("Qdrant collection setup failed: %s", exc)
                session.commit()
                continue

            for chunk in chunks:
                payload: dict[str, Any] = {
                    "chunk_id": str(chunk.id),
                    "content_item_id": str(content.id),
                    "source_site_id": str(source.id),
                    "item_type": "video_description",
                    "video_url": video_url,
                    "source_page_url": source_page_url,
                }
                try:
                    point_id = indexer.upsert_chunk(
                        chunk_id=chunk.id,
                        embed_text=chunk.embed_text,
                        payload=payload,
                    )
                except Exception as exc:
                    chunk.embed_status = "failed"
                    chunk.embed_error = str(exc)[:500]
                    video_failed_count += 1
                    error_count += 1
                    continue

                chunk.vector_backend = "qdrant"
                chunk.vector_point_id = point_id
                chunk.qdrant_point_id = point_id
                chunk.embed_status = "success"
                chunk.embedded_at = utcnow()
                embedded_count += 1

            session.commit()

        # 12. Finalize run
        has_any_success = parsed_count > 0 or video_analyzed_count > 0
        has_errors = error_count > 0

        if has_any_success and has_errors:
            run.status = "partial"
        elif has_any_success:
            run.status = "success"
        else:
            run.status = "failed"

        run.finished_at = utcnow()
        run.discovered_count = discovered_count
        run.fetched_count = fetched_count
        run.parsed_count = parsed_count
        run.extracted_count = video_analyzed_count
        run.deduped_count = deduped_count
        run.error_count = error_count

        session.commit()

        # 13. Return credential-free summary
        summary: dict[str, object] = {
            "status": run.status,
            "run_id": str(run.id),
            "source_id": str(source.id),
            "job_id": str(job.id),
            "pages_discovered": discovered_count,
            "pages_fetched": fetched_count,
            "pages_parsed": parsed_count,
            "video_discovered": video_discovered_count,
            "video_analyzed": video_analyzed_count,
            "video_skipped": video_skipped_count,
            "video_failed": video_failed_count,
            "chunked_count": chunked_count,
            "embedded_count": embedded_count,
            "deduped_count": deduped_count,
            "error_count": error_count,
        }
        return summary

    finally:
        session.close()
        crawl_http.close()
        vlm_http.close()
        validation_http.close()


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="FlyForum video RAG demo importer (BeautifulSoup crawler)"
    )
    parser.add_argument(
        "--start-url",
        default="https://www.flyforum.cn/forum.php",
        help="Starting page URL",
    )
    parser.add_argument(
        "--page-limit",
        type=int,
        default=3,
        help="Maximum pages to visit (default: 3)",
    )
    parser.add_argument(
        "--video-limit",
        type=int,
        default=1,
        help="Maximum videos to describe (default: 1)",
    )
    parser.add_argument(
        "--delay-min",
        type=float,
        default=1.0,
        help="Minimum delay between requests in seconds (default: 1.0)",
    )
    parser.add_argument(
        "--delay-max",
        type=float,
        default=3.0,
        help="Maximum delay between requests in seconds (default: 3.0)",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Output JSON summary instead of human-readable text",
    )
    return parser.parse_args(argv)


def main() -> None:
    args = parse_args()
    summary = ingest(args)
    if args.json:
        import json as json_mod
        print(json_mod.dumps(summary, ensure_ascii=False, indent=2))
    else:
        print("Run complete:")
        for key, value in summary.items():
            print(f"  {key}: {value}")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    main()
