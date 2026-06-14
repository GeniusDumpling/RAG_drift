import hashlib
import uuid
from typing import Any

from app.main import app
from app.models.content import ContentItem
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


async def _create_source_and_run(client: AsyncClient) -> tuple[dict[str, Any], dict[str, Any]]:
    source_response = await client.post(
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
    )
    assert source_response.status_code == 201
    source = source_response.json()

    job_response = await client.post(
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
    )
    assert job_response.status_code == 201
    job = job_response.json()

    run_response = await client.post(
        f"/jobs/{job['id']}/trigger", json={"seed_url": "https://forum.example.com/t/1"}
    )
    assert run_response.status_code == 201
    return source, run_response.json()


def _valid_ingest_payload(source_id: str, run_id: str) -> dict[str, Any]:
    return {
        "source_site_id": source_id,
        "crawl_run_id": run_id,
        "requested_url": "https://forum.example.com/t/1",
        "final_url": "https://forum.example.com/t/1",
        "raw_html": "<html><h1>Thread title</h1><p>Body text about telemetry.</p></html>",
        "item_type": "thread",
        "title": "Thread title",
        "cleaned_text": "Body text about telemetry.",
        "summary_text": "Telemetry discussion.",
        "tags": ["telemetry"],
    }


async def test_content_list_and_detail_hydrate_source_raw_run_chunks_and_hashes(
    db_session: AsyncSession,
) -> None:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        source, run = await _create_source_and_run(client)
        payload = _valid_ingest_payload(source["id"], run["id"])
        raw_html = payload["raw_html"]
        cleaned_text = payload["cleaned_text"]
        assert isinstance(raw_html, str)
        assert isinstance(cleaned_text, str)
        expected_body_hash = _sha256(raw_html)
        expected_content_hash = _sha256(cleaned_text)
        expected_dedup_key = _sha256(
            f"{source['id']}:https://forum.example.com/t/1:thread:{expected_content_hash}"
        )

        ingest_response = await client.post("/contents/test-ingest", json=payload)
        assert ingest_response.status_code == 201
        content_id = ingest_response.json()["content_item_id"]

        list_response = await client.get("/contents?item_type=thread")
        assert list_response.status_code == 200
        assert list_response.json()["items"][0]["title"] == "Thread title"

        detail_response = await client.get(f"/contents/{content_id}")
        assert detail_response.status_code == 200
        detail = detail_response.json()
        assert detail["raw_page"]["requested_url"] == "https://forum.example.com/t/1"
        assert detail["raw_page"]["body_hash"] == expected_body_hash
        assert "content_hash" not in detail["raw_page"]
        assert detail["source"]["name"] == "Forum Demo"
        assert detail["chunks"] != []
        chunk = detail["chunks"][0]
        assert "vector_backend" in chunk
        assert "vector_point_id" in chunk
        assert "embedded_at" in chunk
        assert chunk["vector_backend"] is None
        assert chunk["vector_point_id"] is None
        assert chunk["embedded_at"] is None

    content_item = await db_session.get(ContentItem, uuid.UUID(content_id))
    assert content_item is not None
    assert content_item.content_hash == expected_content_hash
    assert content_item.dedup_key == expected_dedup_key


async def test_test_ingest_requires_raw_body() -> None:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        source, run = await _create_source_and_run(client)
        payload = _valid_ingest_payload(source["id"], run["id"])
        del payload["raw_html"]

        response = await client.post("/contents/test-ingest", json=payload)

    assert response.status_code == 422


async def test_test_ingest_returns_conflict_for_duplicate_content_item() -> None:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        source, run = await _create_source_and_run(client)
        payload = _valid_ingest_payload(source["id"], run["id"])

        first_response = await client.post("/contents/test-ingest", json=payload)
        duplicate_response = await client.post("/contents/test-ingest", json=payload)

    assert first_response.status_code == 201
    assert duplicate_response.status_code == 409
    assert duplicate_response.json()["detail"] == "Duplicate content item"
