from typing import Any, cast

from app.main import app
from fastapi.testclient import TestClient
from httpx import Response

JsonObject = dict[str, Any]

MISSING_SOURCE_ID = "00000000-0000-0000-0000-000000000001"
MISSING_JOB_ID = "00000000-0000-0000-0000-000000000002"
MISSING_RUN_ID = "00000000-0000-0000-0000-000000000003"


def _json_object(response: Response) -> JsonObject:
    payload = response.json()
    assert isinstance(payload, dict)
    return cast(JsonObject, payload)


def _assert_error(response: Response, status_code: int, detail: str) -> None:
    assert response.status_code == status_code
    assert _json_object(response) == {"detail": detail}


def _source_payload(name: str = "Example Docs") -> JsonObject:
    return {
        "name": name,
        "site_type": "docs",
        "base_url": "https://example.com/docs",
        "allowed_domains": ["example.com"],
        "fetch_mode": "manual",
        "default_language": "en",
        "active": True,
        "config_json": {"kind": "demo"},
    }


def _create_source(client: TestClient, name: str = "Example Docs") -> JsonObject:
    response = client.post("/sources", json=_source_payload(name))
    assert response.status_code == 201
    source = _json_object(response)
    assert source["name"] == name
    return source


def _job_payload(source_site_id: str, name: str = "Manual docs crawl") -> JsonObject:
    return {
        "source_site_id": source_site_id,
        "name": name,
        "trigger_mode": "manual",
        "cron_expr": None,
        "seed_config_json": {"urls": ["https://example.com/docs/start"]},
        "parser_profile": "official_site",
        "max_pages": 5,
        "enabled": True,
        "agent_policy_json": {"extraction_mode": "hybrid"},
    }


def _create_job(
    client: TestClient, source_site_id: str, name: str = "Manual docs crawl"
) -> JsonObject:
    response = client.post("/jobs", json=_job_payload(source_site_id, name))
    assert response.status_code == 201
    job = _json_object(response)
    assert job["source_site_id"] == source_site_id
    assert job["name"] == name
    return job


def test_task4_control_api_endpoint_contract() -> None:
    client = TestClient(app)

    source = _create_source(client)
    source_id = str(source["id"])

    sources_response = client.get("/sources")
    assert sources_response.status_code == 200
    sources_page = _json_object(sources_response)
    assert sources_page["total"] == 1
    assert sources_page["limit"] == 50
    assert sources_page["offset"] == 0
    assert [item["id"] for item in sources_page["items"]] == [source_id]

    source_response = client.get(f"/sources/{source_id}")
    assert source_response.status_code == 200
    assert _json_object(source_response)["id"] == source_id

    patch_source_response = client.patch(
        f"/sources/{source_id}",
        json={
            "active": False,
            "default_language": "fr",
            "config_json": {"kind": "updated"},
        },
    )
    assert patch_source_response.status_code == 200
    updated_source = _json_object(patch_source_response)
    assert updated_source["id"] == source_id
    assert updated_source["active"] is False
    assert updated_source["default_language"] == "fr"
    assert updated_source["config_json"] == {"kind": "updated"}

    job = _create_job(client, source_id)
    job_id = str(job["id"])

    jobs_response = client.get("/jobs")
    assert jobs_response.status_code == 200
    jobs_page = _json_object(jobs_response)
    assert jobs_page["total"] == 1
    assert jobs_page["limit"] == 50
    assert jobs_page["offset"] == 0
    assert [item["id"] for item in jobs_page["items"]] == [job_id]

    patch_job_response = client.patch(
        f"/jobs/{job_id}",
        json={"name": "Nightly docs crawl", "max_pages": 9, "enabled": False},
    )
    assert patch_job_response.status_code == 200
    updated_job = _json_object(patch_job_response)
    assert updated_job["id"] == job_id
    assert updated_job["source_site_id"] == source_id
    assert updated_job["name"] == "Nightly docs crawl"
    assert updated_job["max_pages"] == 9
    assert updated_job["enabled"] is False

    seed_url = "https://example.com/docs/start"
    run_response = client.post(f"/jobs/{job_id}/trigger", json={"seed_url": seed_url})
    assert run_response.status_code == 201
    run = _json_object(run_response)
    run_id = str(run["id"])
    assert run["source_site_id"] == source_id
    assert run["crawl_job_id"] == job_id
    assert run["status"] == "queued"
    assert run["trigger_type"] == "manual"
    assert run["seed_url"] == seed_url

    runs_response = client.get("/runs")
    assert runs_response.status_code == 200
    runs_page = _json_object(runs_response)
    assert runs_page["total"] == 1
    assert runs_page["limit"] == 50
    assert runs_page["offset"] == 0
    assert [item["id"] for item in runs_page["items"]] == [run_id]

    get_run_response = client.get(f"/runs/{run_id}")
    assert get_run_response.status_code == 200
    fetched_run = _json_object(get_run_response)
    assert fetched_run["id"] == run_id
    assert fetched_run["source_site_id"] == source_id
    assert fetched_run["crawl_job_id"] == job_id

    events_response = client.get(f"/runs/{run_id}/events")
    assert events_response.status_code == 200
    events_page = _json_object(events_response)
    assert events_page["total"] == 1
    event = events_page["items"][0]
    assert event["crawl_run_id"] == run_id
    assert event["event_type"] == "run_queued"
    assert event["related_url"] == seed_url


def test_task4_control_api_missing_resources_return_404() -> None:
    client = TestClient(app)

    _assert_error(client.get(f"/sources/{MISSING_SOURCE_ID}"), 404, "Source not found")
    _assert_error(
        client.patch(f"/sources/{MISSING_SOURCE_ID}", json={"active": False}),
        404,
        "Source not found",
    )
    _assert_error(
        client.post("/jobs", json=_job_payload(MISSING_SOURCE_ID)),
        404,
        "Source not found",
    )
    _assert_error(
        client.patch(f"/jobs/{MISSING_JOB_ID}", json={"name": "Missing job"}),
        404,
        "Job not found",
    )
    _assert_error(
        client.post(f"/jobs/{MISSING_JOB_ID}/trigger", json={"seed_url": None}),
        404,
        "Job not found",
    )
    _assert_error(client.get(f"/runs/{MISSING_RUN_ID}"), 404, "Run not found")
    _assert_error(client.get(f"/runs/{MISSING_RUN_ID}/events"), 404, "Run not found")

    source = _create_source(client, name="Patch Job Source")
    job = _create_job(client, str(source["id"]), name="Patch job")
    _assert_error(
        client.patch(f"/jobs/{job['id']}", json={"source_site_id": MISSING_SOURCE_ID}),
        404,
        "Source not found",
    )
