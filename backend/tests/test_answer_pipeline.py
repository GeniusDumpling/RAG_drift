import pytest
from app.main import app
from fastapi.testclient import TestClient


def test_answer_returns_summary_with_supporting_evidence(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "app.services.retrieval.QdrantIndexer.search_chunks",
        lambda self, *, query_text, filters, top_k: [],
    )
    client = TestClient(app)
    source = client.post(
        "/sources",
        json={
            "name": "Answer Docs",
            "site_type": "docs",
            "base_url": "https://answer.example.com",
            "allowed_domains": ["answer.example.com"],
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
            "name": "Answer crawl",
            "trigger_mode": "manual",
            "cron_expr": None,
            "seed_config_json": {"urls": ["https://answer.example.com/settings"]},
            "parser_profile": "official_site",
            "max_pages": 1,
            "enabled": True,
            "agent_policy_json": {"extraction_mode": "hybrid"},
        },
    ).json()
    run = client.post(
        f"/jobs/{job['id']}/trigger", json={"seed_url": "https://answer.example.com/settings"}
    ).json()
    client.post(
        "/contents/test-ingest",
        json={
            "source_site_id": source["id"],
            "crawl_run_id": run["id"],
            "requested_url": "https://answer.example.com/settings",
            "final_url": "https://answer.example.com/settings",
            "raw_html": "<p>Set TELEMETRY_ENABLED=false to disable telemetry.</p>",
            "item_type": "doc_page",
            "title": "Settings reference",
            "cleaned_text": "Set TELEMETRY_ENABLED=false to disable telemetry.",
            "summary_text": "Telemetry is disabled with TELEMETRY_ENABLED=false.",
            "tags": ["telemetry", "configuration"],
        },
    )

    response = client.post(
        "/answer",
        json={
            "query": "How do I disable telemetry?",
            "mode": "answer",
            "filters": {"source_site_id": source["id"]},
            "top_k": 5,
        },
    )
    assert response.status_code == 200
    payload = response.json()
    assert "telemetry" in payload["answer"].lower()
    assert payload["supporting_evidence"] != []
    assert payload["supporting_evidence"][0]["canonical_url"] == "https://answer.example.com/settings"
    assert payload["query"]["mode"] == "answer"
    assert "[1]" in payload["answer"]


def test_answer_without_evidence_returns_supported_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "app.services.retrieval.QdrantIndexer.search_chunks",
        lambda self, *, query_text, filters, top_k: [],
    )
    client = TestClient(app)

    response = client.post(
        "/answer",
        json={
            "query": "What is the retention setting?",
            "mode": "answer",
            "filters": {},
            "top_k": 5,
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["answer"] == "No supported answer found in the indexed sources."
    assert payload["supporting_evidence"] == []
    assert payload["query"]["mode"] == "answer"
    assert payload["query"]["result_count"] == 0
