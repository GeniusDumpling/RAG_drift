import importlib.util
import stat
import subprocess
from pathlib import Path
from types import ModuleType


def _is_executable(path: Path) -> bool:
    return bool(path.stat().st_mode & stat.S_IXUSR)


def _load_seed_demo_module() -> ModuleType:
    root = Path(__file__).resolve().parents[2]
    seed = root / "scripts" / "seed_demo.py"
    spec = importlib.util.spec_from_file_location("seed_demo_contract", seed)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_demo_scripts_exist_and_are_executable() -> None:
    root = Path(__file__).resolve().parents[2]
    seed = root / "scripts" / "seed_demo.py"
    worker_once = root / "scripts" / "run_worker_once.py"
    smoke = root / "scripts" / "smoke_demo.sh"

    for script in (seed, worker_once, smoke):
        assert script.exists()
        assert _is_executable(script), f"{script} must be executable"


def test_seed_demo_has_local_db_guard_and_reconciles_demo_config() -> None:
    root = Path(__file__).resolve().parents[2]
    seed = root / "scripts" / "seed_demo.py"

    seed_text = seed.read_text()

    assert "ALLOW_NONLOCAL_DEMO_SEED" in seed_text
    assert "Refusing to seed demo against a non-local database" in seed_text
    assert "DATABASE_URL" in seed_text
    assert "SYNC_DATABASE_URL" in seed_text
    assert "safe_url" in seed_text
    assert "parse_qsl" in seed_text
    assert "query_host_values" in seed_text
    assert "hostless non-sqlite database URL" in seed_text
    assert "DEMO_SOURCE_VALUES" in seed_text
    assert "DEMO_JOB_VALUES" in seed_text
    assert "config_json" in seed_text
    assert "kind" in seed_text
    assert "DemoSeedSafetyError" in seed_text
    assert "not marked as demo-owned" in seed_text
    assert "SourceSite named" in seed_text
    assert "CrawlJob named" in seed_text
    assert "unmarked_sources" in seed_text
    assert "unmarked_jobs" in seed_text
    assert "reconcile_source" in seed_text
    assert "reconcile_job" in seed_text
    assert "seed_config_json" in seed_text
    assert "parser_profile" in seed_text
    assert "agent_policy_json" in seed_text


def test_seed_demo_local_db_guard_rejects_hostless_postgres_and_query_host() -> None:
    seed_demo = _load_seed_demo_module()

    assert seed_demo._is_local_database_url(  # noqa: SLF001
        "postgresql+psycopg://intelligence:intelligence@localhost:54329/intelligence_rag"
    )
    assert seed_demo._is_local_database_url(  # noqa: SLF001
        "postgresql+psycopg://intelligence:intelligence@127.0.0.1:54329/intelligence_rag"
        "?host=localhost"
    )
    assert seed_demo._is_local_database_url(  # noqa: SLF001
        "sqlite+aiosqlite:///tmp/demo.db"
    )
    assert not seed_demo._is_local_database_url(  # noqa: SLF001
        "postgresql+psycopg:///intelligence_rag"
    )
    assert not seed_demo._is_local_database_url(  # noqa: SLF001
        "postgresql+psycopg://intelligence:intelligence@localhost:54329/intelligence_rag"
        "?host=prod-db.example.com"
    )
    assert not seed_demo._is_local_database_url("mysql:///intelligence_rag")  # noqa: SLF001


def test_readme_demo_paths_are_split_and_target_seeded_run() -> None:
    root = Path(__file__).resolve().parents[2]
    readme = root / "README.md"

    readme_text = readme.read_text()

    assert "One-command smoke path" in readme_text
    assert "Manual demo path" in readme_text
    assert "SKIP_DOCKER=1" in readme_text
    assert "may create `.env` from `.env.example`" in readme_text
    assert "SEED_JSON" in readme_text
    assert "RUN_ID" in readme_text
    assert "run_id" in readme_text
    assert 'scripts/run_worker_once.py --json --require-success --run-id "$RUN_ID"' in readme_text


def test_demo_smoke_contract_targets_seeded_run_and_asserts_semantics() -> None:
    root = Path(__file__).resolve().parents[2]
    worker_once = root / "scripts" / "run_worker_once.py"
    smoke = root / "scripts" / "smoke_demo.sh"

    worker_once_text = worker_once.read_text()
    smoke_text = smoke.read_text()

    assert "--run-id" in worker_once_text
    assert "--json" in worker_once_text
    assert "--require-success" in worker_once_text
    assert "json.dumps" in worker_once_text
    assert "scripts/run_worker_once.py --json --require-success --run-id" in smoke_text
    assert "assert_worker_result" in smoke_text
    assert 'payload != {"claimed": 1, "succeeded": 1, "partial": 0, "failed": 0}' in smoke_text
    assert "GET /runs/$RUN_ID" in smoke_text
    assert "assert_run_status_success" in smoke_text
    assert "ALLOW_NONLOCAL_SMOKE_API" in smoke_text
    assert "Refusing to run smoke demo against a non-local API_BASE" in smoke_text
    assert "require_local_api_base" in smoke_text
    assert "parse_qsl" in smoke_text
    assert "query_host_values" in smoke_text
    assert "hostless non-sqlite database URL" in smoke_text
    assert "ALLOW_NONLOCAL_DEMO_SEED=1" in smoke_text
    assert "Created .env from .env.example" in smoke_text
    assert "POST /search" in smoke_text
    assert "POST /answer" in smoke_text
    assert "json.load" in smoke_text
    assert ".venv/bin/python" in smoke_text
    assert ".venv/bin/alembic" in smoke_text
    assert "evidence" in smoke_text
    assert "supporting_evidence" in smoke_text
    assert "[1]" in smoke_text
    assert "telemetry" in smoke_text
    assert "settings" in smoke_text
    assert "/answer supporting_evidence did not reference demo telemetry/settings" in smoke_text
    assert "startswith(\"127.\")" not in smoke_text


def test_smoke_demo_shell_syntax_is_valid() -> None:
    root = Path(__file__).resolve().parents[2]
    smoke = root / "scripts" / "smoke_demo.sh"

    completed = subprocess.run(
        ["bash", "-n", str(smoke)],
        text=True,
        capture_output=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
