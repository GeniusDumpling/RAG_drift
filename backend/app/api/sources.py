import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_session
from app.repositories.sources import SourcesRepository
from app.schemas.sources import (
    CrawlJobCreate,
    CrawlJobRead,
    CrawlJobUpdate,
    CrawlRunEventRead,
    CrawlRunRead,
    CrawlRunTrigger,
    Page,
    SourceSiteCreate,
    SourceSiteRead,
    SourceSiteUpdate,
)

VALID_RUN_STATUSES = {"queued", "running", "success", "partial", "failed"}
VALID_TRIGGER_TYPES = {"manual", "cron", "backfill"}

router = APIRouter()
SessionDep = Annotated[AsyncSession, Depends(get_session)]
LimitQuery = Annotated[int, Query(ge=1, le=100)]
OffsetQuery = Annotated[int, Query(ge=0)]


@router.post("/sources", response_model=SourceSiteRead, status_code=status.HTTP_201_CREATED)
async def create_source(payload: SourceSiteCreate, session: SessionDep) -> SourceSiteRead:
    repo = SourcesRepository(session)
    source = await repo.create_source(payload.model_dump())
    await session.commit()
    await session.refresh(source)
    return SourceSiteRead.model_validate(source)


@router.get("/sources", response_model=Page[SourceSiteRead])
async def list_sources(
    session: SessionDep,
    limit: LimitQuery = 50,
    offset: OffsetQuery = 0,
) -> Page[SourceSiteRead]:
    repo = SourcesRepository(session)
    items, total = await repo.list_sources(limit=limit, offset=offset)
    return Page[SourceSiteRead](
        items=[SourceSiteRead.model_validate(item) for item in items],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.get("/sources/{source_id}", response_model=SourceSiteRead)
async def get_source(source_id: uuid.UUID, session: SessionDep) -> SourceSiteRead:
    repo = SourcesRepository(session)
    source = await repo.get_source(source_id)
    if source is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Source not found")
    return SourceSiteRead.model_validate(source)


@router.patch("/sources/{source_id}", response_model=SourceSiteRead)
async def update_source(
    source_id: uuid.UUID, payload: SourceSiteUpdate, session: SessionDep
) -> SourceSiteRead:
    repo = SourcesRepository(session)
    source = await repo.update_source(source_id, payload.model_dump(exclude_unset=True))
    if source is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Source not found")
    await session.commit()
    await session.refresh(source)
    return SourceSiteRead.model_validate(source)


@router.post("/jobs", response_model=CrawlJobRead, status_code=status.HTTP_201_CREATED)
async def create_job(payload: CrawlJobCreate, session: SessionDep) -> CrawlJobRead:
    repo = SourcesRepository(session)
    if await repo.get_source(payload.source_site_id) is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Source not found")

    job = await repo.create_job(payload.model_dump())
    await session.commit()
    await session.refresh(job)
    return CrawlJobRead.model_validate(job)


@router.get("/jobs", response_model=Page[CrawlJobRead])
async def list_jobs(
    session: SessionDep,
    limit: LimitQuery = 50,
    offset: OffsetQuery = 0,
) -> Page[CrawlJobRead]:
    repo = SourcesRepository(session)
    items, total = await repo.list_jobs(limit=limit, offset=offset)
    return Page[CrawlJobRead](
        items=[CrawlJobRead.model_validate(item) for item in items],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.patch("/jobs/{job_id}", response_model=CrawlJobRead)
async def update_job(
    job_id: uuid.UUID, payload: CrawlJobUpdate, session: SessionDep
) -> CrawlJobRead:
    repo = SourcesRepository(session)
    values = payload.model_dump(exclude_unset=True)
    source_site_id = values.get("source_site_id")
    if source_site_id is not None and await repo.get_source(source_site_id) is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Source not found")

    job = await repo.update_job(job_id, values)
    if job is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Job not found")
    await session.commit()
    await session.refresh(job)
    return CrawlJobRead.model_validate(job)


@router.post(
    "/jobs/{job_id}/trigger",
    response_model=CrawlRunRead,
    status_code=status.HTTP_201_CREATED,
)
async def trigger_job(
    job_id: uuid.UUID, payload: CrawlRunTrigger, session: SessionDep
) -> CrawlRunRead:
    repo = SourcesRepository(session)
    job = await repo.get_job(job_id)
    if job is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Job not found")

    trigger_type = "manual"
    run_status = "queued"
    if trigger_type not in VALID_TRIGGER_TYPES or run_status not in VALID_RUN_STATUSES:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Invalid run state",
        )

    run = await repo.create_run_with_queued_event(
        job,
        trigger_type=trigger_type,
        status=run_status,
        seed_url=payload.seed_url,
    )
    await session.commit()
    await session.refresh(run)
    return CrawlRunRead.model_validate(run)


@router.get("/runs", response_model=Page[CrawlRunRead])
async def list_runs(
    session: SessionDep,
    limit: LimitQuery = 50,
    offset: OffsetQuery = 0,
) -> Page[CrawlRunRead]:
    repo = SourcesRepository(session)
    items, total = await repo.list_runs(limit=limit, offset=offset)
    return Page[CrawlRunRead](
        items=[CrawlRunRead.model_validate(item) for item in items],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.get("/runs/{run_id}", response_model=CrawlRunRead)
async def get_run(run_id: uuid.UUID, session: SessionDep) -> CrawlRunRead:
    repo = SourcesRepository(session)
    run = await repo.get_run(run_id)
    if run is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Run not found")
    return CrawlRunRead.model_validate(run)


@router.get("/runs/{run_id}/events", response_model=Page[CrawlRunEventRead])
async def list_run_events(
    run_id: uuid.UUID,
    session: SessionDep,
    limit: LimitQuery = 50,
    offset: OffsetQuery = 0,
) -> Page[CrawlRunEventRead]:
    repo = SourcesRepository(session)
    if await repo.get_run(run_id) is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Run not found")

    items, total = await repo.list_run_events(run_id=run_id, limit=limit, offset=offset)
    return Page[CrawlRunEventRead](
        items=[CrawlRunEventRead.model_validate(item) for item in items],
        total=total,
        limit=limit,
        offset=offset,
    )
