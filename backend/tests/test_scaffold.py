from pathlib import Path


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
