from app.main import app
from fastapi.testclient import TestClient


def test_create_source_job_and_trigger_run() -> None:
    client = TestClient(app)

    source_response = client.post(
        "/sources",
        json={
            "name": "Example Docs",
            "site_type": "docs",
            "base_url": "https://example.com/docs",
            "allowed_domains": ["example.com"],
            "fetch_mode": "manual",
            "default_language": "en",
            "active": True,
            "config_json": {"kind": "demo"},
        },
    )
    assert source_response.status_code == 201
    source = source_response.json()
    assert source["name"] == "Example Docs"
    assert source["active"] is True

    job_response = client.post(
        "/jobs",
        json={
            "source_site_id": source["id"],
            "name": "Manual docs crawl",
            "trigger_mode": "manual",
            "cron_expr": None,
            "seed_config_json": {"urls": ["https://example.com/docs/start"]},
            "parser_profile": "official_site",
            "max_pages": 5,
            "enabled": True,
            "agent_policy_json": {"extraction_mode": "hybrid"},
        },
    )
    assert job_response.status_code == 201
    job = job_response.json()
    assert job["source_site_id"] == source["id"]

    run_response = client.post(
        f"/jobs/{job['id']}/trigger", json={"seed_url": "https://example.com/docs/start"}
    )
    assert run_response.status_code == 201
    run = run_response.json()
    assert run["status"] == "queued"
    assert run["trigger_type"] == "manual"

    events_response = client.get(f"/runs/{run['id']}/events")
    assert events_response.status_code == 200
    assert events_response.json()["items"][0]["event_type"] == "run_queued"
