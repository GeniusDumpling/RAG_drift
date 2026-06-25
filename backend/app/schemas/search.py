import uuid
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.schemas.common import OrmBaseModel


class SearchFilters(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_site_id: uuid.UUID | None = None
    item_type: str | None = None
    language: str | None = None
    tags: list[str] = Field(default_factory=list)
    published_after: datetime | None = None
    published_before: datetime | None = None


class SearchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    query: str
    mode: Literal["search"] = "search"
    filters: SearchFilters = Field(default_factory=SearchFilters)
    top_k: int = Field(default=10, ge=1, le=50)

    @field_validator("query")
    @classmethod
    def query_must_not_be_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("query must not be blank")
        return value


class AnswerRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    query: str
    mode: Literal["answer"] = "answer"
    filters: SearchFilters = Field(default_factory=SearchFilters)
    top_k: int = Field(default=10, ge=1, le=50)

    @field_validator("query")
    @classmethod
    def query_must_not_be_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("query must not be blank")
        return value


class SearchQueryRead(OrmBaseModel):
    id: uuid.UUID
    raw_query: str
    filter_json: dict[str, Any]
    mode: str
    top_k: int
    used_agent: bool
    optimized_query_text: str | None
    keyword_terms_json: list[str]
    entity_hints_json: list[str]
    time_hints_json: dict[str, Any]
    result_summary_json: dict[str, Any]
    query_trace_json: dict[str, Any]
    result_count: int | None
    created_at: datetime


class EvidenceObject(BaseModel):
    chunk_id: uuid.UUID
    content_item_id: uuid.UUID
    raw_page_id: uuid.UUID
    source_site_id: uuid.UUID
    title: str | None
    snippet: str
    canonical_url: str
    source_site_name: str
    author_name: str | None
    published_at: datetime | None
    item_type: str
    score: float
    vector_score: float | None = None
    keyword_score: float | None = None
    matched_by: Literal["vector", "keyword", "hybrid"]
    thread_summary: str | None
    video_url: str | None = None
    description_text: str | None = None


class SearchResponse(BaseModel):
    query: SearchQueryRead
    evidence: list[EvidenceObject]


class AnswerResponse(BaseModel):
    query: SearchQueryRead
    answer: str
    supporting_evidence: list[EvidenceObject]
