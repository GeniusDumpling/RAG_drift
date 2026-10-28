from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class SupplierRelationRead(BaseModel):
    """阶段1 明确供应关系（用于展示）。"""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    supplier_name: str
    supply_content: str | None
    buyer_name: str | None
    credibility: str
    seen_count: int
    source_urls: list[str]
    drone_models: list[str] = Field(default_factory=list)
    evidence_years: list[int] = Field(default_factory=list)
    evidence_items: list[dict] = Field(default_factory=list)
    verify_status: str
    latest_verification_confidence: str | None = None
    created_at: datetime
    updated_at: datetime


class SupplierVerificationRead(BaseModel):
    """阶段2 定向验证（用于展示）。"""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    relation_id: uuid.UUID
    supplier_name: str
    verdict: str
    confidence: str | None
    supply_content: str | None
    evidence_md: str | None
    evidence_urls: list[str]
    orig_source_urls: list[str]
    verify_time: datetime | None
    drone_models: list[str] = Field(default_factory=list)
    evidence_years: list[int] = Field(default_factory=list)
    execution_status: str = "success"
    created_at: datetime
    updated_at: datetime


class SupplierOverview(BaseModel):
    """供应链概览统计。"""

    total_suppliers: int
    confirmed_relations: int
    verified: int
    unverified: int
    verify_failed: int


class SupplierBrandRead(BaseModel):
    buyer_name: str
    total_suppliers: int
