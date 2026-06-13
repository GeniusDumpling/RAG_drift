import os
import subprocess
import sys
from pathlib import Path
from typing import cast

import pytest
from app.agents.contracts import ExtractionAgentRequest, ExtractionAgentResponse, ExtractionItem
from app.main import app
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text

import worker.app.runner as runner_module
from worker.app.adapters import OfficialSiteAdapter
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
    return cast(
        dict[str, object],
        client.post(
            "/jobs",
            json={
                "source_site_id": source["id"],
                "name": "Manual official crawl",
                "trigger_mode": "manual",
                "cron_expr": None,
                "seed_config_json": {"urls": urls or [seed_url]},
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
    assert second_detail["status"] == "success"

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
