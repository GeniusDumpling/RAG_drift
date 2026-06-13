from typing import cast

import pytest
from app.main import app
from fastapi.testclient import TestClient

from worker.app.adapters import OfficialSiteAdapter
from worker.app.runner import run_once


def _queue_worker_run(
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
    job = cast(
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
    return cast(
        dict[str, object],
        client.post(f"/jobs/{job['id']}/trigger", json={"seed_url": seed_url}).json(),
    )


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
