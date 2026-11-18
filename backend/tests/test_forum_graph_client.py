"""Real client protocol with local HTTP transports; no external model requests."""

import json

import httpx
import pytest
from app.core.config import Settings
from app.services.forum_graph.client import ForumClient

pytestmark = pytest.mark.no_db


@pytest.fixture
def settings():
    return Settings(
        _env_file=None,
        BASE_URL="https://model.invalid/v1",
        LLM_API_KEY="fixture",
        LLM_MODEL="fixture",
        FORUM_GRAPH_TIMEOUT_SECONDS=1,
    )


def local_transport(monkeypatch, handler):
    real_client = httpx.AsyncClient
    monkeypatch.setattr(
        "app.services.forum_graph.client.httpx.AsyncClient",
        lambda **kwargs: real_client(transport=httpx.MockTransport(handler), **kwargs),
    )


def response(value=None, usage=None, finish="stop"):
    return httpx.Response(
        200,
        json=dict(
            choices=[
                dict(finish_reason=finish, message=dict(content=json.dumps(value or {"value": 1})))
            ],
            usage=usage,
        ),
    )


async def test_exact_request_cache_usage_and_offline_replay(settings, tmp_path, monkeypatch):
    calls = []

    def handler(request):
        calls.append(request)
        return response(usage=dict(prompt_tokens=10, completion_tokens=4, total_tokens=14))

    local_transport(monkeypatch, handler)
    output = {}
    client = ForumClient(settings, tmp_path, output=output)
    assert await client.request("assessment", "system", {"post": "PX4 fixture"}) == {"value": 1}
    replay = ForumClient(settings, tmp_path, cache_only=True)
    assert await replay.request("assessment", "system", {"post": "PX4 fixture"}) == {"value": 1}
    assert len(calls) == 1
    assert client.usage.snapshot()["assessment"]["total_tokens"] == 14
    assert replay.usage.snapshot()["assessment"]["checkpoint_replays"] == 1
    with pytest.raises(ValueError):
        await replay.request("assessment", "system", {"post": "different input"})
    assert len(calls) == 1


async def test_unknown_usage_is_not_reported_as_zero(settings, tmp_path, monkeypatch):
    local_transport(monkeypatch, lambda request: response())
    client = ForumClient(settings, tmp_path)
    await client.request("entities", "system", {})
    usage = client.usage.snapshot()["entities"]
    assert usage["unknown_usage_responses"] == 1
    assert "total_tokens" not in usage


async def test_auth_failure_does_not_retry_or_cache(settings, tmp_path, monkeypatch):
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(401, json={"error": "not authorized"})

    local_transport(monkeypatch, handler)
    client = ForumClient(settings, tmp_path)
    with pytest.raises(RuntimeError):
        await client.request("entities", "system", {})
    assert len(calls) == 1 and not list(tmp_path.glob("*.json"))
    assert client.usage.snapshot()["entities"]["unknown_usage_responses"] == 1


async def test_rate_limit_is_bounded_and_retried_once(settings, tmp_path, monkeypatch):
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(429, headers={"Retry-After": "0"}) if len(calls) == 1 else response()

    async def no_sleep(delay):
        pass

    local_transport(monkeypatch, handler)
    monkeypatch.setattr("app.services.forum_graph.client.asyncio.sleep", no_sleep)
    client = ForumClient(settings, tmp_path)
    assert await client.request("entities", "system", {})
    assert len(calls) == 2
    assert client.usage.snapshot()["entities"]["http_attempts"] == 2


async def test_truncation_is_never_cached_as_success(settings, tmp_path, monkeypatch):
    calls = []

    def handler(request):
        calls.append(request)
        return response(finish="length")

    async def no_sleep(delay):
        pass

    local_transport(monkeypatch, handler)
    monkeypatch.setattr("app.services.forum_graph.client.asyncio.sleep", no_sleep)
    client = ForumClient(settings, tmp_path)
    with pytest.raises(RuntimeError):
        await client.request("claims", "system", {})
    assert len(calls) == 2 and not list(tmp_path.glob("*.json"))


async def test_corrupt_cache_is_rejected_offline_and_repaired_online(
    settings, tmp_path, monkeypatch
):
    calls = []

    def handler(request):
        calls.append(request)
        return response()

    local_transport(monkeypatch, handler)
    client = ForumClient(settings, tmp_path)
    await client.request("entities", "system", {})
    path = next(tmp_path.glob("*.json"))
    path.write_text('{"corrupt":true}')
    offline = ForumClient(settings, tmp_path, cache_only=True)
    with pytest.raises(ValueError):
        await offline.request("entities", "system", {})
    assert len(calls) == 1
    online = ForumClient(settings, tmp_path)
    assert await online.request("entities", "system", {}) == {"value": 1}
    assert len(calls) == 2


async def test_changed_provider_account_cannot_reuse_raw_responses(settings, tmp_path, monkeypatch):
    local_transport(monkeypatch, lambda request: response())
    client = ForumClient(settings, tmp_path)
    await client.request("entities", "system", {})
    changed = ForumClient(
        settings.model_copy(update={"llm_api_key": "other-account"}), tmp_path, cache_only=True
    )
    with pytest.raises(ValueError):
        await changed.request("entities", "system", {})
