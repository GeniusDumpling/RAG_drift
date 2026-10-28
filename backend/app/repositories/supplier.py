from __future__ import annotations

from uuid import UUID

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.supplier import SupplierRelation, SupplierVerification
from app.schemas.sources import Page
from app.schemas.supplier import (
    SupplierBrandRead,
    SupplierOverview,
    SupplierRelationRead,
    SupplierVerificationRead,
)


async def list_supplier_brands(session: AsyncSession) -> list[SupplierBrandRead]:
    statement = (
        select(SupplierRelation.buyer_name, func.count())
        .where(SupplierRelation.buyer_name.is_not(None), SupplierRelation.buyer_name != "")
        .group_by(SupplierRelation.buyer_name)
        .order_by(SupplierRelation.buyer_name)
    )
    return [
        SupplierBrandRead(buyer_name=name, total_suppliers=count)
        for name, count in await session.execute(statement)
    ]


async def get_supplier_overview(
    session: AsyncSession, *, buyer_name: str | None = None
) -> SupplierOverview:
    """Count only relationships in the selected brand, or all brands when omitted."""
    statement = select(
        SupplierRelation.credibility, SupplierRelation.verify_status, func.count()
    ).group_by(SupplierRelation.credibility, SupplierRelation.verify_status)
    if buyer_name:
        statement = statement.where(SupplierRelation.buyer_name == buyer_name)
    total = confirmed = 0
    status_counts: dict[str, int] = {}
    for credibility, status, count in await session.execute(statement):
        total += count
        if credibility == "明确":
            confirmed += count
        status_counts[status] = status_counts.get(status, 0) + count
    return SupplierOverview(
        total_suppliers=total,
        confirmed_relations=confirmed,
        verified=status_counts.get("已验证", 0),
        unverified=status_counts.get("未验证", 0),
        verify_failed=status_counts.get("验证失败", 0),
    )


async def list_supplier_relations(
    session: AsyncSession,
    *,
    limit: int,
    offset: int,
    q: str | None,
    credibility: str | None,
    verify_status: str | None,
    buyer_name: str | None = None,
) -> Page[SupplierRelationRead]:
    """分页列出供应关系，支持关键词与状态/可信度过滤。"""
    latest_confidence = (
        select(SupplierVerification.confidence)
        .where(SupplierVerification.relation_id == SupplierRelation.id)
        .order_by(
            SupplierVerification.verify_time.desc().nulls_last(),
            SupplierVerification.created_at.desc(),
        )
        .limit(1)
        .scalar_subquery()
        .label("latest_verification_confidence")
    )
    statement = select(SupplierRelation, latest_confidence)
    count_statement = select(func.count()).select_from(SupplierRelation)

    if buyer_name:
        statement = statement.where(SupplierRelation.buyer_name == buyer_name)
        count_statement = count_statement.where(SupplierRelation.buyer_name == buyer_name)

    if q:
        like = f"%{q}%"
        condition = or_(
            SupplierRelation.supplier_name.ilike(like),
            SupplierRelation.supply_content.ilike(like),
            SupplierRelation.buyer_name.ilike(like),
        )
        statement = statement.where(condition)
        count_statement = count_statement.where(condition)
    if credibility:
        statement = statement.where(SupplierRelation.credibility == credibility)
        count_statement = count_statement.where(SupplierRelation.credibility == credibility)
    if verify_status:
        statement = statement.where(SupplierRelation.verify_status == verify_status)
        count_statement = count_statement.where(SupplierRelation.verify_status == verify_status)

    total = int((await session.scalar(count_statement)) or 0)
    rows = (
        await session.execute(
            statement.order_by(SupplierRelation.updated_at.desc()).offset(offset).limit(limit)
        )
    ).all()
    return Page[SupplierRelationRead](
        items=[
            SupplierRelationRead.model_validate(relation).model_copy(
                update={"latest_verification_confidence": confidence}
            )
            for relation, confidence in rows
        ],
        total=total,
        limit=limit,
        offset=offset,
    )


async def list_supplier_verifications(
    session: AsyncSession, *, relation_id: UUID
) -> list[SupplierVerificationRead]:
    """某个供应关系的全部验证记录（按验证时间倒序）。"""
    statement = (
        select(SupplierVerification)
        .where(SupplierVerification.relation_id == relation_id)
        .order_by(
            SupplierVerification.verify_time.desc().nulls_last(),
            SupplierVerification.created_at.desc(),
        )
    )
    rows = (await session.scalars(statement)).all()
    return [SupplierVerificationRead.model_validate(row) for row in rows]
