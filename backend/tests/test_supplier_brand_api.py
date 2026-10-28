"""The brand picker, overview and supplier list must use the same database scope."""

import pytest
from app.main import app
from app.models.supplier import SupplierRelation
from httpx import ASGITransport, AsyncClient


@pytest.mark.asyncio
async def test_supplier_brand_options_and_scoped_overview(db_session):
    db_session.add_all(
        [
            SupplierRelation(
                buyer_name="大疆",
                supplier_name="Shared",
                credibility="明确",
                verify_status="已验证",
            ),
            SupplierRelation(
                buyer_name="Parrot",
                supplier_name="Shared",
                credibility="明确",
                verify_status="已验证",
                drone_models=["ANAFI USA"],
                evidence_years=[2024],
            ),
            SupplierRelation(
                buyer_name="Parrot",
                supplier_name="Other",
                credibility="疑似",
                verify_status="未验证",
            ),
            SupplierRelation(
                buyer_name="Skydio",
                supplier_name="Shared",
                credibility="明确",
                verify_status="验证失败",
            ),
            SupplierRelation(
                buyer_name="Future Drone",
                supplier_name="New",
                credibility="明确",
                verify_status="未验证",
            ),
        ]
    )
    await db_session.commit()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/supplier/brands")
        assert response.status_code == 200
        assert {b["buyer_name"]: b["total_suppliers"] for b in response.json()} == {
            "大疆": 1,
            "Parrot": 2,
            "Skydio": 1,
            "Future Drone": 1,
        }
        response = await client.get("/supplier/overview", params={"buyer_name": "Parrot"})
        assert response.json() == {
            "total_suppliers": 2,
            "confirmed_relations": 1,
            "verified": 1,
            "unverified": 1,
            "verify_failed": 0,
        }
        response = await client.get("/supplier/overview", params={"buyer_name": "Skydio"})
        assert response.json()["verify_failed"] == 1
        assert response.json()["verified"] == 0
        response = await client.get(
            "/supplier/relations", params={"buyer_name": "Parrot", "q": "Shared"}
        )
        assert response.json()["total"] == 1
        row = response.json()["items"][0]
        assert row["buyer_name"] == "Parrot"
        assert row["drone_models"] == ["ANAFI USA"]
        assert row["evidence_years"] == [2024]
        assert (await client.get("/supplier/overview")).json()["total_suppliers"] == 5
        assert (await client.get("/supplier/relations")).json()["total"] == 5
        missing = (await client.get("/supplier/overview", params={"buyer_name": "Missing"})).json()
        assert all(count == 0 for count in missing.values())
        assert (await client.get("/supplier/relations", params={"buyer_name": "Missing"})).json()[
            "total"
        ] == 0
