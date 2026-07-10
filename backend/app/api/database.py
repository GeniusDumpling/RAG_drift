from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_session
from app.core.config import get_settings
from app.repositories.database import (
    get_database_overview,
    get_table_config,
    get_table_row,
    list_table_metadata,
    list_table_rows,
)
from app.schemas.database import (
    DatabaseOverview,
    DatabaseTableList,
    DatabaseTableRow,
    DatabaseTableRowsPage,
)


def _require_database_browser_api() -> None:
    settings = get_settings()
    if settings.app_env != "dev" and not settings.database_browser_enabled:
        raise HTTPException(status_code=404, detail="Not found")


router = APIRouter(
    prefix="/database",
    tags=["database"],
    dependencies=[Depends(_require_database_browser_api)],
)
SessionDep = Annotated[AsyncSession, Depends(get_session)]
LimitQuery = Annotated[int, Query(ge=1, le=100)]
OffsetQuery = Annotated[int, Query(ge=0)]


@router.get("/overview", response_model=DatabaseOverview)
async def database_overview(session: SessionDep) -> DatabaseOverview:
    return await get_database_overview(session)


@router.get("/tables", response_model=DatabaseTableList)
async def database_tables() -> DatabaseTableList:
    items = list_table_metadata()
    return DatabaseTableList(items=items, total=len(items))


@router.get("/tables/{table_name}/rows", response_model=DatabaseTableRowsPage)
async def database_table_rows(
    table_name: str,
    session: SessionDep,
    q: Annotated[str | None, Query(max_length=200)] = None,
    limit: LimitQuery = 50,
    offset: OffsetQuery = 0,
) -> DatabaseTableRowsPage:
    config = get_table_config(table_name)
    if config is None:
        raise HTTPException(status_code=404, detail="Database table not found")
    return await list_table_rows(session, config=config, limit=limit, offset=offset, q=q)


@router.get("/tables/{table_name}/rows/{row_id}", response_model=DatabaseTableRow)
async def database_table_row(
    table_name: str,
    row_id: uuid.UUID,
    session: SessionDep,
) -> DatabaseTableRow:
    config = get_table_config(table_name)
    if config is None:
        raise HTTPException(status_code=404, detail="Database table not found")
    row = await get_table_row(session, config=config, row_id=row_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Database row not found")
    return row
