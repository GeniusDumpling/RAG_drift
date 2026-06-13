import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_session
from app.core.config import get_settings
from app.models.control import CrawlRun, SourceSite
from app.repositories.contents import ContentDetailRecord, ContentListRecord, ContentsRepository
from app.schemas.contents import (
    ContentChunkRead,
    ContentDetail,
    ContentListItem,
    ContentTestIngestCreate,
    ContentTestIngestResult,
    RawPageRead,
)
from app.schemas.sources import CrawlRunRead, Page, SourceSiteRead

router = APIRouter()
SessionDep = Annotated[AsyncSession, Depends(get_session)]
LimitQuery = Annotated[int, Query(ge=1, le=100)]
OffsetQuery = Annotated[int, Query(ge=0)]


def _content_list_item(record: ContentListRecord) -> ContentListItem:
    return ContentListItem(
        id=record.item.id,
        source_site_id=record.item.source_site_id,
        item_type=record.item.item_type,
        canonical_url=record.item.canonical_url,
        title=record.item.title,
        author_name=record.author_name,
        published_at=record.item.published_at,
        fetched_at=record.fetched_at,
        summary_text=record.item.summary_text,
        tags=record.item.tags,
        extraction_confidence=record.item.extraction_confidence,
    )


def _content_detail(record: ContentDetailRecord) -> ContentDetail:
    list_item = _content_list_item(
        ContentListRecord(
            item=record.item,
            fetched_at=record.raw_page.fetched_at,
            author_name=record.author_name,
        )
    )
    return ContentDetail(
        **list_item.model_dump(),
        raw_page=RawPageRead.model_validate(record.raw_page),
        source=SourceSiteRead.model_validate(record.source),
        crawl_run=CrawlRunRead.model_validate(record.crawl_run),
        chunks=[ContentChunkRead.model_validate(chunk) for chunk in record.chunks],
    )


@router.get("/contents", response_model=Page[ContentListItem])
async def list_contents(
    session: SessionDep,
    source_site_id: uuid.UUID | None = None,
    item_type: str | None = None,
    q: str | None = None,
    limit: LimitQuery = 50,
    offset: OffsetQuery = 0,
) -> Page[ContentListItem]:
    repo = ContentsRepository(session)
    items, total = await repo.list_contents(
        source_site_id=source_site_id,
        item_type=item_type,
        q=q,
        limit=limit,
        offset=offset,
    )
    return Page[ContentListItem](
        items=[_content_list_item(item) for item in items],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.post(
    "/contents/test-ingest",
    response_model=ContentTestIngestResult,
    status_code=status.HTTP_201_CREATED,
)
async def test_ingest_content(
    payload: ContentTestIngestCreate, session: SessionDep
) -> ContentTestIngestResult:
    if get_settings().app_env != "dev":
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")

    source = await session.get(SourceSite, payload.source_site_id)
    if source is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Source not found")

    crawl_run = await session.get(CrawlRun, payload.crawl_run_id)
    if crawl_run is None or crawl_run.source_site_id != payload.source_site_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Run not found")

    repo = ContentsRepository(session)
    created = await repo.create_raw_page_and_item_for_test_ingest(
        source_site_id=payload.source_site_id,
        crawl_run_id=payload.crawl_run_id,
        requested_url=payload.requested_url,
        final_url=payload.final_url,
        raw_html=payload.raw_html,
        raw_text=payload.raw_text,
        item_type=payload.item_type,
        title=payload.title,
        cleaned_text=payload.cleaned_text,
        summary_text=payload.summary_text,
        tags=payload.tags,
        extraction_confidence=payload.extraction_confidence,
    )
    await session.commit()

    return ContentTestIngestResult(
        raw_page_id=created.raw_page.id,
        content_item_id=created.content_item.id,
        chunk_ids=[chunk.id for chunk in created.chunks],
    )


@router.get("/contents/{content_item_id}", response_model=ContentDetail)
async def get_content(content_item_id: uuid.UUID, session: SessionDep) -> ContentDetail:
    repo = ContentsRepository(session)
    record = await repo.get_content_detail(content_item_id)
    if record is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Content not found")
    return _content_detail(record)
