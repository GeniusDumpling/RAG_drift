"""把 supplier-information 的分析/验证结果回写进 PostgreSQL 的两张业务表。

- supplier_relations     阶段1 明确供应关系（按供应商 upsert）
- supplier_verifications 阶段2 定向验证（每次追加一条，并回写表1 verify_status）

表结构与 backend/app/models/supplier.py 对应（不 import backend，保持 tools 独立）。
连接串从环境变量读取：DATABASE_URL 或 SYNC_DATABASE_URL，两者都没有则跳过（非致命）。
"""
import logging
import os
import re
import uuid
from datetime import datetime

logger = logging.getLogger(__name__)

try:
    from sqlalchemy import MetaData, Table, Column, select
    from sqlalchemy import Integer, String, Text, DateTime, ForeignKey
    from sqlalchemy.dialects.postgresql import JSONB, UUID
    from sqlalchemy.orm import Session
    from sqlalchemy import create_engine
    HAS_DB = True
except Exception as e:  # pragma: no cover
    logger.warning("未安装 sqlalchemy/psycopg，跳过数据库回写：%s", e)
    HAS_DB = False


def get_database_url() -> str:
    return os.environ.get("DATABASE_URL") or os.environ.get("SYNC_DATABASE_URL") or ""


def _engine():
    url = get_database_url()
    if not url:
        return None
    return create_engine(url)


def _tables():
    md = MetaData()
    supplier_relations = Table(
        "supplier_relations", md,
        Column("id", UUID(as_uuid=True), primary_key=True),
        Column("supplier_name", String(255), nullable=False),
        Column("supply_content", Text),
        Column("buyer_name", String(120)),
        Column("credibility", String(20)),
        Column("seen_count", Integer),
        Column("source_urls", JSONB),
        Column("verify_status", String(20)),
    )
    supplier_verifications = Table(
        "supplier_verifications", md,
        Column("id", UUID(as_uuid=True), primary_key=True),
        Column("relation_id", UUID(as_uuid=True), ForeignKey("supplier_relations.id")),
        Column("supplier_name", String(255), nullable=False),
        Column("verdict", String(20)),
        Column("confidence", String(20)),
        Column("supply_content", Text),
        Column("evidence_md", Text),
        Column("evidence_urls", JSONB),
        Column("orig_source_urls", JSONB),
        Column("verify_time", DateTime(timezone=True)),
    )
    return supplier_relations, supplier_verifications


def _extract_urls(text: str) -> list:
    if not text:
        return []
    urls = re.findall(r"https?://[^\s)\]}]+", text)
    seen = []
    for u in urls:
        if u not in seen:
            seen.append(u)
    return seen


def find_relation_id(session, table, supplier_name: str):
    """按供应商名在 supplier_relations 中定位 id，找不到返回 None。"""
    row = session.execute(
        select(table.c.id).where(table.c.supplier_name == supplier_name).limit(1)
    ).first()
    return row[0] if row else None


def upsert_supplier_relations(confirmed_sec: dict) -> tuple:
    """阶段1 明确关系写库（按 supplier_name 只增/更新）。

    confirmed_sec 形态来自 llm_supplier_analysis.load_confirmed_relations()：
    {supplier: {supply, first,last,count, sources, verify_status, verify_time, verify_verdict}}
    返回 (新增数, 更新数)。
    """
    if not HAS_DB or not confirmed_sec:
        return (0, 0)
    url = get_database_url()
    if not url:
        logger.warning("[db_ingest] 未设置 DATABASE_URL/SYNC_DATABASE_URL，跳过表1回写")
        return (0, 0)
    engine = _engine()
    relations, _ = _tables()
    inserted = updated = 0
    with Session(engine) as session:
        for name, rec in confirmed_sec.items():
            row = find_relation_id(session, relations, name)
            if row is None:
                session.execute(relations.insert().values(
                    id=uuid.uuid4(),
                    supplier_name=name,
                    supply_content=rec.get("supply") or None,
                    buyer_name="大疆",
                    credibility=rec.get("credibility") or "明确",
                    seen_count=int(rec.get("count") or 1),
                    source_urls=rec.get("sources") or [],
                    verify_status=rec.get("verify_status") or "未验证",
                ))
                inserted += 1
            else:
                session.execute(relations.update().where(relations.c.id == row).values(
                    supply_content=rec.get("supply") or None,
                    credibility=rec.get("credibility") or "明确",
                    seen_count=int(rec.get("count") or 1),
                    source_urls=rec.get("sources") or [],
                    verify_status=rec.get("verify_status") or "未验证",
                ))
                updated += 1
        session.commit()
    logger.info("[db_ingest] 表1 supplier_relations upsert 完成：新增=%d 更新=%d", inserted, updated)
    return (inserted, updated)


def ingest_supplier_verifications(results: list) -> int:
    """阶段2 验证结果写表2，并回写表1 verify_status。

    results 形态来自 supplier_verify.main() 收集的 res 列表：
    {supplier, verdict, confidence, supply, evidence, orig_sources, orig_supply, ...}
    返回写入条数。
    """
    if not HAS_DB or not results:
        return 0
    url = get_database_url()
    if not url:
        logger.warning("[db_ingest] 未设置 DATABASE_URL/SYNC_DATABASE_URL，跳过表2回写")
        return 0
    engine = _engine()
    relations, verifications = _tables()
    written = 0
    now = datetime.now()
    with Session(engine) as session:
        for r in results:
            supplier = r.get("supplier")
            if not supplier:
                continue
            rel_id = find_relation_id(session, relations, supplier)
            verdict = r.get("verdict") or "待确认"
            # 校验：表2 verdict CHECK 限 (确认/否定/待确认)；
            # 上游异常类如 "验证失败：xxx"/"搜索失败：xxx" 归一到三值，失败标志用于回写表1 状态
            is_fail = "失败" in verdict
            db_verdict = "待确认" if is_fail else verdict
            session.execute(verifications.insert().values(
                id=uuid.uuid4(),
                relation_id=rel_id,
                supplier_name=supplier,
                verdict=db_verdict,
                confidence=r.get("confidence") or None,
                supply_content=r.get("supply") or None,
                evidence_md=r.get("evidence") or None,
                evidence_urls=_extract_urls(r.get("evidence") or ""),
                orig_source_urls=r.get("orig_sources") or [],
                verify_time=now,
            ))
            written += 1
            # 回写表1 最新验证状态
            if rel_id is not None:
                status = "验证失败" if is_fail else "已验证"
                session.execute(relations.update().where(relations.c.id == rel_id).values(
                    verify_status=status
                ))
        session.commit()
    logger.info("[db_ingest] 表2 supplier_verifications 写入=%d，并回写表1 验证状态", written)
    return written