from pathlib import Path


def test_demo_scripts_exist_and_are_executable() -> None:
    root = Path(__file__).resolve().parents[2]
    seed = root / "scripts" / "seed_demo.py"
    smoke = root / "scripts" / "smoke_demo.sh"
    assert seed.exists()
    assert smoke.exists()
    assert "POST /search" in smoke.read_text()
    assert "POST /answer" in smoke.read_text()
