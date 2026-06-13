import hashlib
import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.base import utcnow
from app.models.content import Author, ContentChunk, ContentItem, RawPage
from app.models.control import CrawlRun, SourceSite


def stable_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class CreatedContent:
    raw_page: RawPage
    content_item: ContentItem
    chunks: list[ContentChunk]


@dataclass(frozen=True)
class ContentListRecord:
    item: ContentItem
    fetched_at: datetime
    author_name: str | None


@dataclass(frozen=True)
class ContentDetailRecord:
    item: ContentItem
    raw_page: RawPage
    source: SourceSite
    crawl_run: CrawlRun
    chunks: list[ContentChunk]
    author_name: str | None


class ContentsRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def create_raw_page_and_item_for_test_ingest(
        self,
        *,
        source_site_id: uuid.UUID,
        crawl_run_id: uuid.UUID,
        requested_url: str,
        final_url: str | None,
        raw_html: str | None,
        raw_text: str | None,
        item_type: str,
        title: str | None,
        cleaned_text: str,
        summary_text: str | None,
        tags: list[str],
        extraction_confidence: float | None,
    ) -> CreatedContent:
        raw_body = (
            raw_html if raw_html is not None else raw_text if raw_text is not None else cleaned_text
        )
        body_hash = stable_hash(raw_body)
        content_hash = stable_hash(cleaned_text)
        canonical_url = final_url or requested_url
        dedup_key = stable_hash(f"{source_site_id}:{canonical_url}:{item_type}:{content_hash}")

        raw_page = RawPage(
            source_site_id=source_site_id,
            crawl_run_id=crawl_run_id,
            requested_url=requested_url,
            final_url=final_url,
            http_status=200,
            content_type="text/html" if raw_html is not None else "text/plain",
            response_headers_json={},
            raw_html=raw_html,
            raw_text=raw_text,
            raw_json={},
            fetched_at=utcnow(),
            fetch_error=None,
            parser_profile=None,
            extraction_method="test_ingest",
            extraction_confidence=extraction_confidence,
            parse_status="parsed",
            parse_error=None,
            body_hash=body_hash,
        )
        self.session.add(raw_page)
        await self.session.flush()

        content_item = ContentItem(
            source_site_id=source_site_id,
            raw_page_id=raw_page.id,
            crawl_run_id=crawl_run_id,
            author_id=None,
            parent_item_id=None,
            thread_root_id=None,
            item_type=item_type,
            title=title,
            canonical_url=canonical_url,
            source_url=requested_url,
            published_at=None,
            language=None,
            raw_text=raw_text if raw_text is not None else raw_html,
            cleaned_text=cleaned_text,
            summary_text=summary_text,
            structured_by="test_ingest",
            extraction_confidence=extraction_confidence,
            tags=tags,
            metadata_json={"body_hash": body_hash, "ingest_route": "test-ingest"},
            content_hash=content_hash,
            dedup_key=dedup_key,
            search_tsv=None,
        )
        self.session.add(content_item)
        await self.session.flush()

        chunk = ContentChunk(
            content_item_id=content_item.id,
            chunk_index=0,
            char_start=0,
            char_end=len(cleaned_text),
            display_text=cleaned_text,
            embed_text=cleaned_text,
            token_count=len(cleaned_text.split()),
            chunk_metadata_json={"source": "test-ingest"},
            qdrant_point_id=None,
            embed_status="pending",
            embed_error=None,
        )
        self.session.add(chunk)
        await self.session.flush()

        return CreatedContent(raw_page=raw_page, content_item=content_item, chunks=[chunk])

    async def list_contents(
        self,
        *,
        source_site_id: uuid.UUID | None,
        item_type: str | None,
        q: str | None,
        limit: int,
        offset: int,
    ) -> tuple[list[ContentListRecord], int]:
        where_clauses: list[Any] = []
        if source_site_id is not None:
            where_clauses.append(ContentItem.source_site_id == source_site_id)
        if item_type is not None:
            where_clauses.append(ContentItem.item_type == item_type)
        if q is not None and q.strip():
            pattern = f"%{q.strip()}%"
            where_clauses.append(
                or_(
                    ContentItem.title.ilike(pattern),
                    ContentItem.cleaned_text.ilike(pattern),
                    ContentItem.summary_text.ilike(pattern),
                )
            )

        total = await self.session.scalar(
            select(func.count(ContentItem.id)).where(*where_clauses)
        )
        result = await self.session.execute(
            select(ContentItem, RawPage.fetched_at, Author.display_name)
            .join(RawPage, ContentItem.raw_page_id == RawPage.id)
            .outerjoin(Author, ContentItem.author_id == Author.id)
            .where(*where_clauses)
            .order_by(RawPage.fetched_at.desc(), ContentItem.created_at.desc())
            .offset(offset)
            .limit(limit)
        )
        records = [
            ContentListRecord(item=row[0], fetched_at=row[1], author_name=row[2])
            for row in result.all()
        ]
        return records, int(total or 0)

    async def get_content_detail(self, content_item_id: uuid.UUID) -> ContentDetailRecord | None:
        result = await self.session.execute(
            select(ContentItem, RawPage, SourceSite, CrawlRun, Author.display_name)
            .join(RawPage, ContentItem.raw_page_id == RawPage.id)
            .join(SourceSite, ContentItem.source_site_id == SourceSite.id)
            .join(CrawlRun, ContentItem.crawl_run_id == CrawlRun.id)
            .outerjoin(Author, ContentItem.author_id == Author.id)
            .where(ContentItem.id == content_item_id)
        )
        row = result.one_or_none()
        if row is None:
            return None

        chunks_result = await self.session.scalars(
            select(ContentChunk)
            .where(ContentChunk.content_item_id == content_item_id)
            .order_by(ContentChunk.chunk_index.asc())
        )
        chunks = list(chunks_result)

        return ContentDetailRecord(
            item=row[0],
            raw_page=row[1],
            source=row[2],
            crawl_run=row[3],
            author_name=row[4],
            chunks=chunks,
        )
