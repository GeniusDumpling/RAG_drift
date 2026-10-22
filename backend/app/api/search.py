import json
import logging
import uuid
from collections.abc import AsyncIterator
from contextlib import aclosing
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.api.deps import get_session, get_session_factory
from app.repositories.chat import ChatRepository, ChatSessionBusy, ChatSessionNotFound
from app.repositories.search import SearchRepository
from app.schemas.search import (
    AnswerRequest,
    AnswerResponse,
    ChatSessionDetail,
    ChatSessionRead,
    SearchQueryRead,
    SearchRequest,
    SearchResponse,
)
from app.services.search import SearchService, UnsupportedSearchModeError

router = APIRouter()
SessionDep = Annotated[AsyncSession, Depends(get_session)]
SessionFactoryDep = Annotated[async_sessionmaker[AsyncSession], Depends(get_session_factory)]
logger = logging.getLogger(__name__)


@router.post("/search", response_model=SearchResponse, status_code=status.HTTP_200_OK)
async def search(payload: SearchRequest, session: SessionDep) -> SearchResponse:
    try:
        return await SearchService(session).search(payload)
    except UnsupportedSearchModeError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


@router.post("/answer", response_model=AnswerResponse, status_code=status.HTTP_200_OK)
async def answer(payload: AnswerRequest, session: SessionDep) -> AnswerResponse:
    try:
        return await SearchService(session).answer(payload)
    except ChatSessionNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ChatSessionBusy as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/answer/stream")
async def stream_answer(payload: AnswerRequest, factory: SessionFactoryDep) -> StreamingResponse:
    async def events() -> AsyncIterator[str]:
        try:
            async with factory() as session:
                async with aclosing(SearchService(session).stream_answer(payload)) as stream:
                    async for event in stream:
                        yield (
                            f"event: {event['event']}\n"
                            f"data: {json.dumps(event['data'], ensure_ascii=False)}\n\n"
                        )
        except (ChatSessionNotFound, ChatSessionBusy) as exc:
            yield f"event: error\ndata: {json.dumps({'message': str(exc)})}\n\n"
        except Exception:
            logger.exception("Answer stream failed")
            yield 'event: error\ndata: {"message": "问答处理失败，请稍后重试"}\n\n'

    return StreamingResponse(
        events(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


@router.post("/chat-sessions", response_model=ChatSessionRead, status_code=201)
async def create_chat_session(session: SessionDep) -> ChatSessionRead:
    return await ChatRepository(session).create_session()


@router.get("/chat-sessions", response_model=list[ChatSessionRead])
async def list_chat_sessions(
    session: SessionDep,
    limit: int = Query(default=50, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
) -> list[ChatSessionRead]:
    return await ChatRepository(session).list_sessions(limit, offset)


@router.get("/chat-sessions/{session_id}", response_model=ChatSessionDetail)
async def get_chat_session(session_id: uuid.UUID, session: SessionDep) -> ChatSessionDetail:
    try:
        return await ChatRepository(session).detail(session_id)
    except ChatSessionNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/search-queries/{search_query_id}", response_model=SearchQueryRead)
async def get_search_query(search_query_id: uuid.UUID, session: SessionDep) -> SearchQueryRead:
    search_query = await SearchRepository(session).get_search_query(search_query_id)
    if search_query is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Search query not found",
        )
    return SearchQueryRead.model_validate(search_query)
