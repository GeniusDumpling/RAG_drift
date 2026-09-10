"""Exercise actual Compose interpolation without reading the developer's .env."""

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.parametrize("proxy", ["", "http://host.docker.internal:17897"])
def test_compose_environment_and_isolation(tmp_path: Path, proxy: str) -> None:
    if shutil.which("docker") is None:
        pytest.skip("Docker Compose CLI is required")
    shutil.copy(ROOT / "docker-compose.deploy.yml", tmp_path)
    (tmp_path / ".env").write_text(
        f"POSTGRES_PASSWORD=testonly123\nHTTP_PROXY={proxy}\nHTTPS_PROXY={proxy}\n"
        "COLLECTOR_DATA_DIR=./artifacts\n",
        encoding="utf-8",
    )
    result = subprocess.run(
        [
            "docker",
            "compose",
            "-f",
            "docker-compose.deploy.yml",
            "--profile",
            "jobs",
            "config",
            "--format",
            "json",
        ],
        cwd=tmp_path,
        env={"PATH": os.environ["PATH"], "HOME": str(tmp_path)},
        capture_output=True,
        text=True,
        check=True,
    )
    services = json.loads(result.stdout)["services"]
    jobs = services["jobs"]
    for name in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy"):
        assert jobs["environment"][name] == proxy
    for name in (
        "DATABASE_URL",
        "SYNC_DATABASE_URL",
        "QDRANT_COLLECTION",
        "EMBEDDING_MODEL",
        "EMBEDDING_DIMENSION",
    ):
        assert jobs["environment"][name] == services["app"]["environment"][name]
    assert not services["postgres"].get("ports")
    assert not services["qdrant"].get("ports")
    assert services["app"]["ports"][0]["host_ip"] == "127.0.0.1"
    assert any(v["target"] == "/data/video" for v in jobs["volumes"])
