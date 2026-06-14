import os
import subprocess
import sys
import uuid
from pathlib import Path
from typing import cast

import app.db.session as db_session_module
import pytest
from app.agents.contracts import ExtractionAgentRequest, ExtractionAgentResponse, ExtractionItem
from app.db.base import utcnow
from app.main import app
from app.models.content import ContentItem, RawPage
from app.models.control import CrawlJob, CrawlRun, SourceSite
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event, insert, select, text
from sqlalchemy.orm import Session

import worker.app.runner as runner_module
from worker.app.adapters import DiscoveredPage, FetchedPage, OfficialSiteAdapter
from worker.app.normalizer import normalize_extraction_response
from worker.app.runner import run_once

REPO_ROOT = Path(__file__).resolve().parents[2]


def _create_worker_job(
    client: TestClient,
    *,
    parser_profile: str = "official_site",
    urls: list[str] | None = None,
    max_pages: int = 1,
    seed_url: str = "https://example.com/a",
) -> dict[str, object]:
    source = cast(
        dict[str, object],
        client.post(
            "/sources",
            json={
                "name": "Demo Official Site",
                "site_type": "docs",
                "base_url": "https://example.com",
                "allowed_domains": ["example.com"],
                "fetch_mode": "manual",
                "default_language": "en",
                "active": True,
                "config_json": {},
            },
        ).json(),
    )
    configured_urls = [seed_url] if urls is None else urls
    return cast(
        dict[str, object],
        client.post(
            "/jobs",
            json={
                "source_site_id": source["id"],
                "name": "Manual official crawl",
                "trigger_mode": "manual",
                "cron_expr": None,
                "seed_config_json": {"urls": configured_urls},
                "parser_profile": parser_profile,
                "max_pages": max_pages,
                "enabled": True,
                "agent_policy_json": {"extraction_mode": "hybrid"},
            },
        ).json(),
    )


def _trigger_worker_job(
    client: TestClient, job: dict[str, object], *, seed_url: str = "https://example.com/a"
) -> dict[str, object]:
    return cast(
        dict[str, object],
        client.post(f"/jobs/{job['id']}/trigger", json={"seed_url": seed_url}).json(),
    )


def _queue_worker_run(
    client: TestClient,
    *,
    parser_profile: str = "official_site",
    urls: list[str] | None = None,
    max_pages: int = 1,
    seed_url: str = "https://example.com/a",
) -> dict[str, object]:
    job = _create_worker_job(
        client,
        parser_profile=parser_profile,
        urls=urls,
        max_pages=max_pages,
        seed_url=seed_url,
    )
    return _trigger_worker_job(client, job, seed_url=seed_url)


def _fetch_db_rows(query: str, params: dict[str, object]) -> list[dict[str, object]]:
    engine = create_engine(os.environ["SYNC_DATABASE_URL"], pool_pre_ping=True)
    try:
        with engine.connect() as connection:
            rows = connection.execute(text(query), params).mappings().all()
            return [dict(row) for row in rows]
    finally:
        engine.dispose()


def test_run_worker_once_script_executes_directly_with_no_queued_runs() -> None:
    env = os.environ.copy()
    env.pop("PYTHONPATH", None)

    completed = subprocess.run(
        [sys.executable, "scripts/run_worker_once.py"],
        cwd=REPO_ROOT,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    assert "WorkerRunResult(claimed=0, succeeded=0, partial=0, failed=0)" in completed.stdout


def test_worker_persists_raw_page_before_extraction_and_marks_success() -> None:
    client = TestClient(app)
    run = _queue_worker_run(client)

    result = run_once(run_limit=1)
    assert result.claimed == 1
    assert result.succeeded == 1

    detail = client.get(f"/runs/{run['id']}").json()
    assert detail["status"] == "success"
    assert detail["fetched_count"] == 1
    assert detail["extracted_count"] >= 1

    events = client.get(f"/runs/{run['id']}/events").json()["items"]
    event_types = [event["event_type"] for event in events]
    assert event_types.index("raw_page_persisted") < event_types.index(
        "extraction_agent_called"
    )

    agent_calls = _fetch_db_rows(
        "select status from agent_calls where related_crawl_run_id = CAST(:run_id AS uuid)",
        {"run_id": run["id"]},
    )
    assert [agent_call["status"] for agent_call in agent_calls] == ["success"]


def test_unsupported_parser_profile_marks_run_failed_without_raising() -> None:
    client = TestClient(app)
    run = _queue_worker_run(client, parser_profile="unsupported_profile")

    result = run_once(run_limit=1)
    assert result.claimed == 1
    assert result.succeeded == 0
    assert result.partial == 0
    assert result.failed == 1

    detail = client.get(f"/runs/{run['id']}").json()
    assert detail["status"] == "failed"
    assert detail["discovered_count"] == 0
    assert detail["fetched_count"] == 0
    assert detail["extracted_count"] == 0
    assert detail["error_count"] == 1
    assert "Unsupported parser_profile: unsupported_profile" in detail["error_message"]

    events = client.get(f"/runs/{run['id']}/events").json()["items"]
    run_failed_events = [event for event in events if event["event_type"] == "run_failed"]
    assert len(run_failed_events) == 1
    assert "Unsupported parser_profile: unsupported_profile" in run_failed_events[0]["message"]


def test_unexpected_process_run_exception_marks_claimed_run_failed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fail_after_claim(run_id: uuid.UUID) -> runner_module.RunOutcome:
        raise RuntimeError(f"unexpected finalization failure for {run_id}")

    monkeypatch.setattr(runner_module, "_process_run", fail_after_claim)
    client = TestClient(app)
    run = _queue_worker_run(client)

    result = run_once(run_limit=1)
    assert result.claimed == 1
    assert result.succeeded == 0
    assert result.partial == 0
    assert result.failed == 1

    detail = client.get(f"/runs/{run['id']}").json()
    assert detail["status"] == "failed"
    assert detail["error_count"] == 1
    assert "unexpected finalization failure" in detail["error_message"]

    events = client.get(f"/runs/{run['id']}/events").json()["items"]
    run_failed_events = [event for event in events if event["event_type"] == "run_failed"]
    assert len(run_failed_events) == 1
    assert "unexpected finalization failure" in run_failed_events[0]["message"]


def test_zero_discovered_pages_marks_run_failed_with_explicit_message() -> None:
    client = TestClient(app)
    run = _queue_worker_run(client, urls=[], seed_url="")

    result = run_once(run_limit=1)
    assert result.claimed == 1
    assert result.succeeded == 0
    assert result.partial == 0
    assert result.failed == 1

    detail = client.get(f"/runs/{run['id']}").json()
    assert detail["status"] == "failed"
    assert detail["discovered_count"] == 0
    assert detail["fetched_count"] == 0
    assert detail["parsed_count"] == 0
    assert detail["extracted_count"] == 0
    assert detail["deduped_count"] == 0
    assert detail["error_count"] == 1
    assert detail["error_message"] == "No pages were discovered."

    events = client.get(f"/runs/{run['id']}/events").json()["items"]
    run_failed_events = [event for event in events if event["event_type"] == "run_failed"]
    assert len(run_failed_events) == 1
    assert run_failed_events[0]["message"] == "No pages were discovered."


def test_agent_fallback_response_records_fallback_used_status(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fallback_agent(
        request: ExtractionAgentRequest,
    ) -> tuple[ExtractionAgentResponse, int]:
        return (
            ExtractionAgentResponse(
                page_kind="other",
                items=[
                    ExtractionItem(
                        item_type="article",
                        external_item_id=None,
                        title=None,
                        author=None,
                        published_at=None,
                        body_text=request.raw_markdown or "fallback body",
                        summary_text=None,
                        tags=[],
                        parent_ref=None,
                        thread_root_ref=None,
                        metadata_json={"fallback": True},
                    )
                ],
                extraction_confidence=0.1,
                warnings=["agent_fallback_used"],
                trace_summary_json={"fallback": True, "provider": "fake-failing"},
            ),
            7,
        )

    monkeypatch.setattr(runner_module, "_call_extraction_agent", fallback_agent)
    client = TestClient(app)
    run = _queue_worker_run(client)

    result = run_once(run_limit=1)
    assert result.claimed == 1
    assert result.succeeded == 1

    agent_calls = _fetch_db_rows(
        "select status from agent_calls where related_crawl_run_id = CAST(:run_id AS uuid)",
        {"run_id": run["id"]},
    )
    assert [agent_call["status"] for agent_call in agent_calls] == ["fallback_used"]


def test_post_raw_page_failure_marks_raw_page_failed_and_links_event(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail_after_raw_page_persistence(
        request: ExtractionAgentRequest,
    ) -> tuple[ExtractionAgentResponse, int]:
        raise RuntimeError(f"agent exploded for raw page {request.raw_page_id}")

    monkeypatch.setattr(runner_module, "_call_extraction_agent", fail_after_raw_page_persistence)
    client = TestClient(app)
    run = _queue_worker_run(client)

    result = run_once(run_limit=1)
    assert result.claimed == 1
    assert result.succeeded == 0
    assert result.partial == 0
    assert result.failed == 1

    detail = client.get(f"/runs/{run['id']}").json()
    assert detail["status"] == "failed"
    assert detail["fetched_count"] == 1
    assert detail["parsed_count"] == 0
    assert detail["error_count"] == 1

    raw_pages = _fetch_db_rows(
        """
        select id, parse_status, parse_error
        from raw_pages
        where crawl_run_id = CAST(:run_id AS uuid)
        """,
        {"run_id": run["id"]},
    )
    assert len(raw_pages) == 1
    raw_page = raw_pages[0]
    assert raw_page["parse_status"] == "failed"
    assert "agent exploded for raw page" in str(raw_page["parse_error"])

    failure_events = _fetch_db_rows(
        """
        select related_raw_page_id, message
        from crawl_run_events
        where crawl_run_id = CAST(:run_id AS uuid)
          and event_type = 'page_failed'
        """,
        {"run_id": run["id"]},
    )
    assert len(failure_events) == 1
    assert str(failure_events[0]["related_raw_page_id"]) == str(raw_page["id"])
    assert "agent exploded for raw page" in str(failure_events[0]["message"])


def test_replaying_same_deterministic_page_reuses_existing_content_item() -> None:
    client = TestClient(app)
    seed_url = "https://example.com/replay"
    job = _create_worker_job(client, urls=[seed_url], seed_url=seed_url)
    first_run = _trigger_worker_job(client, job, seed_url=seed_url)
    second_run = _trigger_worker_job(client, job, seed_url=seed_url)

    first_result = run_once(run_limit=1)
    second_result = run_once(run_limit=1)
    assert first_result.claimed == 1
    assert first_result.succeeded == 1
    assert second_result.claimed == 1
    assert second_result.succeeded == 1

    first_detail = client.get(f"/runs/{first_run['id']}").json()
    second_detail = client.get(f"/runs/{second_run['id']}").json()
    assert first_detail["status"] == "success"
    assert first_detail["extracted_count"] == 1
    assert first_detail["deduped_count"] == 0
    assert second_detail["status"] == "success"
    assert second_detail["extracted_count"] == 0
    assert second_detail["deduped_count"] >= 1

    content_items = _fetch_db_rows(
        "select id, dedup_key from content_items order by created_at asc",
        {},
    )
    assert len(content_items) == 1

    raw_pages = _fetch_db_rows(
        """
        select parse_status
        from raw_pages
        order by fetched_at asc
        """,
        {},
    )
    assert [raw_page["parse_status"] for raw_page in raw_pages] == ["parsed", "parsed"]


async def test_normalizer_does_not_mutate_reused_item_relationships_on_replay() -> None:
    async with db_session_module.AsyncSessionLocal() as session:
        source_site = SourceSite(
            name="Replay Source",
            site_type="forum",
            base_url="https://example.com",
            allowed_domains=["example.com"],
            fetch_mode="manual",
            default_language="en",
            active=True,
            config_json={},
        )
        session.add(source_site)
        await session.flush()

        crawl_job = CrawlJob(
            source_site_id=source_site.id,
            name="Replay job",
            trigger_mode="manual",
            cron_expr=None,
            seed_config_json={"urls": ["https://example.com/replay-thread"]},
            parser_profile="forum_thread",
            max_pages=1,
            enabled=True,
            agent_policy_json={},
        )
        session.add(crawl_job)
        await session.flush()

        crawl_run = CrawlRun(
            source_site_id=source_site.id,
            crawl_job_id=crawl_job.id,
            trigger_type="manual",
            seed_url="https://example.com/replay-thread",
            status="running",
            config_snapshot_json={},
        )
        session.add(crawl_run)
        await session.flush()

        raw_page = RawPage(
            source_site_id=source_site.id,
            crawl_run_id=crawl_run.id,
            requested_url="https://example.com/replay-thread",
            final_url="https://example.com/replay-thread",
            http_status=200,
            content_type="text/html",
            response_headers_json={},
            raw_html="<html></html>",
            raw_text="stable replay body",
            raw_json={},
            fetched_at=utcnow(),
            fetch_error=None,
            parser_profile="forum_thread",
            extraction_method=None,
            extraction_confidence=None,
            parse_status="pending",
            parse_error=None,
            body_hash="stable-replay-body-hash",
        )
        session.add(raw_page)
        await session.flush()

        first_response = ExtractionAgentResponse(
            page_kind="forum_thread",
            items=[
                ExtractionItem(
                    item_type="comment",
                    external_item_id="stable-comment",
                    title=None,
                    author=None,
                    published_at=None,
                    body_text="stable replay body",
                    summary_text=None,
                    tags=[],
                    parent_ref=None,
                    thread_root_ref=None,
                    metadata_json={},
                )
            ],
            extraction_confidence=0.8,
            warnings=[],
            trace_summary_json={"provider": "test"},
        )
        first_normalization = await normalize_extraction_response(
            session,
            source_site=source_site,
            raw_page=raw_page,
            response=first_response,
        )
        await session.flush()

        reused_item_id = first_normalization.content_item_ids[0]
        reused_item = await session.get(ContentItem, reused_item_id)
        assert reused_item is not None
        original_parent_item_id = reused_item.parent_item_id
        original_thread_root_id = reused_item.thread_root_id
        assert original_parent_item_id is None
        assert original_thread_root_id is None

        second_response = ExtractionAgentResponse(
            page_kind="forum_thread",
            items=[
                ExtractionItem(
                    item_type="thread",
                    external_item_id="conflicting-thread",
                    title="Conflicting thread",
                    author=None,
                    published_at=None,
                    body_text="new conflicting root body",
                    summary_text=None,
                    tags=[],
                    parent_ref=None,
                    thread_root_ref=None,
                    metadata_json={},
                ),
                ExtractionItem(
                    item_type="comment",
                    external_item_id="stable-comment",
                    title=None,
                    author=None,
                    published_at=None,
                    body_text="stable replay body",
                    summary_text=None,
                    tags=[],
                    parent_ref="conflicting-thread",
                    thread_root_ref="conflicting-thread",
                    metadata_json={},
                ),
            ],
            extraction_confidence=0.8,
            warnings=[],
            trace_summary_json={"provider": "test"},
        )
        second_normalization = await normalize_extraction_response(
            session,
            source_site=source_site,
            raw_page=raw_page,
            response=second_response,
        )
        await session.flush()
        await session.refresh(reused_item)

    assert second_normalization.reused_item_ids == [reused_item_id]
    assert second_normalization.deduped_count == 1
    assert reused_item.parent_item_id == original_parent_item_id
    assert reused_item.thread_root_id == original_thread_root_id


async def test_normalizer_duplicate_occurrence_does_not_overwrite_creator_relationships() -> None:
    async with db_session_module.AsyncSessionLocal() as session:
        source_site = SourceSite(
            name="Duplicate Occurrence Source",
            site_type="forum",
            base_url="https://example.com",
            allowed_domains=["example.com"],
            fetch_mode="manual",
            default_language="en",
            active=True,
            config_json={},
        )
        session.add(source_site)
        await session.flush()

        crawl_job = CrawlJob(
            source_site_id=source_site.id,
            name="Duplicate occurrence job",
            trigger_mode="manual",
            cron_expr=None,
            seed_config_json={"urls": ["https://example.com/duplicate-thread"]},
            parser_profile="forum_thread",
            max_pages=1,
            enabled=True,
            agent_policy_json={},
        )
        session.add(crawl_job)
        await session.flush()

        crawl_run = CrawlRun(
            source_site_id=source_site.id,
            crawl_job_id=crawl_job.id,
            trigger_type="manual",
            seed_url="https://example.com/duplicate-thread",
            status="running",
            config_snapshot_json={},
        )
        session.add(crawl_run)
        await session.flush()

        raw_page = RawPage(
            source_site_id=source_site.id,
            crawl_run_id=crawl_run.id,
            requested_url="https://example.com/duplicate-thread",
            final_url="https://example.com/duplicate-thread",
            http_status=200,
            content_type="text/html",
            response_headers_json={},
            raw_html="<html></html>",
            raw_text="duplicate body",
            raw_json={},
            fetched_at=utcnow(),
            fetch_error=None,
            parser_profile="forum_thread",
            extraction_method=None,
            extraction_confidence=None,
            parse_status="pending",
            parse_error=None,
            body_hash="duplicate-body-hash",
        )
        session.add(raw_page)
        await session.flush()

        response = ExtractionAgentResponse(
            page_kind="forum_thread",
            items=[
                ExtractionItem(
                    item_type="thread",
                    external_item_id="thread-a",
                    title="Thread A",
                    author=None,
                    published_at=None,
                    body_text="thread a body",
                    summary_text=None,
                    tags=[],
                    parent_ref=None,
                    thread_root_ref=None,
                    metadata_json={},
                ),
                ExtractionItem(
                    item_type="comment",
                    external_item_id="duplicate-comment",
                    title=None,
                    author=None,
                    published_at=None,
                    body_text="duplicate comment body",
                    summary_text=None,
                    tags=[],
                    parent_ref="thread-a",
                    thread_root_ref="thread-a",
                    metadata_json={},
                ),
                ExtractionItem(
                    item_type="thread",
                    external_item_id="thread-b",
                    title="Thread B",
                    author=None,
                    published_at=None,
                    body_text="thread b body",
                    summary_text=None,
                    tags=[],
                    parent_ref=None,
                    thread_root_ref=None,
                    metadata_json={},
                ),
                ExtractionItem(
                    item_type="comment",
                    external_item_id="duplicate-comment",
                    title=None,
                    author=None,
                    published_at=None,
                    body_text="duplicate comment body",
                    summary_text=None,
                    tags=[],
                    parent_ref="thread-b",
                    thread_root_ref="thread-b",
                    metadata_json={},
                ),
            ],
            extraction_confidence=0.8,
            warnings=[],
            trace_summary_json={"provider": "test"},
        )

        normalization = await normalize_extraction_response(
            session,
            source_site=source_site,
            raw_page=raw_page,
            response=response,
        )
        await session.flush()

        first_thread_id = normalization.content_item_ids[0]
        first_comment_id = normalization.content_item_ids[1]
        second_thread_id = normalization.content_item_ids[2]
        second_comment_id = normalization.content_item_ids[3]
        duplicate_comment = await session.get(ContentItem, first_comment_id)
        assert duplicate_comment is not None
        same_dedup_key_comments = (
            await session.scalars(
                select(ContentItem).where(ContentItem.dedup_key == duplicate_comment.dedup_key)
            )
        ).all()

    assert first_comment_id == second_comment_id
    assert len(same_dedup_key_comments) == 1
    assert normalization.reused_item_ids == [first_comment_id]
    assert normalization.deduped_count == 1
    assert duplicate_comment.parent_item_id == first_thread_id
    assert duplicate_comment.thread_root_id == first_thread_id
    assert duplicate_comment.parent_item_id != second_thread_id
    assert duplicate_comment.thread_root_id != second_thread_id


async def test_normalizer_recovers_when_dedup_insert_loses_race() -> None:
    async with db_session_module.AsyncSessionLocal() as session:
        source_site = SourceSite(
            name="Race Source",
            site_type="forum",
            base_url="https://example.com",
            allowed_domains=["example.com"],
            fetch_mode="manual",
            default_language="en",
            active=True,
            config_json={},
        )
        session.add(source_site)
        await session.flush()

        crawl_job = CrawlJob(
            source_site_id=source_site.id,
            name="Race job",
            trigger_mode="manual",
            cron_expr=None,
            seed_config_json={"urls": ["https://example.com/race"]},
            parser_profile="forum_thread",
            max_pages=1,
            enabled=True,
            agent_policy_json={},
        )
        session.add(crawl_job)
        await session.flush()

        crawl_run = CrawlRun(
            source_site_id=source_site.id,
            crawl_job_id=crawl_job.id,
            trigger_type="manual",
            seed_url="https://example.com/race",
            status="running",
            config_snapshot_json={},
        )
        session.add(crawl_run)
        await session.flush()

        raw_page = RawPage(
            source_site_id=source_site.id,
            crawl_run_id=crawl_run.id,
            requested_url="https://example.com/race",
            final_url="https://example.com/race",
            http_status=200,
            content_type="text/html",
            response_headers_json={},
            raw_html="<html></html>",
            raw_text="race body",
            raw_json={},
            fetched_at=utcnow(),
            fetch_error=None,
            parser_profile="forum_thread",
            extraction_method=None,
            extraction_confidence=None,
            parse_status="pending",
            parse_error=None,
            body_hash="race-body-hash",
        )
        session.add(raw_page)
        await session.commit()

        conflict_item_id = uuid.uuid4()
        conflict_inserted = False

        def insert_conflicting_winner(sync_session: Session, *_args: object) -> None:
            nonlocal conflict_inserted
            if conflict_inserted:
                return
            pending_item = next(
                (
                    item
                    for item in sync_session.new
                    if isinstance(item, ContentItem)
                ),
                None,
            )
            if pending_item is None:
                return

            conflict_inserted = True
            engine = create_engine(os.environ["SYNC_DATABASE_URL"], pool_pre_ping=True)
            try:
                with engine.begin() as connection:
                    connection.execute(
                        insert(ContentItem).values(
                            id=conflict_item_id,
                            source_site_id=pending_item.source_site_id,
                            raw_page_id=pending_item.raw_page_id,
                            crawl_run_id=pending_item.crawl_run_id,
                            author_id=pending_item.author_id,
                            parent_item_id=None,
                            thread_root_id=None,
                            item_type=pending_item.item_type,
                            title=pending_item.title,
                            canonical_url=pending_item.canonical_url,
                            source_url=pending_item.source_url,
                            published_at=pending_item.published_at,
                            language=pending_item.language,
                            raw_text=pending_item.raw_text,
                            cleaned_text=pending_item.cleaned_text,
                            summary_text=pending_item.summary_text,
                            structured_by=pending_item.structured_by,
                            extraction_confidence=pending_item.extraction_confidence,
                            tags=pending_item.tags,
                            metadata_json=pending_item.metadata_json,
                            content_hash=pending_item.content_hash,
                            dedup_key=pending_item.dedup_key,
                            search_tsv=None,
                        )
                    )
            finally:
                engine.dispose()

        event.listen(session.sync_session, "before_flush", insert_conflicting_winner)
        try:
            response = ExtractionAgentResponse(
                page_kind="forum_thread",
                items=[
                    ExtractionItem(
                        item_type="comment",
                        external_item_id="race-comment-1",
                        title=None,
                        author=None,
                        published_at=None,
                        body_text="race body",
                        summary_text=None,
                        tags=[],
                        parent_ref=None,
                        thread_root_ref=None,
                        metadata_json={},
                    )
                ],
                extraction_confidence=0.8,
                warnings=[],
                trace_summary_json={"provider": "test"},
            )

            normalization = await normalize_extraction_response(
                session,
                source_site=source_site,
                raw_page=raw_page,
                response=response,
            )
            await session.commit()
        finally:
            event.remove(session.sync_session, "before_flush", insert_conflicting_winner)

        content_items = (await session.scalars(select(ContentItem))).all()

    assert conflict_inserted is True
    assert len(content_items) == 1
    assert content_items[0].id == conflict_item_id
    assert normalization.content_item_ids == [conflict_item_id]
    assert normalization.created_item_ids == []
    assert normalization.reused_item_ids == [conflict_item_id]
    assert normalization.deduped_count == 1


async def test_normalizer_distinguishes_same_body_items_by_type_and_external_id() -> None:
    async with db_session_module.AsyncSessionLocal() as session:
        source_site = SourceSite(
            name="Normalizer Source",
            site_type="forum",
            base_url="https://example.com",
            allowed_domains=["example.com"],
            fetch_mode="manual",
            default_language="en",
            active=True,
            config_json={},
        )
        session.add(source_site)
        await session.flush()

        crawl_job = CrawlJob(
            source_site_id=source_site.id,
            name="Normalizer job",
            trigger_mode="manual",
            cron_expr=None,
            seed_config_json={"urls": ["https://example.com/thread"]},
            parser_profile="forum_thread",
            max_pages=1,
            enabled=True,
            agent_policy_json={},
        )
        session.add(crawl_job)
        await session.flush()

        crawl_run = CrawlRun(
            source_site_id=source_site.id,
            crawl_job_id=crawl_job.id,
            trigger_type="manual",
            seed_url="https://example.com/thread",
            status="running",
            config_snapshot_json={},
        )
        session.add(crawl_run)
        await session.flush()

        raw_page = RawPage(
            source_site_id=source_site.id,
            crawl_run_id=crawl_run.id,
            requested_url="https://example.com/thread",
            final_url="https://example.com/thread",
            http_status=200,
            content_type="text/html",
            response_headers_json={},
            raw_html="<html></html>",
            raw_text="same body",
            raw_json={},
            fetched_at=utcnow(),
            fetch_error=None,
            parser_profile="forum_thread",
            extraction_method=None,
            extraction_confidence=None,
            parse_status="pending",
            parse_error=None,
            body_hash="body-hash",
        )
        session.add(raw_page)
        await session.flush()

        response = ExtractionAgentResponse(
            page_kind="forum_thread",
            items=[
                ExtractionItem(
                    item_type="article",
                    external_item_id="item-1",
                    title="Shared body article",
                    author=None,
                    published_at=None,
                    body_text="same body",
                    summary_text=None,
                    tags=[],
                    parent_ref=None,
                    thread_root_ref=None,
                    metadata_json={},
                ),
                ExtractionItem(
                    item_type="comment",
                    external_item_id="item-2",
                    title=None,
                    author=None,
                    published_at=None,
                    body_text="same body",
                    summary_text=None,
                    tags=[],
                    parent_ref=None,
                    thread_root_ref=None,
                    metadata_json={},
                ),
                ExtractionItem(
                    item_type="comment",
                    external_item_id="item-3",
                    title=None,
                    author=None,
                    published_at=None,
                    body_text="same body",
                    summary_text=None,
                    tags=[],
                    parent_ref=None,
                    thread_root_ref=None,
                    metadata_json={},
                ),
            ],
            extraction_confidence=0.8,
            warnings=[],
            trace_summary_json={"provider": "test"},
        )

        normalization = await normalize_extraction_response(
            session,
            source_site=source_site,
            raw_page=raw_page,
            response=response,
        )
        content_items = (
            await session.scalars(select(ContentItem).order_by(ContentItem.created_at.asc()))
        ).all()

    assert len(content_items) == 3
    assert len({item.id for item in content_items}) == 3
    assert len({item.dedup_key for item in content_items}) == 3
    assert set(normalization.content_item_ids) == {item.id for item in content_items}
    assert set(normalization.created_item_ids) == {item.id for item in content_items}
    assert normalization.reused_item_ids == []
    assert normalization.deduped_count == 0


def test_page_level_failure_for_one_page_marks_run_partial(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail_second_fetch(self: OfficialSiteAdapter, page: DiscoveredPage) -> FetchedPage:
        if page.requested_url == "https://example.com/b":
            raise RuntimeError("test fetch failure for second page")
        return original_fetch(self, page)

    original_fetch = OfficialSiteAdapter.fetch
    monkeypatch.setattr(OfficialSiteAdapter, "fetch", fail_second_fetch)
    client = TestClient(app)
    run = _queue_worker_run(
        client,
        urls=["https://example.com/a", "https://example.com/b"],
        max_pages=2,
    )

    result = run_once(run_limit=1)
    assert result.claimed == 1
    assert result.succeeded == 0
    assert result.partial == 1
    assert result.failed == 0

    detail = client.get(f"/runs/{run['id']}").json()
    assert detail["status"] == "partial"
    assert detail["discovered_count"] == 2
    assert detail["fetched_count"] == 1
    assert detail["parsed_count"] == 1
    assert detail["extracted_count"] == 1
    assert detail["deduped_count"] == 0
    assert detail["error_count"] == 1
    assert detail["error_message"] == "1 page(s) failed during worker processing."

    events = client.get(f"/runs/{run['id']}/events").json()["items"]
    page_failed_events = [event for event in events if event["event_type"] == "page_failed"]
    assert len(page_failed_events) == 1
    assert page_failed_events[0]["related_url"] == "https://example.com/b"
    assert "test fetch failure for second page" in page_failed_events[0]["message"]


def test_page_level_failure_for_all_pages_marks_run_failed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail_fetch(self: object, page: object) -> None:
        raise RuntimeError("test fetch failure")

    monkeypatch.setattr(OfficialSiteAdapter, "fetch", fail_fetch)
    client = TestClient(app)
    run = _queue_worker_run(
        client,
        urls=["https://example.com/a", "https://example.com/b"],
        max_pages=2,
    )

    result = run_once(run_limit=1)
    assert result.claimed == 1
    assert result.succeeded == 0
    assert result.partial == 0
    assert result.failed == 1

    detail = client.get(f"/runs/{run['id']}").json()
    assert detail["status"] == "failed"
    assert detail["discovered_count"] == 2
    assert detail["fetched_count"] == 0
    assert detail["parsed_count"] == 0
    assert detail["extracted_count"] == 0
    assert detail["error_count"] == 2
    assert detail["error_message"] == "2 page(s) failed during worker processing."

    events = client.get(f"/runs/{run['id']}/events").json()["items"]
    page_failed_events = [event for event in events if event["event_type"] == "page_failed"]
    assert len(page_failed_events) == 2
    assert {event["related_url"] for event in page_failed_events} == {
        "https://example.com/a",
        "https://example.com/b",
    }
