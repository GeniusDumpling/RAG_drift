"""Unified prediction and telemetry schemas for cross-framework KG extraction comparison."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

FORMAT_VERSION = "kg-comparison-v1"


class BaselineEntity(BaseModel):
    """Framework-agnostic extracted entity record for a single chunk/case."""

    model_config = ConfigDict(extra="allow")

    key: str = Field(min_length=1)
    name: str = Field(min_length=1)
    entity_type: str | None = None
    mentions: list[str] = Field(default_factory=list)
    surface_text: str | None = None
    identity_scope: str | None = None
    confidence: float | None = None
    attributes: dict[str, str] = Field(default_factory=dict)


class BaselineRelation(BaseModel):
    """Framework-agnostic extracted relation triple for a single chunk/case."""

    model_config = ConfigDict(extra="allow")

    subject: str = Field(min_length=1)
    predicate: str = Field(min_length=1)
    object: str = Field(min_length=1)
    assertion: str | None = None
    qualifiers: dict[str, str] = Field(default_factory=dict)
    quote: str = ""
    confidence: float | None = None


class CaseUsage(BaseModel):
    """Provider-reported token and HTTP call telemetry for one case or batch stage."""

    model_config = ConfigDict(extra="allow")

    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    cached_tokens: int = 0
    reasoning_tokens: int = 0
    http_calls: int = 0
    replayed_calls: int = 0
    unknown_usage_calls: int = 0
    by_stage: dict[str, dict[str, int]] = Field(default_factory=dict)


class CasePrediction(BaseModel):
    """Normalized per-case extraction result stored in comparison payloads."""

    model_config = ConfigDict(extra="allow")

    status: Literal["success", "error"]
    entities: list[BaselineEntity] = Field(default_factory=list)
    relations: list[BaselineRelation] = Field(default_factory=list)
    seconds: float = 0.0
    usage: CaseUsage = Field(default_factory=CaseUsage)
    error_type: str | None = None
    error_message: str | None = None
    raw_output: dict[str, Any] | None = None
    diagnostics: dict[str, Any] = Field(default_factory=dict)


class BaselineRunPayload(BaseModel):
    """Top-level serializable prediction file shared by all baselines and RAG_drift imports."""

    model_config = ConfigDict(extra="allow")

    format_version: str = FORMAT_VERSION
    method: str
    method_version: str
    mode: str = "open"
    granularity: str = "per_chunk"
    model: str | None = None
    temperature: float | None = 0.0
    input_scope: str = "title_and_current_chunk_only"
    dataset_version: str | None = None
    dataset_sha256: str | None = None
    corpus_usage: CaseUsage = Field(default_factory=CaseUsage)
    corpus_seconds: float = 0.0
    corpus_stats: dict[str, Any] = Field(default_factory=dict)
    results: dict[str, CasePrediction] = Field(default_factory=dict)
    updated_at: str | None = None
