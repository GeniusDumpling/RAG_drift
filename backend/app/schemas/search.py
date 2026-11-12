import re
import uuid
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.schemas.common import OrmBaseModel
from app.schemas.video_graph import GraphEvidenceRead


class GraphAnswerEvidence(GraphEvidenceRead):
    subject: str
    predicate: str
    object: str


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
    session_id: uuid.UUID | None = None

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


class ImageEvidence(BaseModel):
    asset_id: str
    source_guid: str
    url: str
    source_url: str
    guide_url: str
    step_order: int | None = None
    step_title: str | None = None
    caption: str
    width: int
    height: int
    observation: str | None = None
    ocr_text: list[str] = Field(default_factory=list)
    description_generated: bool = False
    citation_index: int | None = None


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
    rerank_score: float | None = None
    matched_by: Literal["vector", "keyword", "hybrid", "graph"]
    graph_evidence: list[GraphAnswerEvidence] = Field(default_factory=list)
    thread_summary: str | None
    video_url: str | None = None
    description_text: str | None = None
    images: list[ImageEvidence] = Field(default_factory=list)


class SearchResponse(BaseModel):
    query: SearchQueryRead
    evidence: list[EvidenceObject]


class GenerationInfo(BaseModel):
    response_mode: Literal["knowledge", "conversation"] = "knowledge"
    conversation_intent: str | None = None
    history_used: bool = False
    status: Literal["generated", "fallback", "no_evidence", "cancelled", "error"] = "generated"
    model: str | None = None
    reason: str | None = None
    latency_ms: int = 0
    total_latency_ms: int = 0
    first_token_ms: int | None = None
    timings: dict[str, int] = Field(default_factory=dict)
    evidence_count: int = 0
    context_chars: int = 0
    context_chunk_ids: list[uuid.UUID] = Field(default_factory=list)
    retrieval_query: str | None = None
    rewrite_status: str = "not_needed"
    reranking: dict[str, Any] = Field(default_factory=dict)
    graph_retrieval: dict[str, Any] = Field(default_factory=dict)
    citation_check: dict[str, Any] = Field(default_factory=dict)


class AnswerResponse(BaseModel):
    query: SearchQueryRead
    answer: str
    supporting_evidence: list[EvidenceObject]
    generation: GenerationInfo = Field(default_factory=GenerationInfo)
    session_id: uuid.UUID | None = None
    turn_id: uuid.UUID | None = None
    images: list[ImageEvidence] = Field(default_factory=list)

    @model_validator(mode="after")
    def attach_cited_images(self) -> "AnswerResponse":
        cited = {int(value) for value in re.findall(r"\[(\d+)\]", self.answer)}
        images, seen = [], set()
        for index, evidence in enumerate(self.supporting_evidence, 1):
            if index not in cited:
                continue
            for image in evidence.images:
                if image.asset_id not in seen:
                    images.append(image.model_copy(update={"citation_index": index}))
                    seen.add(image.asset_id)
        self.images = images
        return self


class ChatSessionRead(OrmBaseModel):
    id: uuid.UUID
    title: str
    created_at: datetime
    updated_at: datetime


class ChatTurnRead(OrmBaseModel):
    id: uuid.UUID
    session_id: uuid.UUID
    query: str
    answer: str
    status: str
    supporting_evidence: list[EvidenceObject]
    generation: GenerationInfo | None = None
    created_at: datetime


class ChatSessionDetail(BaseModel):
    session: ChatSessionRead
    turns: list[ChatTurnRead]
