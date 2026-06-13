import hashlib

from app.main import app
from fastapi.testclient import TestClient


def test_content_list_and_detail_hydrate_source_raw_run_and_chunks() -> None:
    client = TestClient(app)
    source = client.post(
        "/sources",
        json={
            "name": "Forum Demo",
            "site_type": "forum",
            "base_url": "https://forum.example.com",
            "allowed_domains": ["forum.example.com"],
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
            "name": "Forum crawl",
            "trigger_mode": "manual",
            "cron_expr": None,
            "seed_config_json": {"urls": ["https://forum.example.com/t/1"]},
            "parser_profile": "forum_thread",
            "max_pages": 2,
            "enabled": True,
            "agent_policy_json": {"extraction_mode": "hybrid"},
        },
    ).json()
    run = client.post(
        f"/jobs/{job['id']}/trigger", json={"seed_url": "https://forum.example.com/t/1"}
    ).json()

    raw_html = "<html><h1>Thread title</h1><p>Body text about telemetry.</p></html>"
    expected_body_hash = hashlib.sha256(raw_html.encode("utf-8")).hexdigest()
    ingest_response = client.post(
        "/contents/test-ingest",
        json={
            "source_site_id": source["id"],
            "crawl_run_id": run["id"],
            "requested_url": "https://forum.example.com/t/1",
            "final_url": "https://forum.example.com/t/1",
            "raw_html": raw_html,
            "item_type": "thread",
            "title": "Thread title",
            "cleaned_text": "Body text about telemetry.",
            "summary_text": "Telemetry discussion.",
            "tags": ["telemetry"],
        },
    )
    assert ingest_response.status_code == 201
    content_id = ingest_response.json()["content_item_id"]

    list_response = client.get("/contents?item_type=thread")
    assert list_response.status_code == 200
    assert list_response.json()["items"][0]["title"] == "Thread title"

    detail_response = client.get(f"/contents/{content_id}")
    assert detail_response.status_code == 200
    detail = detail_response.json()
    assert detail["raw_page"]["requested_url"] == "https://forum.example.com/t/1"
    assert detail["raw_page"]["body_hash"] == expected_body_hash
    assert "content_hash" not in detail["raw_page"]
    assert detail["source"]["name"] == "Forum Demo"
    assert detail["chunks"] != []
