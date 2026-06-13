import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.schemas.sources import CrawlRunRead, SourceSiteRead


class ContentListItem(BaseModel):
    id: uuid.UUID
    source_site_id: uuid.UUID
    item_type: str
    canonical_url: str
    title: str | None
    author_name: str | None
    published_at: datetime | None
    fetched_at: datetime
    summary_text: str | None
    tags: list[str]
    extraction_confidence: float | None


class RawPageRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    source_site_id: uuid.UUID
    crawl_run_id: uuid.UUID
    requested_url: str
    final_url: str | None
    http_status: int | None
    content_type: str | None
    response_headers_json: dict[str, Any]
    raw_html: str | None
    raw_text: str | None
    raw_json: dict[str, Any]
    fetched_at: datetime
    fetch_error: str | None
    parser_profile: str | None
    extraction_method: str | None
    extraction_confidence: float | None
    parse_status: str
    parse_error: str | None
    body_hash: str | None
    created_at: datetime


class ContentChunkRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    content_item_id: uuid.UUID
    chunk_index: int
    char_start: int | None
    char_end: int | None
    display_text: str
    embed_text: str
    token_count: int | None
    chunk_metadata_json: dict[str, Any]
    qdrant_point_id: str | None
    embed_status: str
    embed_error: str | None
    created_at: datetime
    updated_at: datetime


class ContentDetail(ContentListItem):
    raw_page: RawPageRead
    source: SourceSiteRead
    crawl_run: CrawlRunRead
    chunks: list[ContentChunkRead]


class ContentTestIngestCreate(BaseModel):
    source_site_id: uuid.UUID
    crawl_run_id: uuid.UUID
    requested_url: str
    final_url: str | None = None
    raw_html: str = Field(min_length=1)
    raw_text: str | None = None
    item_type: str
    title: str | None = None
    cleaned_text: str
    summary_text: str | None = None
    tags: list[str] = Field(default_factory=list)
    extraction_confidence: float | None = Field(default=1.0, ge=0, le=1)

    @field_validator("raw_html")
    @classmethod
    def raw_html_must_not_be_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("raw_html must not be empty")
        return value


class ContentTestIngestResult(BaseModel):
    raw_page_id: uuid.UUID
    content_item_id: uuid.UUID
    chunk_ids: list[uuid.UUID]
