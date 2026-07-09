import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator


class LiteratureRunCreate(BaseModel):
    query: str = Field(min_length=2, max_length=2000)
    year_from: int = Field(default=2018, ge=1900, le=2100)
    direction_count: int = Field(default=5, ge=1, le=10)
    candidates_per_direction: int = Field(default=25, ge=1, le=100)
    top_n_per_direction: int = Field(default=1, ge=1, le=5)
    include_fulltext: bool = True

    @model_validator(mode="after")
    def validate_selection_size(self) -> "LiteratureRunCreate":
        self.query = self.query.strip()
        if len(self.query) < 2:
            raise ValueError("query must contain at least two non-whitespace characters")
        if self.top_n_per_direction > self.candidates_per_direction:
            raise ValueError("top_n_per_direction cannot exceed candidates_per_direction")
        return self


class LiteratureRunRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    query: str
    status: str
    current_stage: str
    progress_current: int
    progress_total: int
    progress_message: str | None
    options_json: dict[str, Any]
    directions_json: list[dict[str, Any]]
    raw_search_results_json: dict[str, Any]
    picks_json: list[dict[str, Any]]
    analyses_json: list[dict[str, Any]]
    final_report_markdown: str | None
    error_message: str | None
    started_at: datetime | None
    finished_at: datetime | None
    created_at: datetime
    updated_at: datetime


class LiteratureRunEventRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    literature_run_id: uuid.UUID
    stage: str
    level: str
    event_type: str
    message: str
    counters_json: dict[str, Any]
    trace_json: dict[str, Any]
    created_at: datetime


class LiteraturePaperRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    ieee_article_number: str | None
    doi: str | None
    title: str
    authors_json: list[dict[str, Any]]
    abstract: str | None
    publication_title: str | None
    publication_year: int | None
    citation_count: int
    access_type: str | None
    document_url: str | None
    pdf_url: str | None
    raw_metadata_json: dict[str, Any]
    created_at: datetime
    updated_at: datetime


class LiteratureEvidenceRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    literature_run_paper_id: uuid.UUID
    matched_term: str
    match_level: str
    evidence_text: str
    evidence_source: str
    page_number: int | None
    section_name: str | None
    char_start: int | None
    char_end: int | None
    verified: bool
    created_at: datetime


class LiteratureArtifactRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    literature_run_id: uuid.UUID
    paper_id: uuid.UUID | None
    artifact_type: str
    mime_type: str | None
    byte_size: int | None
    sha256: str | None
    source_url: str | None
    created_at: datetime


class LiteratureRunPaperRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    literature_run_id: uuid.UUID
    paper_id: uuid.UUID
    direction_id: str
    direction_title: str | None
    search_query: str
    candidate_rank: int | None
    selected_rank: int | None
    score: int | None
    score_detail_json: dict[str, Any]
    selected: bool
    analysis_status: str
    abstract_zh: str | None
    match_how: str | None
    match_use: str | None
    conclusion: str | None
    analysis_json: dict[str, Any]
    created_at: datetime
    updated_at: datetime


class LiteratureResultItem(BaseModel):
    selection: LiteratureRunPaperRead
    paper: LiteraturePaperRead
    evidence: list[LiteratureEvidenceRead]
    artifacts: list[LiteratureArtifactRead]


class LiteratureRunResults(BaseModel):
    run: LiteratureRunRead
    items: list[LiteratureResultItem]
    run_artifacts: list[LiteratureArtifactRead]
