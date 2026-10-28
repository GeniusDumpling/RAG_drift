"""Persist the same vendor independently for two drone brands using real PostgreSQL."""

import db_ingest
import pytest
from app.models.supplier import SupplierRelation, SupplierVerification
from app.repositories.supplier import list_supplier_relations
from sqlalchemy import select


@pytest.mark.asyncio
async def test_shared_supplier_keeps_brand_metadata_and_verification_isolated(
    db_session, monkeypatch
):
    # SQLAlchemy masks passwords in str(URL); render it explicitly only in memory.
    url = db_session.bind.url.render_as_string(hide_password=False)
    monkeypatch.setattr(db_ingest, "get_database_url", lambda: url)
    parrot = {
        "Shared Supplier": {
            "supply": "LPDDR5",
            "models": ["ANAFI UKR"],
            "evidence_years": [2025],
            "evidence_items": [
                {
                    "url": "https://parrot.test/source",
                    "models": ["ANAFI UKR"],
                    "evidence_years": [2025],
                }
            ],
            "sources": ["https://parrot.test/source"],
        }
    }
    skydio = {
        "Shared Supplier": {
            "supply": "Motor",
            "models": ["X10"],
            "evidence_years": [2024],
            "sources": ["https://skydio.test/source"],
        }
    }
    assert db_ingest.upsert_supplier_relations(parrot, buyer_name="Parrot") == (1, 0)
    assert db_ingest.upsert_supplier_relations(skydio, buyer_name="Skydio") == (1, 0)
    targets = db_ingest.load_supplier_relations_for_verification("unverified", buyer_name="Parrot")
    assert len(targets) == 1
    assert targets[0]["models"] == ["ANAFI UKR"]
    db_ingest.ingest_supplier_verifications(
        [
            {
                "supplier": "Shared Supplier",
                "buyer_name": "Parrot",
                "verdict": "确认",
                "confidence": "明确",
                "models": ["ANAFI UKR"],
                "evidence_years": [2025],
                "evidence": "Original source text",
            }
        ]
    )
    assert not db_ingest.load_supplier_relations_for_verification("unverified", buyer_name="Parrot")
    assert (
        len(db_ingest.load_supplier_relations_for_verification("unverified", buyer_name="Skydio"))
        == 1
    )
    db_ingest.ingest_supplier_verifications(
        [
            {
                "supplier": "Shared Supplier",
                "buyer_name": "Skydio",
                "verdict": "验证失败：network timeout",
            }
        ]
    )
    assert (
        len(db_ingest.load_supplier_relations_for_verification("failed", buyer_name="Skydio")) == 1
    )
    assert not db_ingest.load_supplier_relations_for_verification("failed", buyer_name="Parrot")
    db_ingest.sync_confirmed_relation_statuses(skydio, buyer_name="Skydio")
    assert skydio["Shared Supplier"]["verify_status"] == "验证失败"
    rows = (await db_session.scalars(select(SupplierRelation))).all()
    assert len(rows) == 2
    by_brand = {r.buyer_name: r for r in rows}
    assert by_brand["Parrot"].drone_models == ["ANAFI UKR"]
    assert by_brand["Parrot"].evidence_years == [2025]
    assert by_brand["Skydio"].drone_models == ["X10"]
    audits = (await db_session.scalars(select(SupplierVerification))).all()
    assert {r.relation_id for r in audits} == {r.id for r in rows}
    page = await list_supplier_relations(
        db_session,
        limit=50,
        offset=0,
        q=None,
        credibility=None,
        verify_status=None,
        buyer_name="Parrot",
    )
    assert page.total == 1
    assert page.items[0].evidence_years == [2025]
