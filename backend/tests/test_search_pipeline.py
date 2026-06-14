from app.main import app
from fastapi.testclient import TestClient


def test_search_records_raw_and_optimized_query_and_returns_evidence() -> None:
    client = TestClient(app)
    source = client.post(
        "/sources",
        json={
            "name": "Docs",
            "site_type": "docs",
            "base_url": "https://docs.example.com",
            "allowed_domains": ["docs.example.com"],
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
            "name": "Docs crawl",
            "trigger_mode": "manual",
            "cron_expr": None,
            "seed_config_json": {"urls": ["https://docs.example.com/telemetry"]},
            "parser_profile": "official_site",
            "max_pages": 1,
            "enabled": True,
            "agent_policy_json": {"extraction_mode": "hybrid"},
        },
    ).json()
    run = client.post(
        f"/jobs/{job['id']}/trigger", json={"seed_url": "https://docs.example.com/telemetry"}
    ).json()
    client.post(
        "/contents/test-ingest",
        json={
            "source_site_id": source["id"],
            "crawl_run_id": run["id"],
            "requested_url": "https://docs.example.com/telemetry",
            "final_url": "https://docs.example.com/telemetry",
            "raw_html": "<p>Telemetry can be disabled in settings.</p>",
            "item_type": "doc_page",
            "title": "Telemetry settings",
            "cleaned_text": "Telemetry can be disabled in settings.",
            "summary_text": "How to disable telemetry.",
            "tags": ["telemetry", "settings"],
        },
    )

    response = client.post(
        "/search",
        json={
            "query": "disable telemetry",
            "mode": "search",
            "filters": {"source_site_id": source["id"], "item_type": "doc_page"},
            "top_k": 5,
        },
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["query"]["raw_query"] == "disable telemetry"
    assert payload["query"]["optimized_query_text"]
    assert payload["evidence"] != []
    assert payload["evidence"][0]["canonical_url"] == "https://docs.example.com/telemetry"
    assert payload["evidence"][0]["matched_by"] in {"keyword", "vector", "hybrid"}

    debug_response = client.get(f"/search-queries/{payload['query']['id']}")
    assert debug_response.status_code == 200
    assert debug_response.json()["raw_query"] == "disable telemetry"
