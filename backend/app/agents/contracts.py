from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, field_validator


class ExtractionAgentRequest(BaseModel):
    raw_page_id: UUID
    source_site_id: UUID
    crawl_run_id: UUID
    requested_url: str
    final_url: str | None
    site_type: str
    parser_profile: str
    content_type: str | None
    raw_html: str | None
    raw_markdown: str | None
    raw_json: dict[str, Any] | None
    context_json: dict[str, Any]
    response_schema_version: str = "extraction.v1"


class ExtractionItem(BaseModel):
    item_type: str
    external_item_id: str | None
    title: str | None
    author: dict[str, Any] | None
    published_at: datetime | None
    body_text: str
    summary_text: str | None
    tags: list[str]
    parent_ref: str | None
    thread_root_ref: str | None
    metadata_json: dict[str, Any]

    @field_validator("body_text")
    @classmethod
    def body_text_must_not_be_blank(cls, value: str) -> str:
        if len(value.strip()) < 1:
            raise ValueError("body_text must not be blank")
        return value


class ExtractionAgentResponse(BaseModel):
    page_kind: str
    items: list[ExtractionItem]
    extraction_confidence: float
    warnings: list[str]
    trace_summary_json: dict[str, Any]

    @field_validator("items")
    @classmethod
    def items_must_not_be_empty(cls, value: list[ExtractionItem]) -> list[ExtractionItem]:
        if len(value) < 1:
            raise ValueError("items must contain at least one item")
        return value

    @field_validator("extraction_confidence")
    @classmethod
    def extraction_confidence_must_be_unit_interval(cls, value: float) -> float:
        if not 0.0 <= value <= 1.0:
            raise ValueError("extraction_confidence must be between 0.0 and 1.0")
        return value


class QueryOptimizationRequest(BaseModel):
    raw_query: str
    filters: dict[str, Any]
    mode: str
    response_schema_version: str = "query_optimization.v1"


class QueryOptimizationResponse(BaseModel):
    optimized_query_text: str
    keyword_terms: list[str]
    entity_hints: list[str]
    time_hints_json: dict[str, Any]
    query_intent: str
    confidence: float
    trace_summary_json: dict[str, Any]

    @field_validator("optimized_query_text")
    @classmethod
    def optimized_query_text_must_not_be_blank(cls, value: str) -> str:
        if len(value.strip()) < 1:
            raise ValueError("optimized_query_text must not be blank")
        return value

    @field_validator("confidence")
    @classmethod
    def confidence_must_be_unit_interval(cls, value: float) -> float:
        if not 0.0 <= value <= 1.0:
            raise ValueError("confidence must be between 0.0 and 1.0")
        return value
