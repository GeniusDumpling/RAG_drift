#!/usr/bin/env python3
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
from app.models.control import CrawlJob, SourceSite  # noqa: E402
from app.repositories.sources import SourcesRepository  # noqa: E402

DEMO_SOURCE_NAME = "Demo Docs"
DEMO_JOB_NAME = "Demo manual crawl"
DEMO_SEED_URL = "https://example.com/docs/telemetry-settings"


async def seed_demo() -> dict[str, str]:
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
        }


def main() -> None:
    print(json.dumps(asyncio.run(seed_demo()), sort_keys=True))


if __name__ == "__main__":
    main()
