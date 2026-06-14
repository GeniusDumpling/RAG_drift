#!/usr/bin/env python3
import argparse
import asyncio
import json
import os
import sys
from copy import deepcopy
from pathlib import Path
from typing import Any
from urllib.parse import ParseResult, parse_qsl, urlparse

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


class DemoSeedSafetyError(RuntimeError):
    """Raised when demo seeding would touch records not marked as demo-owned."""


def _copied_values(values: dict[str, Any]) -> dict[str, Any]:
    return {key: deepcopy(value) for key, value in values.items()}


LOCAL_DATABASE_HOSTS = {"localhost", "127.0.0.1", "::1"}


def _query_host_values(parsed: ParseResult) -> list[str]:
    return [
        value
        for key, value in parse_qsl(parsed.query, keep_blank_values=True)
        if key.lower() == "host"
    ]


def _is_local_database_host(value: str) -> bool:
    return value.lower() in LOCAL_DATABASE_HOSTS


def _safe_query_host(value: str) -> str:
    return (value or "<empty>").replace("\r", "%0D").replace("\n", "%0A")


def _safe_query_string(parsed: ParseResult) -> str:
    query_host_values = _query_host_values(parsed)
    return "&".join(f"host={_safe_query_host(host)}" for host in query_host_values)


def _safe_url(value: str) -> str:
    parsed = urlparse(value)
    scheme = parsed.scheme or "database"
    safe_query = _safe_query_string(parsed)
    if parsed.hostname is None:
        if scheme.lower().startswith("sqlite") or value in {":memory:", ""}:
            return f"{scheme}:<local>"
        result = f"{scheme}://<missing-host>"
        return f"{result}?{safe_query}" if safe_query else result

    host = parsed.hostname
    if ":" in host and not host.startswith("["):
        host = f"[{host}]"
    if parsed.port is not None:
        host = f"{host}:{parsed.port}"
    return parsed._replace(netloc=host, params="", query=safe_query, fragment="").geturl()


def _is_local_database_url(value: str) -> bool:
    if not value:
        return True

    parsed = urlparse(value)
    scheme = parsed.scheme.lower()
    if scheme.startswith("sqlite") or value == ":memory:":
        return True

    query_host_values = _query_host_values(parsed)
    if any(not _is_local_database_host(host) for host in query_host_values):
        return False

    host = parsed.hostname
    if host is None:
        # A hostless non-sqlite database URL is not clearly local; require an override.
        return False

    return _is_local_database_host(host)


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
    unmarked_sources = [source for source in candidates if not _source_has_demo_marker(source)]
    if unmarked_sources:
        source_ids = ", ".join(str(source.id) for source in unmarked_sources)
        raise DemoSeedSafetyError(
            "Refusing to seed demo because existing SourceSite named "
            f"'{DEMO_SOURCE_NAME}' is not marked as demo-owned "
            f"(expected config_json.kind == '{DEMO_KIND}'; id(s): {source_ids}). "
            "Rename or remove the unmarked record before running the demo seed."
        )
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
    unmarked_jobs = [job for job in candidates if not _job_has_demo_marker(job)]
    if unmarked_jobs:
        job_ids = ", ".join(str(job.id) for job in unmarked_jobs)
        raise DemoSeedSafetyError(
            "Refusing to seed demo because existing CrawlJob named "
            f"'{DEMO_JOB_NAME}' for SourceSite '{DEMO_SOURCE_NAME}' is not marked as "
            f"demo-owned (expected seed_config_json.kind == '{DEMO_KIND}' or "
            f"agent_policy_json.kind == '{DEMO_KIND}'; id(s): {job_ids}). "
            "Rename or remove the unmarked record before running the demo seed."
        )
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
    try:
        payload = asyncio.run(seed_demo(new_run=args.new_run))
    except DemoSeedSafetyError as exc:
        print(str(exc), file=sys.stderr)
        raise SystemExit(1) from None
    print(json.dumps(payload, sort_keys=True))


if __name__ == "__main__":
    main()
