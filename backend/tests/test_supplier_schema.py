from __future__ import annotations

import importlib.util
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import get_args
from uuid import uuid4

import app.models  # noqa: F401
import pytest
from app.db.base import Base
from app.schemas.supplier import SupplierRelationRead, SupplierVerificationRead
from sqlalchemy import UniqueConstraint

TOOLS_SUPPLIER_DIR = Path(__file__).resolve().parents[2] / "tools" / "supplier-information"
_DB_INGEST_SPEC = importlib.util.spec_from_file_location(
    "supplier_db_ingest", TOOLS_SUPPLIER_DIR / "db_ingest.py"
)
assert _DB_INGEST_SPEC is not None
assert _DB_INGEST_SPEC.loader is not None
db_ingest = importlib.util.module_from_spec(_DB_INGEST_SPEC)
_DB_INGEST_SPEC.loader.exec_module(db_ingest)


@pytest.mark.no_db
def test_supplier_relation_is_unique_per_buyer_and_supplier() -> None:
    table = Base.metadata.tables["supplier_relations"]
    unique_columns = {
        tuple(column.name for column in constraint.columns)
        for constraint in table.constraints
        if isinstance(constraint, UniqueConstraint)
    }

    assert ("buyer_name", "supplier_name") in unique_columns


@pytest.mark.no_db
def test_supplier_verification_requires_a_relation() -> None:
    table = Base.metadata.tables["supplier_verifications"]

    assert table.c.relation_id.nullable is False


@pytest.mark.no_db
def test_supplier_verification_api_contract_requires_a_relation() -> None:
    assert get_args(SupplierVerificationRead.model_fields["relation_id"].annotation) == ()


@pytest.mark.no_db
def test_supplier_relation_read_defaults_missing_latest_confidence_to_none() -> None:
    now = datetime(2026, 9, 9, 8, 30, tzinfo=UTC)
    relation = SimpleNamespace(
        id=uuid4(),
        supplier_name="供应商甲",
        supply_content="镜头",
        buyer_name="大疆",
        credibility="明确",
        seen_count=1,
        source_urls=[],
        verify_status="未验证",
        created_at=now,
        updated_at=now,
    )

    assert SupplierRelationRead.model_validate(relation).latest_verification_confidence is None


@pytest.mark.no_db
def test_relation_update_values_refresh_updated_at() -> None:
    now = datetime(2026, 9, 9, 8, 30, tzinfo=UTC)

    values = db_ingest.relation_update_values(
        {
            "supply": "摄像头模组",
            "credibility": "明确",
            "count": "3",
            "sources": ["https://example.test/source"],
            "verify_status": "已验证",
        },
        now,
    )

    assert values["updated_at"] == now
    assert values["seen_count"] == 3
    assert "verify_status" not in values


@pytest.mark.no_db
def test_audit_status_sync_replaces_stale_markdown_verification_state() -> None:
    confirmed = {
        "有审计记录的供应商": {
            "verify_status": "未验证",
            "verify_time": "",
            "verify_verdict": "",
        },
        "无审计记录的供应商": {
            "verify_status": "已验证",
            "verify_time": "2026-09-09 11:00:00",
            "verify_verdict": "确认；镜头",
        },
    }

    changed = db_ingest.apply_audit_statuses(
        confirmed,
        {
            "有审计记录的供应商": {
                "verify_time": "2026-09-10 11:00:00",
                "verdict": "确认",
                "confidence": "明确",
                "supply_content": "摄像头模组",
            }
        },
    )

    assert changed == 2
    assert confirmed["有审计记录的供应商"]["verify_status"] == "已验证"
    assert confirmed["有审计记录的供应商"]["verify_verdict"] == "确认；摄像头模组"
    assert confirmed["无审计记录的供应商"] == {
        "verify_status": "未验证",
        "verify_time": "",
        "verify_verdict": "",
    }
