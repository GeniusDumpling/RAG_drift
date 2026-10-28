from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from fastapi.responses import FileResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.database import _require_database_browser_api
from app.repositories import document_library as repo
from app.repositories.database_catalog import get_document_session

router = APIRouter(
    prefix="/document-library",
    tags=["document-library"],
    dependencies=[Depends(_require_database_browser_api)],
)
Session = Annotated[AsyncSession, Depends(get_document_session)]
Limit = Annotated[int, Query(ge=1, le=100)]
Offset = Annotated[int, Query(ge=0)]


@router.get("/models")
async def models(session: Session) -> dict:
    return await repo.models(session)


@router.get("/assets/{asset_id}")
async def asset(asset_id: UUID, session: Session) -> FileResponse:
    data = await repo.asset(session, asset_id)
    return FileResponse(**data, headers={"X-Content-Type-Options": "nosniff"})


@router.get("")
async def documents(
    session: Session,
    q: Annotated[str | None, Query(max_length=200)] = None,
    model: Annotated[str | None, Query(max_length=100)] = None,
    limit: Limit = 50,
    offset: Offset = 0,
) -> dict:
    return await repo.list_documents(session, q=q, model=model, limit=limit, offset=offset)


@router.get("/{document_id}")
async def detail(document_id: UUID, session: Session, processing_id: UUID | None = None) -> dict:
    return await repo.detail(session, document_id, processing_id)


@router.get("/{document_id}/{section}")
async def section(
    document_id: UUID,
    section: Literal["pages", "elements", "chunks"],
    session: Session,
    processing_id: UUID | None = None,
    kind: Annotated[str | None, Query(max_length=40)] = None,
    page_no: Annotated[int | None, Query(ge=1)] = None,
    limit: Limit = 30,
    offset: Offset = 0,
) -> dict:
    return await repo.section(
        session,
        document_id,
        section,
        processing_id=processing_id,
        kind=kind,
        page_no=page_no,
        limit=limit,
        offset=offset,
    )
