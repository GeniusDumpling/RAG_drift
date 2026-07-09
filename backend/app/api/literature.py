import json
import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_session
from app.repositories.literature import LiteratureRepository
from app.schemas.literature import (
    LiteratureArtifactRead,
    LiteratureEvidenceRead,
    LiteraturePaperRead,
    LiteratureResultItem,
    LiteratureRunCreate,
    LiteratureRunEventRead,
    LiteratureRunPaperRead,
    LiteratureRunRead,
    LiteratureRunResults,
)
from app.schemas.sources import Page

router = APIRouter()
SessionDep = Annotated[AsyncSession, Depends(get_session)]
LimitQuery = Annotated[int, Query(ge=1, le=100)]
OffsetQuery = Annotated[int, Query(ge=0)]


@router.post(
    "/literature-runs", response_model=LiteratureRunRead, status_code=status.HTTP_201_CREATED
)
async def create_literature_run(
    payload: LiteratureRunCreate, session: SessionDep
) -> LiteratureRunRead:
    repo = LiteratureRepository(session)
    options = payload.model_dump(exclude={"query"})
    run = await repo.create_run(payload.query, options)
    await session.commit()
    await session.refresh(run)
    return LiteratureRunRead.model_validate(run)


@router.get("/literature-runs", response_model=Page[LiteratureRunRead])
async def list_literature_runs(
    session: SessionDep, limit: LimitQuery = 20, offset: OffsetQuery = 0
) -> Page[LiteratureRunRead]:
    rows, total = await LiteratureRepository(session).list_runs(limit, offset)
    return Page[LiteratureRunRead](
        items=[LiteratureRunRead.model_validate(row) for row in rows],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.get("/literature-runs/{run_id}", response_model=LiteratureRunRead)
async def get_literature_run(run_id: uuid.UUID, session: SessionDep) -> LiteratureRunRead:
    run = await LiteratureRepository(session).get_run(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="Literature run not found")
    return LiteratureRunRead.model_validate(run)


@router.get("/literature-runs/{run_id}/events", response_model=Page[LiteratureRunEventRead])
async def list_literature_run_events(
    run_id: uuid.UUID,
    session: SessionDep,
    limit: LimitQuery = 100,
    offset: OffsetQuery = 0,
) -> Page[LiteratureRunEventRead]:
    repo = LiteratureRepository(session)
    if await repo.get_run(run_id) is None:
        raise HTTPException(status_code=404, detail="Literature run not found")
    rows, total = await repo.list_events(run_id, limit, offset)
    return Page[LiteratureRunEventRead](
        items=[LiteratureRunEventRead.model_validate(row) for row in rows],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.get("/literature-runs/{run_id}/results", response_model=LiteratureRunResults)
async def get_literature_run_results(
    run_id: uuid.UUID, session: SessionDep
) -> LiteratureRunResults:
    repo = LiteratureRepository(session)
    run = await repo.get_run(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="Literature run not found")
    pairs = await repo.result_rows(run_id)
    links = [link for link, _paper in pairs]
    evidence = await repo.evidence_for_links([link.id for link in links])
    artifacts = await repo.artifacts_for_run(run_id)
    by_paper: dict[uuid.UUID, list[Any]] = {}
    for artifact in artifacts:
        if artifact.paper_id is not None:
            by_paper.setdefault(artifact.paper_id, []).append(artifact)
    return LiteratureRunResults(
        run=LiteratureRunRead.model_validate(run),
        items=[
            LiteratureResultItem(
                selection=LiteratureRunPaperRead.model_validate(link),
                paper=LiteraturePaperRead.model_validate(paper),
                evidence=[
                    LiteratureEvidenceRead.model_validate(row) for row in evidence.get(link.id, [])
                ],
                artifacts=[
                    LiteratureArtifactRead.model_validate(row) for row in by_paper.get(paper.id, [])
                ],
            )
            for link, paper in pairs
        ],
        run_artifacts=[
            LiteratureArtifactRead.model_validate(row) for row in artifacts if row.paper_id is None
        ],
    )


@router.post("/literature-runs/{run_id}/cancel", response_model=LiteratureRunRead)
async def cancel_literature_run(run_id: uuid.UUID, session: SessionDep) -> LiteratureRunRead:
    repo = LiteratureRepository(session)
    run = await repo.get_run(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="Literature run not found")
    await repo.cancel_run(run)
    await session.commit()
    await session.refresh(run)
    return LiteratureRunRead.model_validate(run)


@router.get("/literature-papers/{paper_id}", response_model=LiteraturePaperRead)
async def get_literature_paper(paper_id: uuid.UUID, session: SessionDep) -> LiteraturePaperRead:
    paper = await LiteratureRepository(session).get_paper(paper_id)
    if paper is None:
        raise HTTPException(status_code=404, detail="Literature paper not found")
    return LiteraturePaperRead.model_validate(paper)


@router.get("/literature-artifacts/{artifact_id}/download", response_class=Response)
async def download_literature_artifact(artifact_id: uuid.UUID, session: SessionDep) -> Response:
    artifact = await LiteratureRepository(session).get_artifact(artifact_id)
    if artifact is None:
        raise HTTPException(status_code=404, detail="Literature artifact not found")
    if artifact.binary_data is not None:
        body = artifact.binary_data
    elif artifact.text_data is not None:
        body = artifact.text_data.encode("utf-8")
    elif artifact.json_data not in ({}, []):
        body = json.dumps(artifact.json_data, ensure_ascii=False, indent=2).encode("utf-8")
    else:
        raise HTTPException(status_code=404, detail="Artifact has no downloadable payload")
    extension = {
        "application/pdf": "pdf",
        "application/json": "json",
        "text/markdown": "md",
        "text/plain": "txt",
    }.get(artifact.mime_type or "", "bin")
    filename = f"{artifact.artifact_type}-{artifact.id}.{extension}"
    return Response(
        content=body,
        media_type=artifact.mime_type or "application/octet-stream",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
