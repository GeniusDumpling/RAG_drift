#!/usr/bin/env python3
import argparse
import asyncio
import json
import os
import sys
from copy import deepcopy
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

PROJECT_ROOT = Path(__file__).resolve().parents[1]
BACKEND_DIR = PROJECT_ROOT / "backend"

for import_path in (PROJECT_ROOT, BACKEND_DIR):
    import_path_str = str(import_path)
    if import_path_str not in sys.path:
        sys.path.insert(0, import_path_str)

import app.db.session as db_session_module  # noqa: E402
from app.core.config import get_settings  # noqa: E402
from app.models.control import CrawlJob, CrawlRun, SourceSite  # noqa: E402
from app.repositories.sources import SourcesRepository  # noqa: E402

DEMO_KIND = "demo"
DEMO_SOURCE_NAME = "Demo Docs"
DEMO_JOB_NAME = "Demo manual crawl"
DEMO_SEED_URL = "https://example.com/docs/telemetry-settings"

DEMO_SOURCE_VALUES: dict[str, Any] = {
    "name": DEMO_SOURCE_NAME,
    "site_type": "docs",
    "base_url": "https://example.com/docs",
    "allowed_domains": ["example.com"],
    "fetch_mode": "manual",
    "default_language": "en",
    "active": True,
    "config_json": {"kind": DEMO_KIND},
}
DEMO_JOB_VALUES: dict[str, Any] = {
    "name": DEMO_JOB_NAME,
    "trigger_mode": "manual",
    "cron_expr": None,
    "seed_config_json": {"kind": DEMO_KIND, "urls": [DEMO_SEED_URL]},
    "parser_profile": "official_site",
    "max_pages": 1,
    "enabled": True,
    "agent_policy_json": {"kind": DEMO_KIND, "extraction_mode": "hybrid"},
}


def _copied_values(values: dict[str, Any]) -> dict[str, Any]:
    return {key: deepcopy(value) for key, value in values.items()}


def _safe_url(value: str) -> str:
    parsed = urlparse(value)
    scheme = parsed.scheme or "database"
    if parsed.hostname is None:
        if scheme.lower().startswith("sqlite") or value in {":memory:", ""}:
            return f"{scheme}:<local>"
        return f"{scheme}://<local-or-unparseable>"

    host = parsed.hostname
    if ":" in host and not host.startswith("["):
        host = f"[{host}]"
    if parsed.port is not None:
        host = f"{host}:{parsed.port}"
    return parsed._replace(netloc=host, params="", query="", fragment="").geturl()


def _is_local_database_url(value: str) -> bool:
    if not value:
        return True

    parsed = urlparse(value)
    scheme = parsed.scheme.lower()
    if scheme.startswith("sqlite") or value == ":memory:":
        return True

    host = parsed.hostname
    if host is None:
        return True

    normalized = host.lower()
    return normalized == "localhost" or normalized == "::1" or normalized.startswith("127.")


def _configured_database_urls() -> tuple[tuple[str, str], tuple[str, str]]:
    settings = get_settings()
    return (
        ("DATABASE_URL", os.environ.get("DATABASE_URL") or settings.database_url),
        ("SYNC_DATABASE_URL", os.environ.get("SYNC_DATABASE_URL") or settings.sync_database_url),
    )


def require_local_demo_database() -> None:
    if os.environ.get("ALLOW_NONLOCAL_DEMO_SEED") == "1":
        return

    unsafe = [
        f"{name}={_safe_url(value)}"
        for name, value in _configured_database_urls()
        if not _is_local_database_url(value)
    ]
    if unsafe:
        print(
            "Refusing to seed demo against a non-local database. "
            "Set ALLOW_NONLOCAL_DEMO_SEED=1 to override. Offending setting(s): "
            + ", ".join(unsafe),
            file=sys.stderr,
        )
        raise SystemExit(1)


def _source_has_demo_marker(source: SourceSite) -> bool:
    config_json = dict(source.config_json or {})
    return config_json["kind"] == DEMO_KIND if "kind" in config_json else False


def _job_has_demo_marker(job: CrawlJob) -> bool:
    seed_config_json = dict(job.seed_config_json or {})
    agent_policy_json = dict(job.agent_policy_json or {})
    return seed_config_json.get("kind") == DEMO_KIND or agent_policy_json.get("kind") == DEMO_KIND


def reconcile_source(source: SourceSite) -> bool:
    changed = False
    for key, expected in DEMO_SOURCE_VALUES.items():
        if getattr(source, key) != expected:
            setattr(source, key, deepcopy(expected))
            changed = True
    return changed


def reconcile_job(job: CrawlJob) -> bool:
    changed = False
    for key, expected in DEMO_JOB_VALUES.items():
        if getattr(job, key) != expected:
            setattr(job, key, deepcopy(expected))
            changed = True
    return changed


def _run_config_snapshot(job: CrawlJob) -> dict[str, Any]:
    return {
        "job_name": job.name,
        "trigger_mode": job.trigger_mode,
        "seed_config_json": deepcopy(job.seed_config_json),
        "parser_profile": job.parser_profile,
        "max_pages": job.max_pages,
        "agent_policy_json": deepcopy(job.agent_policy_json),
    }


async def _find_demo_source(session: AsyncSession) -> SourceSite | None:
    result = await session.scalars(
        select(SourceSite)
        .where(SourceSite.name == DEMO_SOURCE_NAME)
        .order_by(SourceSite.created_at.asc())
    )
    candidates = list(result)
    for source in candidates:
        if _source_has_demo_marker(source):
            return source
    return candidates[0] if candidates else None


async def _find_demo_job(session: AsyncSession, source: SourceSite) -> CrawlJob | None:
    result = await session.scalars(
        select(CrawlJob)
        .where(
            CrawlJob.source_site_id == source.id,
            CrawlJob.name == DEMO_JOB_NAME,
        )
        .order_by(CrawlJob.created_at.asc())
    )
    candidates = list(result)
    for job in candidates:
        if _job_has_demo_marker(job):
            return job
    return candidates[0] if candidates else None


async def seed_demo(*, new_run: bool = False) -> dict[str, object]:
    require_local_demo_database()

    async with db_session_module.AsyncSessionLocal() as session:
        repo = SourcesRepository(session)

        source = await _find_demo_source(session)
        source_reconciled = False
        if source is None:
            source = await repo.create_source(_copied_values(DEMO_SOURCE_VALUES))
        else:
            source_reconciled = reconcile_source(source)
            if source_reconciled:
                await session.flush()

        job = await _find_demo_job(session, source)
        job_reconciled = False
        if job is None:
            job_values = _copied_values(DEMO_JOB_VALUES)
            job_values["source_site_id"] = source.id
            job = await repo.create_job(job_values)
        else:
            job_reconciled = reconcile_job(job)
            if job_reconciled:
                await session.flush()

        run = None
        reused_run = False
        if not new_run:
            run = await session.scalar(
                select(CrawlRun)
                .where(
                    CrawlRun.crawl_job_id == job.id,
                    CrawlRun.status == "queued",
                    CrawlRun.seed_url == DEMO_SEED_URL,
                )
                .order_by(CrawlRun.created_at.asc())
                .limit(1)
            )
            reused_run = run is not None
            if run is not None:
                expected_snapshot = _run_config_snapshot(job)
                if run.config_snapshot_json != expected_snapshot:
                    run.config_snapshot_json = expected_snapshot

        if run is None:
            run = await repo.create_run_with_queued_event(
                job,
                trigger_type="manual",
                status="queued",
                seed_url=DEMO_SEED_URL,
            )
        await session.commit()
        await session.refresh(source)
        await session.refresh(job)
        await session.refresh(run)

        return {
            "source_id": str(source.id),
            "job_id": str(job.id),
            "run_id": str(run.id),
            "reused_run": reused_run,
            "source_reconciled": source_reconciled,
            "job_reconciled": job_reconciled,
        }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Seed the deterministic demo crawl job.")
    parser.add_argument(
        "--new-run",
        action="store_true",
        help="Create a fresh queued demo run even when one is already queued.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    print(json.dumps(asyncio.run(seed_demo(new_run=args.new_run)), sort_keys=True))


if __name__ == "__main__":
    main()
