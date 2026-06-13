import uuid
from datetime import datetime
from typing import Any, Generic, TypeVar

from pydantic import BaseModel, ConfigDict, Field

T = TypeVar("T")


class Page(BaseModel, Generic[T]):
    items: list[T]
    total: int
    limit: int
    offset: int


class SourceSiteCreate(BaseModel):
    name: str
    site_type: str
    base_url: str
    allowed_domains: list[str] = Field(default_factory=list)
    fetch_mode: str
    default_language: str | None = None
    active: bool = True
    config_json: dict[str, Any] = Field(default_factory=dict)


class SourceSiteUpdate(BaseModel):
    name: str | None = None
    site_type: str | None = None
    base_url: str | None = None
    allowed_domains: list[str] | None = None
    fetch_mode: str | None = None
    default_language: str | None = None
    active: bool | None = None
    config_json: dict[str, Any] | None = None


class SourceSiteRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    site_type: str
    base_url: str
    allowed_domains: list[str]
    fetch_mode: str
    default_language: str | None
    active: bool
    config_json: dict[str, Any]
    created_at: datetime
    updated_at: datetime


class CrawlJobCreate(BaseModel):
    source_site_id: uuid.UUID
    name: str
    trigger_mode: str
    cron_expr: str | None = None
    seed_config_json: dict[str, Any] = Field(default_factory=dict)
    parser_profile: str
    max_pages: int = Field(default=20, gt=0)
    enabled: bool = True
    agent_policy_json: dict[str, Any] = Field(default_factory=dict)


class CrawlJobUpdate(BaseModel):
    source_site_id: uuid.UUID | None = None
    name: str | None = None
    trigger_mode: str | None = None
    cron_expr: str | None = None
    seed_config_json: dict[str, Any] | None = None
    parser_profile: str | None = None
    max_pages: int | None = Field(default=None, gt=0)
    enabled: bool | None = None
    agent_policy_json: dict[str, Any] | None = None


class CrawlJobRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    source_site_id: uuid.UUID
    name: str
    trigger_mode: str
    cron_expr: str | None
    seed_config_json: dict[str, Any]
    parser_profile: str
    max_pages: int
    enabled: bool
    agent_policy_json: dict[str, Any]
    last_run_at: datetime | None
    next_run_at: datetime | None
    created_at: datetime
    updated_at: datetime


class CrawlRunTrigger(BaseModel):
    seed_url: str | None = None


class CrawlRunRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    source_site_id: uuid.UUID
    crawl_job_id: uuid.UUID
    trigger_type: str
    execution_mode: str
    seed_url: str | None
    status: str
    started_at: datetime | None
    finished_at: datetime | None
    discovered_count: int
    fetched_count: int
    parsed_count: int
    extracted_count: int
    deduped_count: int
    chunked_count: int
    embedded_count: int
    error_count: int
    config_snapshot_json: dict[str, Any]
    error_message: str | None
    created_at: datetime


class CrawlRunEventRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    crawl_run_id: uuid.UUID
    stage: str
    level: str
    event_type: str
    message: str
    related_url: str | None
    related_raw_page_id: uuid.UUID | None
    related_content_item_id: uuid.UUID | None
    counters_json: dict[str, Any]
    agent_trace_json: dict[str, Any]
    created_at: datetime
