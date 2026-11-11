"""An explicit CLI graph target must never silently write to the app's default graph."""

import importlib.util
from pathlib import Path
from unittest.mock import Mock, patch

import pytest
from app.core.config import Settings

pytestmark = pytest.mark.no_db
ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def cli():
    spec = importlib.util.spec_from_file_location(
        "video_graph_target_cli", ROOT / "tools/video-knowledge-graph/build_graph.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def profile(tmp_path, text=None):
    path = tmp_path / "target.env"
    path.write_text(text or (
        "NEO4J_URI=bolt://127.0.0.1:7688\nNEO4J_USER=neo4j\n"
        "NEO4J_PASSWORD=target-test-password\nNEO4J_DATABASE=neo4j\n"
    ))
    return path


def test_profile_overrides_only_connection_without_mutating_shared_settings(cli, tmp_path):
    base = Settings(_env_file=None, NEO4J_URI="bolt://old-graph:7687", LLM_API_KEY="test")
    before = base.model_dump()
    selected = cli.target_settings(base, profile(tmp_path))
    assert base.model_dump() == before
    assert selected.neo4j_uri == "bolt://127.0.0.1:7688"
    assert selected.neo4j_password == "target-test-password"
    assert selected.model_dump(exclude={
        "neo4j_uri", "neo4j_user", "neo4j_password", "neo4j_database"
    }) == base.model_dump(exclude={
        "neo4j_uri", "neo4j_user", "neo4j_password", "neo4j_database"
    })
    assert cli.target_settings(base, None) is base


@pytest.mark.parametrize("text", [
    "NEO4J_URI=bolt://new:7687\n",
    "NEO4J_URI=bolt://new:7687\nNEO4J_USER=neo4j\nNEO4J_PASSWORD=\nNEO4J_DATABASE=neo4j\n",
    "LLM_API_KEY=not-a-graph-option\n",
])
def test_invalid_profile_never_falls_back_to_default(cli, tmp_path, text):
    with pytest.raises(ValueError, match="Neo4j目标配置"):
        cli.target_settings(Settings(_env_file=None), profile(tmp_path, text))


def test_missing_profile_never_falls_back_to_default(cli, tmp_path):
    with pytest.raises(ValueError, match="拒绝回退"):
        cli.target_settings(Settings(_env_file=None), tmp_path / "missing.env")


def test_pipeline_uses_selected_connection_before_opening_store(cli, tmp_path):
    base = Settings(_env_file=None, NEO4J_URI="bolt://old-graph:7687")
    store = Mock()
    with (
        patch.object(cli, "get_settings", return_value=base),
        patch.object(cli, "create_engine") as engine,
        patch("app.services.video_graph.storage.GraphStore", return_value=store) as constructor,
        patch.object(cli, "run_pipeline", return_value=iter([])),
    ):
        result = cli.main([
            "pipeline", "--neo4j-env-file", str(profile(tmp_path)),
            "--manifest", str(tmp_path / "plan.json"),
            "--report", str(tmp_path / "report.json"), "--dry-run",
        ])
    assert result == 0
    assert constructor.call_args.args[0].neo4j_uri == "bolt://127.0.0.1:7688"
    assert base.neo4j_uri == "bolt://old-graph:7687"
    engine.assert_called_once_with(base.sync_database_url, pool_pre_ping=True)
    store.initialize.assert_not_called()
    store.ensure_ready.assert_called_once()
