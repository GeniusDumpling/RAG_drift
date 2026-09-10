"""Portable scheduled wrappers: real shell execution, fake Docker boundary."""

import ast
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


class JobRunnerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)
        self.bin = self.base / "bin"
        self.bin.mkdir()
        self.log = self.base / "docker.log"
        docker = self.bin / "docker"
        docker.write_text("""#!/usr/bin/env python3
import json, os, sys, time
from pathlib import Path
args = sys.argv[1:]
with open(os.environ['DOCKER_LOG'], 'a') as f: f.write(json.dumps(args) + '\\n')
if args[0] == 'compose':
    if '--cidfile' in args:
        Path(args[args.index('--cidfile')+1]).write_text('test-owned-id')
    label = args[args.index('--label')+1].split('=', 1)[1]
    Path(os.environ['DOCKER_LOG']+'.token').write_text(label)
    time.sleep(float(os.environ.get('FAKE_SLEEP', '0')))
    sys.exit(int(os.environ.get('FAKE_EXIT', '0')))
if args[0] == 'inspect':
    token = Path(os.environ['DOCKER_LOG']+'.token').read_text()
    print('test-owned-id', os.environ.get('FAKE_INSPECT_TOKEN', token))
""")
        docker.chmod(0o755)
        self.env = dict(
            os.environ,
            PATH=f"{self.bin}:{os.environ['PATH']}",
            DOCKER_LOG=str(self.log),
            JOB_LOCK_DIR=str(self.base / "locks"),
        )

    def run_job(self, job, **env):
        return subprocess.run(
            ["bash", str(ROOT / "deploy/run-job.sh"), job],
            env=dict(self.env, **env),
            capture_output=True,
            text=True,
            timeout=10,
        )

    def calls(self):
        return [json.loads(line) for line in self.log.read_text().splitlines()]

    def test_four_jobs_use_isolated_named_compose_runs(self):
        expected = {
            "video-collect": "tools/video-crawler/scripts/run_scheduled_collect.sh",
            "discourse-collect": "tools/forum-crawler/scripts/run_scheduled_discourse.sh",
            "supplier-pipeline": "tools/supplier-information/pipeline.py",
            "supplier-verify": "tools/supplier-information/supplier_verify.py",
        }
        for job, command in expected.items():
            with self.subTest(job=job):
                result = self.run_job(job)
                self.assertEqual(result.returncode, 0, result.stderr)
                run = [call for call in self.calls() if call[0] == "compose"][-1]
                for option in ("--rm", "--no-deps", "--name", "--entrypoint"):
                    self.assertIn(option, run)
                self.assertEqual(run[run.index("--name") + 1], f"intelligence-rag-{job}")
                self.assertIn("jobs", run)
                self.assertIn(command, run)

    def test_timeout_removes_only_owned_container(self):
        result = self.run_job("video-collect", JOB_TIMEOUT="0.2s", FAKE_SLEEP="2")
        self.assertEqual(result.returncode, 124, result.stderr)
        self.assertIn(["rm", "-f", "test-owned-id"], self.calls())

    def test_timeout_does_not_remove_foreign_container(self):
        result = self.run_job(
            "video-collect", JOB_TIMEOUT="0.2s", FAKE_SLEEP="2", FAKE_INSPECT_TOKEN="foreign-token"
        )
        self.assertEqual(result.returncode, 124, result.stderr)
        self.assertFalse(any(c[0] == "rm" for c in self.calls()))

    def test_job_lock_skips_without_touching_docker(self):
        import fcntl

        lockdir = Path(self.env["JOB_LOCK_DIR"])
        lockdir.mkdir()
        with (lockdir / "video-collect.lock").open("w") as handle:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
            result = self.run_job("video-collect")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(self.log.exists())

    def test_validate_mode_uses_only_imports_and_compilation(self):
        result = subprocess.run(
            ["bash", str(ROOT / "deploy/run-job.sh"), "video-collect", "--validate"],
            env=self.env,
            capture_output=True,
            text=True,
            timeout=10,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        run = next(c for c in self.calls() if c[0] == "compose")
        self.assertEqual(run[run.index("--entrypoint") + 1], "python")
        self.assertIn("-c", run)
        self.assertIn("compile(", run[run.index("-c") + 1])
        self.assertNotIn("--scheduled", run)

    def test_systemd_templates_are_host_only(self):
        service = ROOT / "deploy/systemd/intelligence-rag-video-collect.service"
        timer = ROOT / "deploy/systemd/intelligence-rag-video-collect.timer"
        self.assertTrue(service.is_file())
        self.assertTrue(timer.is_file())
        self.assertIn("WorkingDirectory=/opt/intelligence-rag", service.read_text())
        self.assertIn("deploy/run-job.sh video-collect", service.read_text())
        self.assertIn("Persistent=true", timer.read_text())

    def test_exit_status_and_invalid_job(self):
        self.assertEqual(self.run_job("supplier-verify", FAKE_EXIT="7").returncode, 7)
        self.assertEqual(self.run_job("invalid").returncode, 64)


class CrawlerPortabilityTests(unittest.TestCase):
    def function(self, name, **namespace):
        source = ROOT / "tools/video-crawler/scripts/pipeline.py"
        node = next(
            n
            for n in ast.parse(source.read_text()).body
            if isinstance(n, ast.FunctionDef) and n.name == name
        )
        scope = dict(
            os=os,
            Path=Path,
            SCRIPT_DIR=source.parent,
            conf_get=lambda conf, section, key, default: conf.get(section, {}).get(key, default),
            **namespace,
        )
        exec(compile(ast.Module(body=[node], type_ignores=[]), str(source), "exec"), scope)
        return scope[name]

    def test_empty_environment_proxy_overrides_yaml_for_all_aliases(self):
        from unittest.mock import patch

        with patch.dict(os.environ, {"HTTPS_PROXY": ""}, clear=True):
            self.function("apply_proxy")({"download": {"proxy": "http://config.invalid:8080"}})
            for key in (
                "HTTPS_PROXY",
                "HTTP_PROXY",
                "https_proxy",
                "http_proxy",
                "ALL_PROXY",
                "all_proxy",
            ):
                self.assertEqual(os.environ.get(key, ""), "", key)

    def test_lowercase_proxy_precedes_yaml(self):
        from unittest.mock import patch

        with patch.dict(os.environ, {"https_proxy": "http://environment.invalid:8080"}, clear=True):
            self.function("apply_proxy")({"download": {"proxy": "http://config.invalid:8080"}})
            self.assertEqual(os.environ["HTTPS_PROXY"], "http://environment.invalid:8080")

    def test_state_path_environment_override(self):
        from unittest.mock import patch

        with patch.dict(os.environ, {"VIDEO_SEARCH_STATE_PATH": "/data/video/search-state.json"}):
            self.assertEqual(
                self.function("resolve_search_state_path")({}),
                Path("/data/video/search-state.json"),
            )

    def test_shell_wrappers_use_system_python_without_venv_or_uv(self):
        import shutil

        for relative in (
            "tools/video-crawler/scripts/run_scheduled_collect.sh",
            "tools/forum-crawler/scripts/run_scheduled_discourse.sh",
        ):
            with self.subTest(script=relative), tempfile.TemporaryDirectory() as directory:
                base = Path(directory)
                script = base / relative
                script.parent.mkdir(parents=True)
                shutil.copy2(ROOT / relative, script)
                bindir = base / "bin"
                bindir.mkdir()
                fake = bindir / "python3"
                fake.write_text('#!/bin/bash\nprintf "PYTHON_OK %s\\n" "$@"\n')
                fake.chmod(0o755)
                result = subprocess.run(
                    ["bash", str(script)],
                    capture_output=True,
                    text=True,
                    env={"PATH": f"{bindir}:/usr/bin:/bin"},
                    timeout=5,
                )
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn("PYTHON_OK", result.stdout)


if __name__ == "__main__":
    unittest.main()
