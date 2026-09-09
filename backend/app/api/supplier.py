import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_session
from app.repositories.supplier import (
    get_supplier_overview,
    list_supplier_relations,
    list_supplier_verifications,
)
from app.schemas.sources import Page
from app.schemas.supplier import (
    SupplierOverview,
    SupplierRelationRead,
    SupplierVerificationRead,
)

router = APIRouter(prefix="/supplier", tags=["supplier"])
SessionDep = Annotated[AsyncSession, Depends(get_session)]
LimitQuery = Annotated[int, Query(ge=1, le=100)]
OffsetQuery = Annotated[int, Query(ge=0)]


@router.get("/overview", response_model=SupplierOverview)
async def supplier_overview(session: SessionDep) -> SupplierOverview:
    return await get_supplier_overview(session)


@router.get("/relations", response_model=Page[SupplierRelationRead])
async def supplier_relations(
    session: SessionDep,
    q: Annotated[str | None, Query(max_length=200)] = None,
    credibility: Annotated[str | None, Query(pattern="^(明确|疑似)$")] = None,
    verify_status: Annotated[
        str | None, Query(pattern="^(未验证|已验证|验证失败)$")
    ] = None,
    limit: LimitQuery = 50,
    offset: OffsetQuery = 0,
) -> Page[SupplierRelationRead]:
    return await list_supplier_relations(
        session, limit=limit, offset=offset, q=q, credibility=credibility, verify_status=verify_status
    )


@router.get("/relations/{relation_id}/verifications", response_model=list[SupplierVerificationRead])
async def supplier_verifications(
    relation_id: uuid.UUID,
    session: SessionDep,
) -> list[SupplierVerificationRead]:
    return await list_supplier_verifications(session, relation_id=relation_id)