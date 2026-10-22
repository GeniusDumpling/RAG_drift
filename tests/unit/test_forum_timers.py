"""Real wrapper execution with fake Python; no network, ingestion or systemd writes."""

import fcntl
import importlib.util
import json
import os
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / "tools/forum-crawler/scripts"


def test_render_two_separate_timers_and_bounded_services(tmp_path):
    spec = importlib.util.spec_from_file_location("forum_timers", SCRIPTS / "install_timers.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    units = module.render_units(ROOT)
    assert len(units) == 4
    assert "00/2:30:00 Asia/Shanghai" in units["rag-discourse-collect.timer"]
    assert "*:05:00 Asia/Shanghai" in units["rag-discourse-backfill.timer"]
    assert "TimeoutStartSec=20min" in units["rag-discourse-backfill.service"]
    assert "run_scheduled_backfill.sh" in units["rag-discourse-backfill.service"]
    assert "run_scheduled_discourse.sh" in units["rag-discourse-collect.service"]
    assert f"WorkingDirectory={ROOT}\n" in units["rag-discourse-collect.service"]
    assert f'EnvironmentFile=-{ROOT / ".env"}\n' in units["rag-discourse-collect.service"]
    assert all("Persistent=true" in value for key, value in units.items() if key.endswith(".timer"))
    module.main(["--unit-dir", str(tmp_path)])
    assert {p.name for p in tmp_path.iterdir()} == set(units)


def fake_python(tmp_path):
    executable = tmp_path / "python"
    log = tmp_path / "calls.jsonl"
    executable.write_text(
        "#!/usr/bin/python3\nimport json,os,sys\n"
        'with open(os.environ["PYTHON_LOG"],"a") as f: f.write(json.dumps(sys.argv[1:])+"\\n")\n'
    )
    executable.chmod(0o755)
    environment = {
        **os.environ,
        "PY": str(executable),
        "PYTHON_LOG": str(log),
        "FORUM_LOCK_FILE": str(tmp_path / "pipeline.lock"),
        "FORUM_EMBED": "0",
    }
    return environment, log


def test_backfill_wrapper_selects_history_and_then_processes_archives(tmp_path):
    environment, log = fake_python(tmp_path)
    result = subprocess.run(
        ["bash", str(SCRIPTS / "run_scheduled_backfill.sh")],
        env=environment,
        capture_output=True,
        text=True,
        timeout=5,
    )
    assert result.returncode == 0, result.stderr
    capture, processing = [json.loads(line) for line in log.read_text().splitlines()]
    assert capture[0].endswith("discourse_ingest.py")
    assert capture[capture.index("--mode", capture.index("--mode") + 1) + 1] == "backfill"
    assert capture[-2:] == ["--refresh-limit", "0"]
    assert processing[0].endswith("discourse_process.py")
    assert "--embed" not in processing


def test_shared_pipeline_lock_prevents_both_modes(tmp_path):
    environment, log = fake_python(tmp_path)
    with Path(environment["FORUM_LOCK_FILE"]).open("w") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        for script in ("run_scheduled_discourse.sh", "run_scheduled_backfill.sh"):
            result = subprocess.run(
                ["bash", str(SCRIPTS / script)],
                env=environment,
                capture_output=True,
                text=True,
                timeout=5,
            )
            assert result.returncode == 0
            assert "already running" in result.stdout
    assert not log.exists()
