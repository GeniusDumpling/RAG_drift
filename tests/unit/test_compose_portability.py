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
        "COLLECTOR_DATA_DIR=./artifacts\n"
        "BASE_URL=https://alternative.example/v1\n"
        "LLM_API_KEY=test-only-key\nLLM_MODEL=alternative-model\n"
        "LLM_PROVIDER=openai-compatible\n",
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
    assert "VIDEO_GRAPH_BACKEND" not in jobs["environment"]
    for name in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy"):
        assert jobs["environment"][name] == proxy
    for name in (
        "DATABASE_URL",
        "SYNC_DATABASE_URL",
        "QDRANT_COLLECTION",
        "EMBEDDING_MODEL",
        "EMBEDDING_DIMENSION",
        "BASE_URL",
        "LLM_API_KEY",
        "LLM_MODEL",
    ):
        assert jobs["environment"][name] == services["app"]["environment"][name]
    assert services["app"]["environment"]["BASE_URL"] == "https://alternative.example/v1"
    assert services["app"]["environment"]["LLM_API_KEY"] == "test-only-key"
    assert services["app"]["environment"]["LLM_MODEL"] == "alternative-model"
    assert services["app"]["environment"]["LLM_PROVIDER"] == "openai-compatible"
    # Host collectors use loopback ports; graph storage remains independent of PostgreSQL.
    assert all(p["host_ip"] == "127.0.0.1" for p in services["postgres"]["ports"])
    assert all(p["host_ip"] == "127.0.0.1" for p in services["qdrant"]["ports"])
    assert services["app"]["ports"][0]["host_ip"] == "127.0.0.1"
    assert any(v["target"] == "/data/video" for v in jobs["volumes"])
    for brand, directory in [("dji", "DJI无人机文档"), ("parrot", "Parrot无人机文档")]:
        target = f"/data/{brand}-documents"
        mount = next(v for v in services["app"]["volumes"] if v["target"] == target)
        assert mount["read_only"] is True
        assert mount["source"].endswith(directory)
    assert services["app"]["environment"]["PARROT_DOCUMENTS_DIR"] == "/data/parrot-documents"


def test_neo4j_overlay_ports_credentials_and_persistence(tmp_path: Path) -> None:
    if shutil.which("docker") is None:
        pytest.skip("Docker Compose CLI is required")
    for name in ("docker-compose.deploy.yml", "docker-compose.neo4j.yml"):
        shutil.copy(ROOT / name, tmp_path)
    (tmp_path / ".env").write_text(
        "POSTGRES_PASSWORD=testonly123\nNEO4J_PASSWORD=graph-test-only\n"
    )
    result = subprocess.run(
        [
            "docker",
            "compose",
            "-f",
            "docker-compose.deploy.yml",
            "-f",
            "docker-compose.neo4j.yml",
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
    graph = services["neo4j"]
    assert all(port["host_ip"] == "127.0.0.1" for port in graph["ports"])
    assert {volume["target"] for volume in graph["volumes"]} == {"/data", "/logs"}
    assert graph["environment"]["NEO4J_AUTH"] == "neo4j/graph-test-only"
    assert services["app"]["environment"]["NEO4J_URI"] == "bolt://neo4j:7687"
    assert "VIDEO_GRAPH_BACKEND" not in services["app"]["environment"]


def test_document_graph_has_independent_credentials_ports_and_volumes(tmp_path: Path) -> None:
    if shutil.which("docker") is None:
        pytest.skip("Docker Compose CLI is required")
    shutil.copy(ROOT / "docker-compose.document-graph.yml", tmp_path)
    env_file = tmp_path / ".env.document-graph"
    env_file.write_text(
        "DOCUMENT_GRAPH_NEO4J_PASSWORD=document-test-only\nNEO4J_PASSWORD=video-test-only\n"
    )
    result = subprocess.run(
        [
            "docker",
            "compose",
            "--env-file",
            str(env_file),
            "-f",
            "docker-compose.document-graph.yml",
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
    config = json.loads(result.stdout)
    assert config["name"] == "rag-document-graph"
    assert set(config["services"]) == {"neo4j-document"}
    graph = config["services"]["neo4j-document"]
    assert graph["environment"]["NEO4J_AUTH"] == "neo4j/document-test-only"
    assert {str(p["published"]) for p in graph["ports"]} == {"7476", "7689"}
    assert all(p["host_ip"] == "127.0.0.1" for p in graph["ports"])
    assert {v["source"] for v in graph["volumes"]} == {
        "neo4j-document-data",
        "neo4j-document-logs",
    }
    assert not any(v.get("external") for v in config["volumes"].values())
