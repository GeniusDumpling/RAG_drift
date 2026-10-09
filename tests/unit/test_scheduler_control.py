import importlib.util
from pathlib import Path
from unittest.mock import patch

import pytest

ROOT = Path(__file__).resolve().parents[2]


def load_bridge():
    spec = importlib.util.spec_from_file_location("bridge", ROOT / "scripts/scheduler_bridge.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_only_allowlisted_units_can_start():
    bridge = load_bridge()
    with patch.object(bridge, "systemctl") as command:
        with pytest.raises(ValueError):
            bridge.trigger("ssh.service")
        command.assert_not_called()


def test_running_unit_is_not_started_again():
    bridge = load_bridge()
    with (
        patch.object(
            bridge, "properties", return_value={"LoadState": "loaded", "ActiveState": "activating"}
        ),
        patch.object(bridge, "systemctl") as command,
    ):
        assert bridge.trigger("video-collect")["status"] == "already_running"
        command.assert_not_called()


def test_trigger_starts_service_not_timer_and_reads_back():
    bridge = load_bridge()
    with (
        patch.object(
            bridge,
            "properties",
            side_effect=[
                {"LoadState": "loaded", "ActiveState": "inactive"},
                {"LoadState": "loaded", "ActiveState": "activating", "InvocationID": "new-run"},
            ],
        ),
        patch.object(bridge, "systemctl") as command,
    ):
        result = bridge.trigger("video-collect")
        command.assert_called_once_with("start", "--no-block", "rag-video-collect.service")
        assert result["invocation_id"] == "new-run"


def test_http_requires_operator_token_and_preserves_allowlist(tmp_path, monkeypatch):
    from app.api import scheduled_tasks
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    token = tmp_path / "token"
    token.write_text("test-only")
    monkeypatch.setenv("SCHEDULER_TOKEN_FILE", str(token))
    app = FastAPI()
    app.include_router(scheduled_tasks.router)
    with TestClient(app) as client, patch.object(scheduled_tasks, "bridge_request") as bridge:
        assert client.post("/scheduled-tasks/video-collect/trigger").status_code == 403
        assert client.post("/scheduled-tasks/ssh/trigger").status_code == 404
        bridge.assert_not_called()
        bridge.return_value = {"status": "accepted"}
        assert (
            client.post(
                "/scheduled-tasks/video-collect/trigger",
                headers={"Authorization": "Bearer test-only"},
            ).status_code
            == 202
        )
        bridge.assert_called_once_with("POST", "/tasks/video-collect")
