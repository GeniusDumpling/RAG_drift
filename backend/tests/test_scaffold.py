from pathlib import Path


def _make_target_body(makefile: str, target: str) -> str:
    lines = makefile.splitlines()
    start = lines.index(f"{target}:") + 1
    body: list[str] = []
    for line in lines[start:]:
        if line and not line.startswith("\t"):
            break
        if line.startswith("\t"):
            body.append(line)
    return "\n".join(body)


def test_required_root_files_exist() -> None:
    root = Path(__file__).resolve().parents[2]
    expected = [
        "pyproject.toml",
        "docker-compose.yml",
        ".env.example",
        ".gitignore",
        "Makefile",
        "README.md",
    ]
    missing = [name for name in expected if not (root / name).exists()]
    assert missing == []


def test_makefile_targets_prefer_installed_virtualenv_tools() -> None:
    root = Path(__file__).resolve().parents[2]
    makefile = (root / "Makefile").read_text()

    expected_tools = {
        "PY": ".venv/bin/python",
        "PYTEST": ".venv/bin/pytest",
        "ALEMBIC": ".venv/bin/alembic",
        "UVICORN": ".venv/bin/uvicorn",
        "RUFF": ".venv/bin/ruff",
        "MYPY": ".venv/bin/mypy",
    }
    for variable, path in expected_tools.items():
        assert f"{variable} := {path}" in makefile

    expected_invocations = {
        "test": ["$(PYTEST) -q"],
        "lint": ["$(RUFF) check backend worker scripts", "$(MYPY) backend worker"],
        "migrate": ["$(ALEMBIC) upgrade head"],
        "api": ["$(UVICORN) app.main:app"],
        "worker-once": ["$(PY) scripts/run_worker_once.py"],
        "seed": ["$(PY) scripts/seed_demo.py"],
    }
    for target, invocations in expected_invocations.items():
        body = _make_target_body(makefile, target)
        for invocation in invocations:
            assert invocation in body
