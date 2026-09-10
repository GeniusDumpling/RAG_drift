"""供应商情报：阶段1 明确供应关系 + 阶段2 定向验证。"""

import uuid
from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin, UuidPrimaryKeyMixin


class SupplierRelation(Base, UuidPrimaryKeyMixin, TimestampMixin):
    """阶段1 LLM 抽取的明确供应关系（按供应商聚合）。

    confirmed_relations.md 中「采购方=大疆 且 可信度=明确」的明细。
    """

    __tablename__ = "supplier_relations"
    __table_args__ = (
        CheckConstraint("credibility IN ('明确','疑似')", name="credibility_valid"),
        CheckConstraint(
            "verify_status IN ('未验证','已验证','验证失败')",
            name="verify_status_valid",
        ),
        CheckConstraint("seen_count >= 0", name="seen_count_non_negative"),
        UniqueConstraint("buyer_name", "supplier_name"),
        Index("ix_supplier_relations_supplier_name", "supplier_name"),
        Index("ix_supplier_relations_verify_status", "verify_status"),
        Index("ix_supplier_relations_buyer_name", "buyer_name"),
    )

    supplier_name: Mapped[str] = mapped_column(String(255), nullable=False)
    supply_content: Mapped[str | None] = mapped_column(Text)
    buyer_name: Mapped[str] = mapped_column(
        String(120), nullable=False, default="大疆", server_default=text("'大疆'")
    )
    credibility: Mapped[str] = mapped_column(
        String(20), nullable=False, default="明确", server_default=text("'明确'")
    )
    seen_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=1, server_default=text("1")
    )
    source_urls: Mapped[list[str]] = mapped_column(
        JSONB, default=list, server_default=text("'[]'::jsonb"), nullable=False
    )
    verify_status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="未验证", server_default=text("'未验证'")
    )

    verifications: Mapped[list["SupplierVerification"]] = relationship(
        back_populates="relation"
    )


class SupplierVerification(Base, UuidPrimaryKeyMixin, TimestampMixin):
    """阶段2 定向验证结果（supplier_verify.py）的单次判定。"""

    __tablename__ = "supplier_verifications"
    __table_args__ = (
        CheckConstraint("verdict IN ('确认','否定','待确认')", name="verdict_valid"),
        CheckConstraint(
            "confidence IS NULL OR confidence IN ('明确','疑似','不相关')",
            name="confidence_valid",
        ),
        Index("ix_supplier_verifications_relation_id", "relation_id"),
        Index("ix_supplier_verifications_supplier_name", "supplier_name"),
        Index("ix_supplier_verifications_verdict", "verdict"),
        Index("ix_supplier_verifications_verify_time", "verify_time"),
    )

    relation_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("supplier_relations.id", ondelete="RESTRICT"), nullable=False
    )
    supplier_name: Mapped[str] = mapped_column(String(255), nullable=False)
    verdict: Mapped[str] = mapped_column(String(20), nullable=False)
    confidence: Mapped[str | None] = mapped_column(String(20))
    supply_content: Mapped[str | None] = mapped_column(Text)
    evidence_md: Mapped[str | None] = mapped_column(Text)
    evidence_urls: Mapped[list[str]] = mapped_column(
        JSONB, default=list, server_default=text("'[]'::jsonb"), nullable=False
    )
    orig_source_urls: Mapped[list[str]] = mapped_column(
        JSONB, default=list, server_default=text("'[]'::jsonb"), nullable=False
    )
    verify_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    relation: Mapped[SupplierRelation | None] = relationship(back_populates="verifications")