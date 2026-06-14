#!/usr/bin/env python3
import argparse
import asyncio
import json
import sys
from pathlib import Path

from sqlalchemy import select

PROJECT_ROOT = Path(__file__).resolve().parents[1]
BACKEND_DIR = PROJECT_ROOT / "backend"

for import_path in (PROJECT_ROOT, BACKEND_DIR):
    import_path_str = str(import_path)
    if import_path_str not in sys.path:
        sys.path.insert(0, import_path_str)

import app.db.session as db_session_module  # noqa: E402
from app.models.control import CrawlJob, CrawlRun, SourceSite  # noqa: E402
from app.repositories.sources import SourcesRepository  # noqa: E402

DEMO_SOURCE_NAME = "Demo Docs"
DEMO_JOB_NAME = "Demo manual crawl"
DEMO_SEED_URL = "https://example.com/docs/telemetry-settings"


async def seed_demo(*, new_run: bool = False) -> dict[str, object]:
    async with db_session_module.AsyncSessionLocal() as session:
        repo = SourcesRepository(session)

        source = await session.scalar(
            select(SourceSite)
            .where(SourceSite.name == DEMO_SOURCE_NAME)
            .order_by(SourceSite.created_at.asc())
            .limit(1)
        )
        if source is None:
            source = await repo.create_source(
                {
                    "name": DEMO_SOURCE_NAME,
                    "site_type": "docs",
                    "base_url": "https://example.com/docs",
                    "allowed_domains": ["example.com"],
                    "fetch_mode": "manual",
                    "default_language": "en",
                    "active": True,
                    "config_json": {"kind": "demo"},
                }
            )
            await session.flush()

        job = await session.scalar(
            select(CrawlJob)
            .where(
                CrawlJob.source_site_id == source.id,
                CrawlJob.name == DEMO_JOB_NAME,
            )
            .order_by(CrawlJob.created_at.asc())
            .limit(1)
        )
        if job is None:
            job = await repo.create_job(
                {
                    "source_site_id": source.id,
                    "name": DEMO_JOB_NAME,
                    "trigger_mode": "manual",
                    "cron_expr": None,
                    "seed_config_json": {"urls": [DEMO_SEED_URL]},
                    "parser_profile": "official_site",
                    "max_pages": 1,
                    "enabled": True,
                    "agent_policy_json": {"extraction_mode": "hybrid"},
                }
            )
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
