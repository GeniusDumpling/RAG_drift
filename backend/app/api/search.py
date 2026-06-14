import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_session
from app.repositories.search import SearchRepository
from app.schemas.search import (
    AnswerRequest,
    AnswerResponse,
    SearchQueryRead,
    SearchRequest,
    SearchResponse,
)
from app.services.search import SearchService, UnsupportedSearchModeError

router = APIRouter()
SessionDep = Annotated[AsyncSession, Depends(get_session)]


@router.post("/search", response_model=SearchResponse, status_code=status.HTTP_200_OK)
async def search(payload: SearchRequest, session: SessionDep) -> SearchResponse:
    try:
        return await SearchService(session).search(payload)
    except UnsupportedSearchModeError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


@router.post("/answer", response_model=AnswerResponse, status_code=status.HTTP_200_OK)
async def answer(payload: AnswerRequest, session: SessionDep) -> AnswerResponse:
    return await SearchService(session).answer(payload)


@router.get("/search-queries/{search_query_id}", response_model=SearchQueryRead)
async def get_search_query(search_query_id: uuid.UUID, session: SessionDep) -> SearchQueryRead:
    search_query = await SearchRepository(session).get_search_query(search_query_id)
    if search_query is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Search query not found",
        )
    return SearchQueryRead.model_validate(search_query)
