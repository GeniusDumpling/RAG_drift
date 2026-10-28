"""Verify all text-model callers can use the same alternative service."""

import importlib
from pathlib import Path

import httpx
import pytest
from app.agents.client import AgentClient
from app.core.config import Settings
from app.core.llm_config import LLMSettings
from app.literature.llm import LiteratureLLM
from app.services.answer_generation import LLMAnswerClient
from app.services.video_graph import LLMVideoExtractor

ROOT = Path(__file__).resolve().parents[2]


def test_environment_overrides_shared_dotenv(tmp_path, monkeypatch):
    env = tmp_path / ".env"
    env.write_text(
        'BASE_URL=https://file.example/v1\nLLM_API_KEY="file-key"\nLLM_MODEL=file-model\n'
    )
    for key in ("BASE_URL", "LLM_API_KEY", "LLM_MODEL"):
        monkeypatch.delenv(key, raising=False)
    settings = Settings(_env_file=env)
    assert (settings.base_url, settings.llm_api_key, settings.llm_model) == (
        "https://file.example/v1", "file-key", "file-model"
    )
    monkeypatch.setenv("BASE_URL", "https://env.example/v1")
    monkeypatch.setenv("LLM_API_KEY", "env-key")
    monkeypatch.setenv("LLM_MODEL", "env-model")
    shared = LLMSettings(_env_file=env)
    backend = Settings(_env_file=env)
    for config in (shared, backend):
        assert (config.base_url, config.llm_api_key, config.llm_model) == (
            "https://env.example/v1", "env-key", "env-model"
        )


def test_shared_dotenv_does_not_change_with_working_directory(tmp_path, monkeypatch):
    original = LLMSettings()
    (tmp_path / ".env").write_text("LLM_MODEL=wrong-local-model\n")
    monkeypatch.chdir(tmp_path)
    assert LLMSettings() == original


@pytest.mark.parametrize("provider", ["openai-compatible", "deepseek"])
async def test_all_callers_use_alternative_service(monkeypatch, provider):
    monkeypatch.setenv("BASE_URL", "https://alternative.example/custom/v1/")
    monkeypatch.setenv("LLM_API_KEY", "alternative-key")
    monkeypatch.setenv("LLM_MODEL", "alternative-model")
    settings = Settings(_env_file=None)
    requests = []

    def respond(request):
        import json

        body = json.loads(request.content)
        assert str(request.url) == "https://alternative.example/custom/v1/chat/completions"
        assert request.headers["Authorization"] == "Bearer alternative-key"
        assert body["model"] == "alternative-model"
        assert "thinking" not in body
        assert "reasoning_effort" not in body
        requests.append(request)
        return httpx.Response(
            200, json={"choices": [{"message": {"content": '{"ok":true}'}}]}
        )

    transport = httpx.MockTransport(respond)
    sync_client = httpx.Client
    async_client = httpx.AsyncClient
    monkeypatch.setattr(httpx, "Client", lambda **kw: sync_client(transport=transport, **kw))
    monkeypatch.setattr(
        httpx, "AsyncClient", lambda **kw: async_client(transport=transport, **kw)
    )

    def post(url, **kwargs):
        with sync_client(transport=transport) as client:
            return client.post(url, **kwargs)

    monkeypatch.setattr(httpx, "post", post)
    agent = AgentClient(provider, 5, settings)
    optimized = agent.optimize_query("q", {}, "search")
    assert optimized.trace_summary_json == {"provider": provider, "fallback": False}
    assert await LiteratureLLM(settings)._request_json_content(
        messages=[], temperature=0
    ) == '{"ok":true}'
    assert LLMVideoExtractor(settings)._request("s", {}) == {"ok": True}
    assert LLMAnswerClient(
        base_url=settings.base_url,
        api_key=settings.llm_api_key,
        model=settings.llm_model,
        timeout_seconds=5,
    ).generate("q", []) == '{"ok":true}'

    from scripts.evaluate_answer_evidence_counts import json_completion

    assert (await json_completion(settings, "s", {}))["output"] == {"ok": True}

    monkeypatch.syspath_prepend(str(ROOT / "tools/supplier-information"))
    supplier_config = importlib.import_module("llm_config")
    config = supplier_config.get_llm_config(
        {"api_base": "https://stale.example", "model": "stale-model", "timeout": 5}
    )
    assert config["timeout"] == 5

    def supplier_post(url, **kwargs):
        return post(url, **kwargs)

    for name in ("llm_supplier_analysis", "supplier_verify"):
        module = importlib.import_module(name)
        monkeypatch.setattr(module.requests, "post", supplier_post)
        assert module.call_llm(config["api_key"], config, "u") == '{"ok":true}'
    assert len(requests) == 7
