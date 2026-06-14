import asyncio
import json
import time
import uuid
from dataclasses import dataclass
from typing import Any, Literal

import app.db.session as db_session_module
from app.agents.client import AgentClient
from app.agents.contracts import ExtractionAgentRequest, ExtractionAgentResponse
from app.core.config import get_settings
from app.db.base import utcnow
from app.models.content import ContentChunk, ContentItem, RawPage
from app.models.control import CrawlJob, CrawlRun, CrawlRunEvent, SourceSite
from app.repositories.search import create_agent_call
from app.services.chunking import build_chunks
from app.services.embeddings import DeterministicEmbeddingService
from app.services.retrieval import QdrantIndexer
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from worker.app.adapters import DiscoveredPage, FetchedPage, get_adapter
from worker.app.normalizer import normalize_extraction_response, stable_hash

RunOutcome = Literal["success", "partial", "failed"]


@dataclass(frozen=True)
class WorkerRunResult:
    claimed: int
    succeeded: int
    partial: int
    failed: int


@dataclass(frozen=True)
class ChunkIndexingResult:
    chunked_count: int
    embedded_count: int
    failed_count: int


VectorIndexMetadata = dict[str, str | int]


@dataclass(frozen=True)
class VectorIndexTarget:
    backend_name: Literal["memory", "qdrant"]
    metadata: VectorIndexMetadata


def run_once(run_limit: int = 1) -> WorkerRunResult:
    if run_limit < 1:
        return WorkerRunResult(claimed=0, succeeded=0, partial=0, failed=0)
    return asyncio.run(_run_once_async(run_limit=run_limit))


async def _run_once_async(*, run_limit: int) -> WorkerRunResult:
    claimed_run_ids = await _claim_queued_runs(run_limit=run_limit)
    succeeded = 0
    partial = 0
    failed = 0

    for run_id in claimed_run_ids:
        try:
            outcome = await _process_run(run_id)
        except Exception as exc:
            await _mark_unexpected_run_failure(run_id, exc)
            failed += 1
            continue

        if outcome == "success":
            succeeded += 1
        elif outcome == "partial":
            partial += 1
        else:
            failed += 1

    return WorkerRunResult(
        claimed=len(claimed_run_ids), succeeded=succeeded, partial=partial, failed=failed
    )


async def _claim_queued_runs(*, run_limit: int) -> list[uuid.UUID]:
    async with db_session_module.AsyncSessionLocal() as session:
        result = await session.scalars(
            select(CrawlRun)
            .where(CrawlRun.status == "queued")
            .order_by(CrawlRun.created_at.asc())
            .limit(run_limit)
            .with_for_update(skip_locked=True)
        )
        runs = list(result)
        started_at = utcnow()
        for run in runs:
            run.status = "running"
            run.started_at = started_at
        await session.commit()
        return [run.id for run in runs]


async def _mark_unexpected_run_failure(run_id: uuid.UUID, exc: Exception) -> None:
    try:
        async with db_session_module.AsyncSessionLocal() as session:
            run = await session.get(CrawlRun, run_id)
            if run is None:
                return

            await _mark_run_failed(
                session,
                run_id=run_id,
                seed_url=run.seed_url,
                error_message=f"Worker failed unexpectedly: {exc}",
                discovered_count=run.discovered_count,
                fetched_count=run.fetched_count,
                parsed_count=run.parsed_count,
                extracted_count=run.extracted_count,
                deduped_count=run.deduped_count,
                error_count=run.error_count + 1,
            )
    except Exception:
        return


async def _process_run(run_id: uuid.UUID) -> RunOutcome:
    async with db_session_module.AsyncSessionLocal() as session:
        run = await session.get(CrawlRun, run_id)
        if run is None:
            return "failed"

        source_site_id = run.source_site_id
        crawl_job_id = run.crawl_job_id
        run_seed_url = run.seed_url

        source_site = await session.get(SourceSite, source_site_id)
        crawl_job = await session.get(CrawlJob, crawl_job_id)
        if source_site is None or crawl_job is None:
            return await _mark_run_failed(
                session,
                run_id=run_id,
                seed_url=run_seed_url,
                error_message="Crawl run references a missing source site or crawl job.",
                discovered_count=0,
                fetched_count=0,
                parsed_count=0,
                extracted_count=0,
                deduped_count=0,
                error_count=1,
            )

        parser_profile = crawl_job.parser_profile
        seed_config_json = dict(crawl_job.seed_config_json)
        max_pages = crawl_job.max_pages
        source_site_type = source_site.site_type

        try:
            adapter = get_adapter(parser_profile)
            discovered_pages = adapter.discover(seed_config_json, run_seed_url, max_pages)
        except Exception as exc:
            await session.rollback()
            return await _mark_run_failed(
                session,
                run_id=run_id,
                seed_url=run_seed_url,
                error_message=str(exc),
                discovered_count=0,
                fetched_count=0,
                parsed_count=0,
                extracted_count=0,
                deduped_count=0,
                error_count=1,
            )

        discovered_count = len(discovered_pages)
        if discovered_count == 0:
            return await _mark_run_failed(
                session,
                run_id=run_id,
                seed_url=run_seed_url,
                error_message="No pages were discovered.",
                discovered_count=0,
                fetched_count=0,
                parsed_count=0,
                extracted_count=0,
                deduped_count=0,
                error_count=1,
            )

        fetched_count = 0
        extracted_count = 0
        deduped_count = 0
        successful_pages = 0
        failed_pages = 0
        chunked_count = 0
        embedded_count = 0
        indexing_failure_count = 0

        for page in discovered_pages:
            requested_url = page.requested_url
            related_raw_page_id: uuid.UUID | None = None
            try:
                fetched = adapter.fetch(page)
                raw_page = await _persist_raw_page_and_event(
                    session=session,
                    source_site_id=source_site_id,
                    crawl_run_id=run_id,
                    parser_profile=parser_profile,
                    page=page,
                    fetched=fetched,
                )
                related_raw_page_id = raw_page.id
                fetched_count += 1

                request = ExtractionAgentRequest(
                    raw_page_id=related_raw_page_id,
                    source_site_id=source_site_id,
                    crawl_run_id=run_id,
                    requested_url=fetched.requested_url,
                    final_url=fetched.final_url,
                    site_type=source_site_type,
                    parser_profile=parser_profile,
                    content_type=fetched.content_type,
                    raw_html=fetched.raw_html,
                    raw_markdown=fetched.raw_markdown,
                    raw_json=fetched.raw_json,
                    context_json=adapter.prepare_extraction_context(page, fetched),
                )
                response, latency_ms = _call_extraction_agent(request)

                await create_agent_call(
                    session,
                    agent_role="extraction",
                    caller="ingestion_worker",
                    status=_agent_call_status(response),
                    related_crawl_run_id=run_id,
                    related_raw_page_id=related_raw_page_id,
                    request_schema_version=request.response_schema_version,
                    response_schema_version="extraction.v1",
                    input_summary_json={
                        "requested_url": request.requested_url,
                        "final_url": request.final_url,
                        "raw_page_id": str(request.raw_page_id),
                        "parser_profile": request.parser_profile,
                    },
                    output_summary_json={
                        "page_kind": response.page_kind,
                        "item_count": len(response.items),
                        "extraction_confidence": response.extraction_confidence,
                        "warnings": response.warnings,
                        "trace_summary_json": response.trace_summary_json,
                    },
                    latency_ms=latency_ms,
                )
                await _record_extraction_called_event(
                    session=session,
                    run_id=run_id,
                    raw_page=raw_page,
                    requested_url=fetched.requested_url,
                    response=response,
                )

                normalization_result = await normalize_extraction_response(
                    session,
                    source_site=source_site,
                    raw_page=raw_page,
                    response=response,
                )
                raw_page.parse_status = "parsed"
                raw_page.extraction_method = "extraction_agent"
                raw_page.extraction_confidence = response.extraction_confidence
                indexing_result = await _chunk_and_index_content_items(
                    session=session,
                    run_id=run_id,
                    content_item_ids=normalization_result.content_item_ids,
                )
                await session.commit()

                extracted_count += len(normalization_result.created_item_ids)
                deduped_count += normalization_result.deduped_count
                chunked_count += indexing_result.chunked_count
                embedded_count += indexing_result.embedded_count
                indexing_failure_count += indexing_result.failed_count
                successful_pages += 1
            except Exception as exc:
                failed_pages += 1
                await session.rollback()
                await _record_page_failure_event(
                    session=session,
                    run_id=run_id,
                    requested_url=requested_url,
                    related_raw_page_id=related_raw_page_id,
                    exc=exc,
                )
                reloaded_source_site = await session.get(SourceSite, source_site_id)
                if reloaded_source_site is None:
                    return await _mark_run_failed(
                        session,
                        run_id=run_id,
                        seed_url=run_seed_url,
                        error_message="Crawl run source site disappeared during worker processing.",
                        discovered_count=discovered_count,
                        fetched_count=fetched_count,
                        parsed_count=successful_pages,
                        extracted_count=extracted_count,
                        deduped_count=deduped_count,
                        error_count=failed_pages + 1,
                    )
                source_site = reloaded_source_site
                source_site_type = source_site.site_type

        if successful_pages == len(discovered_pages) and discovered_pages:
            if indexing_failure_count:
                final_status: RunOutcome = "partial"
                error_message = _run_partial_error_message(
                    failed_pages=failed_pages,
                    indexing_failure_count=indexing_failure_count,
                )
            else:
                final_status = "success"
                error_message = None
        elif successful_pages > 0:
            final_status = "partial"
            error_message = _run_partial_error_message(
                failed_pages=failed_pages,
                indexing_failure_count=indexing_failure_count,
            )
        else:
            final_status = "failed"
            error_message = "No pages were successfully processed."
            if failed_pages:
                error_message = f"{failed_pages} page(s) failed during worker processing."

        run = await session.get(CrawlRun, run_id)
        if run is None:
            return "failed"
        run.status = final_status
        run.finished_at = utcnow()
        run.discovered_count = discovered_count
        run.fetched_count = fetched_count
        run.parsed_count = successful_pages
        run.extracted_count = extracted_count
        run.deduped_count = deduped_count
        run.chunked_count = chunked_count
        run.embedded_count = embedded_count
        run.error_count = failed_pages + indexing_failure_count
        run.error_message = error_message
        crawl_job_for_update = await session.get(CrawlJob, crawl_job_id)
        if crawl_job_for_update is not None:
            crawl_job_for_update.last_run_at = run.finished_at
        if final_status == "failed":
            _add_run_failed_event(
                session,
                run_id=run_id,
                seed_url=run_seed_url,
                error_message=error_message or "Run failed.",
                error_count=failed_pages,
            )
        await session.commit()
        return final_status


async def _chunk_and_index_content_items(
    *,
    session: AsyncSession,
    run_id: uuid.UUID,
    content_item_ids: list[uuid.UUID],
) -> ChunkIndexingResult:
    unique_item_ids = list(dict.fromkeys(content_item_ids))
    vector_target = _current_vector_index_target()
    chunks_to_index: list[tuple[ContentChunk, ContentItem]] = []
    newly_created_chunk_count = 0

    for content_item_id in unique_item_ids:
        content_item = await session.get(ContentItem, content_item_id)
        if content_item is None:
            continue

        existing_chunks = await _load_chunks_for_item(session, content_item_id)
        if existing_chunks:
            for existing_chunk in _chunks_requiring_indexing(
                existing_chunks,
                vector_target=vector_target,
            ):
                chunks_to_index.append((existing_chunk, content_item))
            continue

        thread_title = await _thread_title_for_item(session, content_item)
        chunks_for_indexing, created_chunk_count = await _create_chunks_or_reload_existing(
            session=session,
            content_item=content_item,
            thread_title=thread_title,
            vector_target=vector_target,
        )
        for content_chunk in chunks_for_indexing:
            chunks_to_index.append((content_chunk, content_item))
        newly_created_chunk_count += created_chunk_count

    if not chunks_to_index:
        return ChunkIndexingResult(chunked_count=0, embedded_count=0, failed_count=0)

    await session.commit()

    backend_name = "qdrant"
    try:
        indexer = _build_qdrant_indexer()
        backend_name = indexer.backend_name
        indexer.ensure_collection()
    except Exception as exc:
        error_message = _error_message(exc)
        for content_chunk, _content_item in chunks_to_index:
            _mark_chunk_failed(
                content_chunk=content_chunk,
                backend_name=backend_name,
                error_message=error_message,
            )
            await _record_vector_index_failure_event(
                session=session,
                run_id=run_id,
                content_chunk=content_chunk,
                error_message=error_message,
            )
        await session.flush()
        return ChunkIndexingResult(
            chunked_count=newly_created_chunk_count,
            embedded_count=0,
            failed_count=len(chunks_to_index),
        )

    embedded_count = 0
    failed_count = 0
    for content_chunk, content_item in chunks_to_index:
        try:
            point_id = indexer.upsert_chunk(
                chunk_id=content_chunk.id,
                embed_text=content_chunk.embed_text,
                payload=_build_chunk_payload(
                    content_item=content_item,
                    chunk_id=content_chunk.id,
                ),
            )
        except Exception as exc:
            failed_count += 1
            error_message = _error_message(exc)
            _mark_chunk_failed(
                content_chunk=content_chunk,
                backend_name=indexer.backend_name,
                error_message=error_message,
            )
            await _record_vector_index_failure_event(
                session=session,
                run_id=run_id,
                content_chunk=content_chunk,
                error_message=error_message,
            )
            continue

        embedded_count += 1
        _mark_chunk_success(
            content_chunk=content_chunk,
            backend_name=indexer.backend_name,
            point_id=point_id,
            index_metadata=vector_target.metadata,
        )

    await session.flush()
    return ChunkIndexingResult(
        chunked_count=newly_created_chunk_count,
        embedded_count=embedded_count,
        failed_count=failed_count,
    )


async def _load_chunks_for_item(
    session: AsyncSession, content_item_id: uuid.UUID
) -> list[ContentChunk]:
    chunks = await session.scalars(
        select(ContentChunk)
        .where(ContentChunk.content_item_id == content_item_id)
        .order_by(ContentChunk.chunk_index.asc())
    )
    return list(chunks.all())


def _chunks_requiring_indexing(
    chunks: list[ContentChunk],
    *,
    vector_target: VectorIndexTarget,
) -> list[ContentChunk]:
    return [
        chunk
        for chunk in chunks
        if _chunk_requires_indexing(chunk, vector_target=vector_target)
    ]


def _chunk_requires_indexing(
    chunk: ContentChunk,
    *,
    vector_target: VectorIndexTarget,
) -> bool:
    if chunk.embed_status in {"pending", "failed"}:
        return True
    if chunk.embed_status != "success":
        return True
    if chunk.vector_backend != vector_target.backend_name:
        return True

    metadata = chunk.chunk_metadata_json
    return any(
        metadata.get(key) != value for key, value in vector_target.metadata.items()
    )


async def _create_chunks_or_reload_existing(
    *,
    session: AsyncSession,
    content_item: ContentItem,
    thread_title: str | None,
    vector_target: VectorIndexTarget,
) -> tuple[list[ContentChunk], int]:
    built_chunks = build_chunks(
        item_type=content_item.item_type,
        title=content_item.title,
        cleaned_text=content_item.cleaned_text,
        summary_text=content_item.summary_text,
        tags=content_item.tags,
        thread_title=thread_title,
    )
    new_chunks = [
        ContentChunk(
            content_item_id=content_item.id,
            chunk_index=built_chunk.chunk_index,
            char_start=built_chunk.start_char,
            char_end=built_chunk.end_char,
            display_text=built_chunk.display_text,
            embed_text=built_chunk.embed_text,
            token_count=built_chunk.token_count,
            chunk_metadata_json=built_chunk.chunk_metadata_json,
            qdrant_point_id=None,
            vector_backend=None,
            vector_point_id=None,
            embedded_at=None,
            embed_status="pending",
            embed_error=None,
        )
        for built_chunk in built_chunks
    ]

    try:
        async with session.begin_nested():
            for content_chunk in new_chunks:
                session.add(content_chunk)
            await session.flush()
    except IntegrityError:
        existing_chunks = await _load_chunks_for_item(session, content_item.id)
        if not existing_chunks:
            raise
        return _chunks_requiring_indexing(
            existing_chunks,
            vector_target=vector_target,
        ), 0

    return new_chunks, len(new_chunks)


async def _thread_title_for_item(
    session: AsyncSession, content_item: ContentItem
) -> str | None:
    if content_item.item_type == "thread":
        return content_item.title
    if content_item.item_type != "comment":
        return None

    related_ids = [content_item.thread_root_id, content_item.parent_item_id]
    for related_id in related_ids:
        if related_id is None or related_id == content_item.id:
            continue
        related_item = await session.get(ContentItem, related_id)
        if related_item is not None and related_item.title:
            return related_item.title
    return None


def _build_qdrant_indexer() -> QdrantIndexer:
    settings = get_settings()
    return QdrantIndexer(
        url=settings.qdrant_url,
        collection=settings.qdrant_collection,
        embedding=DeterministicEmbeddingService(),
    )


def _current_vector_index_target() -> VectorIndexTarget:
    settings = get_settings()
    embedding = DeterministicEmbeddingService()
    return VectorIndexTarget(
        backend_name=_vector_backend_name(settings.qdrant_url),
        metadata={
            "vector_collection": settings.qdrant_collection,
            "embedding_model": embedding.model_name,
            "embedding_dimension": embedding.dimension,
        },
    )


def _vector_backend_name(url: str) -> Literal["memory", "qdrant"]:
    if url == ":memory:" or url.startswith("memory://"):
        return "memory"
    return "qdrant"


def _build_chunk_payload(
    *,
    content_item: ContentItem,
    chunk_id: uuid.UUID,
) -> dict[str, Any]:
    return {
        "chunk_id": str(chunk_id),
        "content_item_id": str(content_item.id),
        "source_site_id": str(content_item.source_site_id),
        "item_type": content_item.item_type,
        "canonical_url": content_item.canonical_url,
        "title": content_item.title,
        "author_id": str(content_item.author_id) if content_item.author_id else None,
        "published_at": (
            content_item.published_at.isoformat() if content_item.published_at else None
        ),
        "language": content_item.language,
        "source_section": _metadata_string(content_item.metadata_json, "source_section"),
        "thread_root_id": (
            str(content_item.thread_root_id) if content_item.thread_root_id else None
        ),
        "parent_item_id": (
            str(content_item.parent_item_id) if content_item.parent_item_id else None
        ),
        "tags": list(content_item.tags),
    }


def _metadata_string(metadata_json: dict[str, Any], key: str) -> str | None:
    value = metadata_json.get(key)
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


def _mark_chunk_success(
    *,
    content_chunk: ContentChunk,
    backend_name: str,
    point_id: str,
    index_metadata: VectorIndexMetadata,
) -> None:
    content_chunk.embed_status = "success"
    content_chunk.embed_error = None
    content_chunk.vector_backend = backend_name
    content_chunk.vector_point_id = point_id
    content_chunk.qdrant_point_id = point_id if backend_name == "qdrant" else None
    content_chunk.embedded_at = utcnow()
    content_chunk.chunk_metadata_json = {
        **content_chunk.chunk_metadata_json,
        **index_metadata,
    }


def _mark_chunk_failed(
    *,
    content_chunk: ContentChunk,
    backend_name: str,
    error_message: str,
) -> None:
    content_chunk.embed_status = "failed"
    content_chunk.embed_error = error_message
    content_chunk.vector_backend = backend_name
    content_chunk.vector_point_id = None
    content_chunk.qdrant_point_id = None
    content_chunk.embedded_at = None


async def _record_vector_index_failure_event(
    *,
    session: AsyncSession,
    run_id: uuid.UUID,
    content_chunk: ContentChunk,
    error_message: str,
) -> None:
    content_item = await session.get(ContentItem, content_chunk.content_item_id)
    if content_item is None:
        return
    session.add(
        CrawlRunEvent(
            crawl_run_id=run_id,
            stage="index",
            level="error",
            event_type="vector_index_failed",
            message=error_message,
            related_url=content_item.canonical_url,
            related_raw_page_id=content_item.raw_page_id,
            related_content_item_id=content_chunk.content_item_id,
            counters_json={"chunk_id": str(content_chunk.id), "error_count_increment": 1},
            agent_trace_json={},
        )
    )


def _error_message(exc: Exception) -> str:
    message = str(exc) or exc.__class__.__name__
    return f"{exc.__class__.__name__}: {message}"[:2000]


def _run_partial_error_message(*, failed_pages: int, indexing_failure_count: int) -> str:
    messages: list[str] = []
    if failed_pages:
        messages.append(f"{failed_pages} page(s) failed during worker processing.")
    if indexing_failure_count:
        messages.append(f"{indexing_failure_count} chunk(s) failed during vector indexing.")
    return " ".join(messages) or "Run completed partially."


async def _persist_raw_page_and_event(
    *,
    session: AsyncSession,
    source_site_id: uuid.UUID,
    crawl_run_id: uuid.UUID,
    parser_profile: str,
    page: DiscoveredPage,
    fetched: FetchedPage,
) -> RawPage:
    raw_page = RawPage(
        source_site_id=source_site_id,
        crawl_run_id=crawl_run_id,
        requested_url=fetched.requested_url,
        final_url=fetched.final_url,
        http_status=fetched.http_status,
        content_type=fetched.content_type,
        response_headers_json=fetched.response_headers_json,
        raw_html=fetched.raw_html,
        raw_text=fetched.raw_markdown,
        raw_json=fetched.raw_json or {},
        fetched_at=utcnow(),
        fetch_error=None,
        parser_profile=parser_profile,
        extraction_method=None,
        extraction_confidence=None,
        parse_status="pending",
        parse_error=None,
        body_hash=_body_hash(fetched),
    )
    session.add(raw_page)
    await session.flush()

    session.add(
        CrawlRunEvent(
            crawl_run_id=crawl_run_id,
            stage="fetch",
            level="info",
            event_type="raw_page_persisted",
            message="Raw page persisted before extraction agent call.",
            related_url=page.requested_url,
            related_raw_page_id=raw_page.id,
            related_content_item_id=None,
            counters_json={"fetched_count_increment": 1},
            agent_trace_json={},
        )
    )
    await session.commit()
    return raw_page


def _call_extraction_agent(request: ExtractionAgentRequest) -> tuple[ExtractionAgentResponse, int]:
    settings = get_settings()
    client = AgentClient(
        provider=settings.llm_provider,
        timeout_seconds=settings.agent_timeout_seconds,
    )
    started_at = time.perf_counter()
    response = client.extract_page(request)
    latency_ms = int((time.perf_counter() - started_at) * 1000)
    return response, latency_ms


def _agent_call_status(response: ExtractionAgentResponse) -> Literal["success", "fallback_used"]:
    if response.trace_summary_json.get("fallback") is True:
        return "fallback_used"
    if any("fallback" in warning.lower() for warning in response.warnings):
        return "fallback_used"
    return "success"


async def _record_extraction_called_event(
    *,
    session: AsyncSession,
    run_id: uuid.UUID,
    raw_page: RawPage,
    requested_url: str,
    response: ExtractionAgentResponse,
) -> None:
    session.add(
        CrawlRunEvent(
            crawl_run_id=run_id,
            stage="extract",
            level="info",
            event_type="extraction_agent_called",
            message="Extraction agent called for persisted raw page.",
            related_url=requested_url,
            related_raw_page_id=raw_page.id,
            related_content_item_id=None,
            counters_json={"extracted_item_count": len(response.items)},
            agent_trace_json=response.trace_summary_json,
        )
    )
    await session.commit()


async def _record_page_failure_event(
    *,
    session: AsyncSession,
    run_id: uuid.UUID,
    requested_url: str,
    related_raw_page_id: uuid.UUID | None,
    exc: Exception,
) -> None:
    error_message = str(exc)
    if related_raw_page_id is not None:
        raw_page = await session.get(RawPage, related_raw_page_id)
        if raw_page is not None:
            raw_page.parse_status = "failed"
            raw_page.parse_error = error_message

    session.add(
        CrawlRunEvent(
            crawl_run_id=run_id,
            stage="worker",
            level="error",
            event_type="page_failed",
            message=error_message,
            related_url=requested_url,
            related_raw_page_id=related_raw_page_id,
            related_content_item_id=None,
            counters_json={"error_count_increment": 1},
            agent_trace_json={},
        )
    )
    await session.commit()


async def _mark_run_failed(
    session: AsyncSession,
    *,
    run_id: uuid.UUID,
    seed_url: str | None,
    error_message: str,
    discovered_count: int,
    fetched_count: int,
    parsed_count: int,
    extracted_count: int,
    deduped_count: int,
    error_count: int,
) -> Literal["failed"]:
    run = await session.get(CrawlRun, run_id)
    if run is None:
        return "failed"
    run.status = "failed"
    run.finished_at = utcnow()
    run.discovered_count = discovered_count
    run.fetched_count = fetched_count
    run.parsed_count = parsed_count
    run.extracted_count = extracted_count
    run.deduped_count = deduped_count
    run.error_count = error_count
    run.error_message = error_message
    _add_run_failed_event(
        session,
        run_id=run_id,
        seed_url=seed_url,
        error_message=error_message,
        error_count=error_count,
    )
    await session.commit()
    return "failed"


def _add_run_failed_event(
    session: AsyncSession,
    *,
    run_id: uuid.UUID,
    seed_url: str | None,
    error_message: str,
    error_count: int,
) -> None:
    session.add(
        CrawlRunEvent(
            crawl_run_id=run_id,
            stage="worker",
            level="error",
            event_type="run_failed",
            message=error_message,
            related_url=seed_url,
            related_raw_page_id=None,
            related_content_item_id=None,
            counters_json={"error_count": error_count},
            agent_trace_json={},
        )
    )


def _body_hash(fetched: FetchedPage) -> str:
    body = {
        "raw_html": fetched.raw_html,
        "raw_markdown": fetched.raw_markdown,
        "raw_json": fetched.raw_json,
    }
    return stable_hash(json.dumps(body, sort_keys=True, separators=(",", ":")))
