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
        outcome = await _process_run(run_id)
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


async def _process_run(run_id: uuid.UUID) -> RunOutcome:
    async with db_session_module.AsyncSessionLocal() as session:
        run = await session.get(CrawlRun, run_id)
        if run is None:
            return "failed"

        source_site = await session.get(SourceSite, run.source_site_id)
        crawl_job = await session.get(CrawlJob, run.crawl_job_id)
        if source_site is None or crawl_job is None:
            return await _mark_run_failed(
                session,
                run,
                error_message="Crawl run references a missing source site or crawl job.",
                discovered_count=0,
                fetched_count=0,
                extracted_count=0,
                error_count=1,
            )

        try:
            adapter = get_adapter(crawl_job.parser_profile)
            discovered_pages = adapter.discover(
                crawl_job.seed_config_json, run.seed_url, crawl_job.max_pages
            )
        except Exception as exc:
            await session.rollback()
            return await _mark_run_failed(
                session,
                run,
                error_message=str(exc),
                discovered_count=0,
                fetched_count=0,
                extracted_count=0,
                error_count=1,
            )

        discovered_count = len(discovered_pages)
        fetched_count = 0
        extracted_count = 0
        successful_pages = 0
        failed_pages = 0

        for page in discovered_pages:
            try:
                fetched = adapter.fetch(page)
                raw_page = await _persist_raw_page_and_event(
                    session=session,
                    source_site=source_site,
                    crawl_run=run,
                    crawl_job=crawl_job,
                    page=page,
                    fetched=fetched,
                )
                fetched_count += 1

                request = ExtractionAgentRequest(
                    raw_page_id=raw_page.id,
                    source_site_id=source_site.id,
                    crawl_run_id=run.id,
                    requested_url=fetched.requested_url,
                    final_url=fetched.final_url,
                    site_type=source_site.site_type,
                    parser_profile=crawl_job.parser_profile,
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
                    status="succeeded",
                    related_crawl_run_id=run.id,
                    related_raw_page_id=raw_page.id,
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
                    run=run,
                    raw_page=raw_page,
                    requested_url=fetched.requested_url,
                    response=response,
                )

                inserted_item_ids = await normalize_extraction_response(
                    session,
                    source_site=source_site,
                    raw_page=raw_page,
                    response=response,
                )
                raw_page.parse_status = "parsed"
                raw_page.extraction_method = "extraction_agent"
                raw_page.extraction_confidence = response.extraction_confidence
                await session.commit()

                extracted_count += len(inserted_item_ids)
                successful_pages += 1
            except Exception as exc:
                failed_pages += 1
                await session.rollback()
                await _record_page_failure_event(
                    session=session, run_id=run.id, requested_url=page.requested_url, exc=exc
                )

        if successful_pages == len(discovered_pages) and discovered_pages:
            final_status: RunOutcome = "success"
            error_message = None
        elif successful_pages > 0:
            final_status = "partial"
            error_message = f"{failed_pages} page(s) failed during worker processing."
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
        run.error_count = failed_pages
        run.error_message = error_message
        crawl_job.last_run_at = run.finished_at
        await session.commit()
        return final_status


async def _persist_raw_page_and_event(
    *,
    session: AsyncSession,
    source_site: SourceSite,
    crawl_run: CrawlRun,
    crawl_job: CrawlJob,
    page: DiscoveredPage,
    fetched: FetchedPage,
) -> RawPage:
    raw_page = RawPage(
        source_site_id=source_site.id,
        crawl_run_id=crawl_run.id,
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
        parser_profile=crawl_job.parser_profile,
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
            crawl_run_id=crawl_run.id,
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


async def _record_extraction_called_event(
    *,
    session: AsyncSession,
    run: CrawlRun,
    raw_page: RawPage,
    requested_url: str,
    response: ExtractionAgentResponse,
) -> None:
    session.add(
        CrawlRunEvent(
            crawl_run_id=run.id,
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
    *, session: AsyncSession, run_id: uuid.UUID, requested_url: str, exc: Exception
) -> None:
    session.add(
        CrawlRunEvent(
            crawl_run_id=run_id,
            stage="worker",
            level="error",
            event_type="page_failed",
            message=str(exc),
            related_url=requested_url,
            related_raw_page_id=None,
            related_content_item_id=None,
            counters_json={"error_count_increment": 1},
            agent_trace_json={},
        )
    )
    await session.commit()


async def _mark_run_failed(
    session: AsyncSession,
    run: CrawlRun,
    *,
    error_message: str,
    discovered_count: int,
    fetched_count: int,
    extracted_count: int,
    error_count: int,
) -> Literal["failed"]:
    run.status = "failed"
    run.finished_at = utcnow()
    run.discovered_count = discovered_count
    run.fetched_count = fetched_count
    run.parsed_count = 0
    run.extracted_count = extracted_count
    run.error_count = error_count
    run.error_message = error_message
    session.add(
        CrawlRunEvent(
            crawl_run_id=run.id,
            stage="worker",
            level="error",
            event_type="run_failed",
            message=error_message,
            related_url=run.seed_url,
            related_raw_page_id=None,
            related_content_item_id=None,
            counters_json={"error_count": error_count},
            agent_trace_json={},
        )
    )
    await session.commit()
    return "failed"


def _body_hash(fetched: FetchedPage) -> str:
    body = {
        "raw_html": fetched.raw_html,
        "raw_markdown": fetched.raw_markdown,
        "raw_json": fetched.raw_json,
    }
    return stable_hash(json.dumps(body, sort_keys=True, separators=(",", ":")))
