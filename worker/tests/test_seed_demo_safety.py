import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

from sqlalchemy import Engine, create_engine, select
from sqlalchemy.orm import Session

from app.models.control import CrawlJob, CrawlRun, SourceSite

REPO_ROOT = Path(__file__).resolve().parents[2]
DEMO_SOURCE_NAME = "Demo Docs"
DEMO_JOB_NAME = "Demo manual crawl"
DEMO_SEED_URL = "https://example.com/docs/telemetry-settings"

EXPECTED_SOURCE_VALUES: dict[str, Any] = {
    "name": DEMO_SOURCE_NAME,
    "site_type": "docs",
    "base_url": "https://example.com/docs",
    "allowed_domains": ["example.com"],
    "fetch_mode": "manual",
    "default_language": "en",
    "active": True,
    "config_json": {"kind": "demo"},
}
EXPECTED_JOB_VALUES: dict[str, Any] = {
    "name": DEMO_JOB_NAME,
    "trigger_mode": "manual",
    "cron_expr": None,
    "seed_config_json": {"kind": "demo", "urls": [DEMO_SEED_URL]},
    "parser_profile": "official_site",
    "max_pages": 1,
    "enabled": True,
    "agent_policy_json": {"kind": "demo", "extraction_mode": "hybrid"},
}


def _run_seed_demo(*args: str) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env.pop("PYTHONPATH", None)
    return subprocess.run(
        [sys.executable, "scripts/seed_demo.py", *args],
        cwd=REPO_ROOT,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )


def _engine() -> Engine:
    return create_engine(os.environ["SYNC_DATABASE_URL"], pool_pre_ping=True)


def test_seed_demo_refuses_unmarked_same_name_source_without_modifying_it() -> None:
    engine = _engine()
    try:
        with Session(engine) as session:
            source = SourceSite(
                name=DEMO_SOURCE_NAME,
                site_type="forum",
                base_url="https://dev.example.local/docs",
                allowed_domains=["dev.example.local"],
                fetch_mode="scheduled",
                default_language="fr",
                active=False,
                config_json={"owner": "developer"},
            )
            session.add(source)
            session.commit()
            source_id = source.id

        completed = _run_seed_demo()

        assert completed.returncode == 1
        assert "Refusing to seed demo" in completed.stderr
        assert "SourceSite named 'Demo Docs'" in completed.stderr
        assert "config_json.kind" in completed.stderr
        with Session(engine) as session:
            stored_source = session.get(SourceSite, source_id)
            assert stored_source is not None
            assert stored_source.site_type == "forum"
            assert stored_source.base_url == "https://dev.example.local/docs"
            assert stored_source.allowed_domains == ["dev.example.local"]
            assert stored_source.fetch_mode == "scheduled"
            assert stored_source.default_language == "fr"
            assert stored_source.active is False
            assert stored_source.config_json == {"owner": "developer"}
            assert len(session.scalars(select(SourceSite)).all()) == 1
            assert session.scalars(select(CrawlJob)).all() == []
    finally:
        engine.dispose()


def test_seed_demo_refuses_unmarked_same_name_job_without_modifying_it() -> None:
    engine = _engine()
    try:
        with Session(engine) as session:
            source = SourceSite(**EXPECTED_SOURCE_VALUES)
            session.add(source)
            session.flush()
            job = CrawlJob(
                source_site_id=source.id,
                name=DEMO_JOB_NAME,
                trigger_mode="scheduled",
                cron_expr="0 0 * * *",
                seed_config_json={"urls": ["https://dev.example.local/manual"]},
                parser_profile="developer_profile",
                max_pages=42,
                enabled=False,
                agent_policy_json={"extraction_mode": "manual"},
            )
            session.add(job)
            session.commit()
            job_id = job.id

        completed = _run_seed_demo()

        assert completed.returncode == 1
        assert "Refusing to seed demo" in completed.stderr
        assert "CrawlJob named 'Demo manual crawl'" in completed.stderr
        assert "seed_config_json.kind" in completed.stderr
        assert "agent_policy_json.kind" in completed.stderr
        with Session(engine) as session:
            stored_job = session.get(CrawlJob, job_id)
            assert stored_job is not None
            assert stored_job.trigger_mode == "scheduled"
            assert stored_job.cron_expr == "0 0 * * *"
            assert stored_job.seed_config_json == {"urls": ["https://dev.example.local/manual"]}
            assert stored_job.parser_profile == "developer_profile"
            assert stored_job.max_pages == 42
            assert stored_job.enabled is False
            assert stored_job.agent_policy_json == {"extraction_mode": "manual"}
            assert session.scalars(select(CrawlRun)).all() == []
    finally:
        engine.dispose()


def test_seed_demo_reconciles_marked_same_name_demo_records() -> None:
    engine = _engine()
    try:
        with Session(engine) as session:
            source = SourceSite(
                name=DEMO_SOURCE_NAME,
                site_type="forum",
                base_url="https://example.com/old-docs",
                allowed_domains=["legacy.example.com"],
                fetch_mode="scheduled",
                default_language="fr",
                active=False,
                config_json={"kind": "demo", "stale": True},
            )
            session.add(source)
            session.flush()
            job = CrawlJob(
                source_site_id=source.id,
                name=DEMO_JOB_NAME,
                trigger_mode="scheduled",
                cron_expr="0 0 * * *",
                seed_config_json={"kind": "demo", "urls": ["https://example.com/old"]},
                parser_profile="legacy_profile",
                max_pages=42,
                enabled=False,
                agent_policy_json={"kind": "demo", "extraction_mode": "manual"},
            )
            session.add(job)
            session.commit()
            source_id = source.id
            job_id = job.id

        completed = _run_seed_demo()

        assert completed.returncode == 0, completed.stderr
        payload = json.loads(completed.stdout)
        assert payload["source_id"] == str(source_id)
        assert payload["job_id"] == str(job_id)
        assert payload["source_reconciled"] is True
        assert payload["job_reconciled"] is True
        with Session(engine) as session:
            stored_source = session.get(SourceSite, source_id)
            stored_job = session.get(CrawlJob, job_id)
            assert stored_source is not None
            assert stored_job is not None
            for key, expected in EXPECTED_SOURCE_VALUES.items():
                assert getattr(stored_source, key) == expected
            for key, expected in EXPECTED_JOB_VALUES.items():
                assert getattr(stored_job, key) == expected
            assert len(session.scalars(select(CrawlRun)).all()) == 1
    finally:
        engine.dispose()
