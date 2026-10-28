from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.repositories import scoped_database
from app.repositories.database import (
    get_database_overview,
    get_table_config,
    get_table_row,
    list_table_metadata,
    list_table_rows,
)
from app.repositories.database_catalog import database_targets, get_browser_session
from app.schemas.database import (
    DatabaseOverview,
    DatabaseTableList,
    DatabaseTableRow,
    DatabaseTableRowsPage,
)


def _require_database_browser_api() -> None:
    settings = get_settings()
    if settings.app_env != "dev" and not getattr(settings, "database_browser_enabled", False):
        raise HTTPException(status_code=404, detail="Not found")


router = APIRouter(
    prefix="/database",
    tags=["database"],
    dependencies=[Depends(_require_database_browser_api)],
)
SessionDep = Annotated[AsyncSession, Depends(get_browser_session)]
LimitQuery = Annotated[int, Query(ge=1, le=100)]
OffsetQuery = Annotated[int, Query(ge=0)]


@router.get("/catalog")
async def database_catalog() -> dict:
    return {
        "items": [
            {
                "id": t.id,
                "label": t.label,
                "database_name": make_url(t.url).database,
                "collection": t.collection,
            }
            for t in database_targets().values()
        ]
    }


@router.get("/overview", response_model=DatabaseOverview)
async def database_overview(session: SessionDep, database: str = "primary") -> DatabaseOverview:
    if database != "primary":
        return await scoped_database.overview(session, database)
    return await get_database_overview(session)


@router.get("/tables", response_model=DatabaseTableList)
async def database_tables(database: str = "primary") -> DatabaseTableList:
    items = list_table_metadata() if database == "primary" else scoped_database.metadata(database)
    return DatabaseTableList(items=items, total=len(items))


@router.get("/tables/{table_name}/rows", response_model=DatabaseTableRowsPage)
async def database_table_rows(
    table_name: str,
    session: SessionDep,
    q: Annotated[str | None, Query(max_length=200)] = None,
    limit: LimitQuery = 50,
    offset: OffsetQuery = 0,
    database: str = "primary",
) -> DatabaseTableRowsPage:
    if database != "primary":
        return await scoped_database.rows(session, database, table_name, limit, offset, q)
    config = get_table_config(table_name)
    if config is None:
        raise HTTPException(status_code=404, detail="Database table not found")
    return await list_table_rows(session, config=config, limit=limit, offset=offset, q=q)


@router.get("/tables/{table_name}/rows/{row_id}", response_model=DatabaseTableRow)
async def database_table_row(
    table_name: str,
    row_id: uuid.UUID,
    session: SessionDep,
    database: str = "primary",
) -> DatabaseTableRow:
    if database != "primary":
        result = await scoped_database.rows(session, database, table_name, 1, 0, row_id=row_id)
        if not result.items:
            raise HTTPException(404, "Database row not found")
        return result.items[0]
    config = get_table_config(table_name)
    if config is None:
        raise HTTPException(status_code=404, detail="Database table not found")
    row = await get_table_row(session, config=config, row_id=row_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Database row not found")
    return row
