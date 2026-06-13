from app.main import app
from fastapi.testclient import TestClient

from worker.app.runner import run_once


def test_worker_persists_raw_page_before_extraction_and_marks_success() -> None:
    client = TestClient(app)
    source = client.post(
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
    ).json()
    job = client.post(
        "/jobs",
        json={
            "source_site_id": source["id"],
            "name": "Manual official crawl",
            "trigger_mode": "manual",
            "cron_expr": None,
            "seed_config_json": {"urls": ["https://example.com/a"]},
            "parser_profile": "official_site",
            "max_pages": 1,
            "enabled": True,
            "agent_policy_json": {"extraction_mode": "hybrid"},
        },
    ).json()
    run = client.post(
        f"/jobs/{job['id']}/trigger", json={"seed_url": "https://example.com/a"}
    ).json()

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
