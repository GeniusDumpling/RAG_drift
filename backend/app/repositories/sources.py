import uuid
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.control import CrawlJob, CrawlRun, CrawlRunEvent, SourceSite


class SourcesRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def create_source(self, values: dict[str, Any]) -> SourceSite:
        source = SourceSite(**values)
        self.session.add(source)
        await self.session.flush()
        return source

    async def get_source(self, source_id: uuid.UUID) -> SourceSite | None:
        return await self.session.get(SourceSite, source_id)

    async def list_sources(self, limit: int, offset: int) -> tuple[list[SourceSite], int]:
        total = await self.session.scalar(select(func.count(SourceSite.id)))
        result = await self.session.scalars(
            select(SourceSite).order_by(SourceSite.created_at.desc()).offset(offset).limit(limit)
        )
        return list(result), int(total or 0)

    async def update_source(
        self, source_id: uuid.UUID, values: dict[str, Any]
    ) -> SourceSite | None:
        source = await self.get_source(source_id)
        if source is None:
            return None
        for key, value in values.items():
            setattr(source, key, value)
        await self.session.flush()
        return source

    async def create_job(self, values: dict[str, Any]) -> CrawlJob:
        job = CrawlJob(**values)
        self.session.add(job)
        await self.session.flush()
        return job

    async def get_job(self, job_id: uuid.UUID) -> CrawlJob | None:
        return await self.session.get(CrawlJob, job_id)

    async def list_jobs(self, limit: int, offset: int) -> tuple[list[CrawlJob], int]:
        total = await self.session.scalar(select(func.count(CrawlJob.id)))
        result = await self.session.scalars(
            select(CrawlJob).order_by(CrawlJob.created_at.desc()).offset(offset).limit(limit)
        )
        return list(result), int(total or 0)

    async def update_job(self, job_id: uuid.UUID, values: dict[str, Any]) -> CrawlJob | None:
        job = await self.get_job(job_id)
        if job is None:
            return None
        for key, value in values.items():
            setattr(job, key, value)
        await self.session.flush()
        return job

    async def create_run_with_queued_event(
        self,
        job: CrawlJob,
        *,
        trigger_type: str,
        status: str,
        seed_url: str | None,
    ) -> CrawlRun:
        run = CrawlRun(
            source_site_id=job.source_site_id,
            crawl_job_id=job.id,
            trigger_type=trigger_type,
            seed_url=seed_url,
            status=status,
            config_snapshot_json={
                "job_name": job.name,
                "trigger_mode": job.trigger_mode,
                "seed_config_json": job.seed_config_json,
                "parser_profile": job.parser_profile,
                "max_pages": job.max_pages,
                "agent_policy_json": job.agent_policy_json,
            },
        )
        self.session.add(run)
        await self.session.flush()

        event = CrawlRunEvent(
            crawl_run_id=run.id,
            stage="queue",
            level="info",
            event_type="run_queued",
            message="Crawl run queued for execution.",
            related_url=seed_url,
            counters_json={},
            agent_trace_json={},
        )
        self.session.add(event)
        await self.session.flush()
        return run

    async def get_run(self, run_id: uuid.UUID) -> CrawlRun | None:
        return await self.session.get(CrawlRun, run_id)

    async def list_runs(self, limit: int, offset: int) -> tuple[list[CrawlRun], int]:
        total = await self.session.scalar(select(func.count(CrawlRun.id)))
        result = await self.session.scalars(
            select(CrawlRun).order_by(CrawlRun.created_at.desc()).offset(offset).limit(limit)
        )
        return list(result), int(total or 0)

    async def list_run_events(
        self, run_id: uuid.UUID, limit: int, offset: int
    ) -> tuple[list[CrawlRunEvent], int]:
        total = await self.session.scalar(
            select(func.count(CrawlRunEvent.id)).where(CrawlRunEvent.crawl_run_id == run_id)
        )
        result = await self.session.scalars(
            select(CrawlRunEvent)
            .where(CrawlRunEvent.crawl_run_id == run_id)
            .order_by(CrawlRunEvent.created_at.asc())
            .offset(offset)
            .limit(limit)
        )
        return list(result), int(total or 0)
