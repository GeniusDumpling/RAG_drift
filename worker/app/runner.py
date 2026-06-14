import asyncio
import json
import time
import uuid
from dataclasses import dataclass
from typing import Literal

import app.db.session as db_session_module
from app.agents.client import AgentClient
from app.agents.contracts import ExtractionAgentRequest, ExtractionAgentResponse
from app.core.config import get_settings
from app.db.base import utcnow
from app.models.content import RawPage
from app.models.control import CrawlJob, CrawlRun, CrawlRunEvent, SourceSite
from app.repositories.search import create_agent_call
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from worker.app.adapters import DiscoveredPage, FetchedPage, get_adapter
from worker.app.chunk_indexer import chunk_and_index_content_items
from worker.app.normalizer import normalize_extraction_response, stable_hash

RunOutcome = Literal["success", "partial", "failed"]


@dataclass(frozen=True)
class WorkerRunResult:
    claimed: int
    succeeded: int
    partial: int
    failed: int


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
                indexing_result = await chunk_and_index_content_items(
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
