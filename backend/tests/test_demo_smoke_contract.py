import stat
import subprocess
from pathlib import Path


def _is_executable(path: Path) -> bool:
    return bool(path.stat().st_mode & stat.S_IXUSR)


def test_demo_scripts_exist_and_are_executable() -> None:
    root = Path(__file__).resolve().parents[2]
    seed = root / "scripts" / "seed_demo.py"
    worker_once = root / "scripts" / "run_worker_once.py"
    smoke = root / "scripts" / "smoke_demo.sh"

    for script in (seed, worker_once, smoke):
        assert script.exists()
        assert _is_executable(script), f"{script} must be executable"


def test_demo_smoke_contract_targets_seeded_run_and_asserts_semantics() -> None:
    root = Path(__file__).resolve().parents[2]
    worker_once = root / "scripts" / "run_worker_once.py"
    smoke = root / "scripts" / "smoke_demo.sh"

    worker_once_text = worker_once.read_text()
    smoke_text = smoke.read_text()

    assert "--run-id" in worker_once_text
    assert "scripts/run_worker_once.py --run-id" in smoke_text
    assert "POST /search" in smoke_text
    assert "POST /answer" in smoke_text
    assert "json.load" in smoke_text
    assert "evidence" in smoke_text
    assert "supporting_evidence" in smoke_text
    assert "[1]" in smoke_text
    assert "telemetry" in smoke_text
    assert "settings" in smoke_text


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
