"""Brand routing and isolation use local catalogs; no database is required."""

import csv

import httpx
import pytest
from app.api.drones import get_drone_catalog, router
from app.core.config import Settings, get_settings
from fastapi import FastAPI


@pytest.fixture
def brand_settings(tmp_path):
    for brand, manufacturer, value in [
        ("dji", "DJI", "51 min"),
        ("parrot", "Parrot", "38 min at 6 m/s"),
        ("autel", "Autel", "42 min"),
        ("hubsan", "Hubsan", "28 min"),
        ("skydio", "Skydio", "40 minutes"),
    ]:
        root = tmp_path / brand
        if brand in {"autel", "hubsan", "skydio"}:
            root = root / "机型参数目录"
        (root / "same-slug").mkdir(parents=True)
        (root / "same-slug/技术参数.html").write_text(
            f"<h2>飞行器</h2><div><strong>最长飞行时间</strong><br>{value}</div>", encoding="utf-8"
        )
        row = {
            "slug": "same-slug",
            "name": manufacturer + " Model",
            "manufacturer": manufacturer,
            "category": "行业应用",
            "status": "saved",
            "group_count": "1",
            "parameter_count": "1",
            "source_url": "https://example.com/" + brand,
            "collected_at": "",
            "html_path": "same-slug/技术参数.html",
            "official_version": "8.5" if brand == "parrot" else "",
            "updated_at": "2026-10-01" if brand == "parrot" else "",
        }
        with (root / "技术参数索引.csv").open("w", encoding="utf-8-sig", newline="") as file:
            writer = csv.DictWriter(file, fieldnames=list(row))
            writer.writeheader()
            writer.writerow(row)
    return Settings(
        _env_file=None,
        DRONE_DOCUMENTS_DIR=tmp_path / "dji",
        PARROT_DOCUMENTS_DIR=tmp_path / "parrot",
        AUTEL_DOCUMENTS_DIR=tmp_path / "autel",
        HUBSAN_DOCUMENTS_DIR=tmp_path / "hubsan",
        SKYDIO_DOCUMENTS_DIR=tmp_path / "skydio",
    )


def test_brand_factory_keeps_same_slug_in_separate_roots(brand_settings):
    dji = get_drone_catalog(brand_settings)
    parrot = get_drone_catalog(brand_settings, brand="parrot")
    assert dji.catalog().items[0].manufacturer == "DJI"
    assert parrot.catalog().items[0].manufacturer == "Parrot"
    assert dji.detail("same-slug").groups[0].parameters[0].value == "51 min"
    detail = parrot.detail("same-slug")
    assert detail.groups[0].parameters[0].value == "38 min at 6 m/s"
    assert detail.official_version == "8.5" and detail.updated_at == "2026-10-01"
    autel = get_drone_catalog(brand_settings, brand="autel")
    assert autel.catalog().items[0].manufacturer == "Autel"
    assert autel.detail("same-slug").groups[0].parameters[0].value == "42 min"
    for brand, manufacturer, value in [
        ("hubsan", "Hubsan", "28 min"),
        ("skydio", "Skydio", "40 minutes"),
    ]:
        service = get_drone_catalog(brand_settings, brand=brand)
        assert service.catalog().items[0].manufacturer == manufacturer
        assert service.detail("same-slug").groups[0].parameters[0].value == value


@pytest.mark.asyncio
async def test_brand_query_defaults_validation_and_missing_catalog(brand_settings):
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_settings] = lambda: brand_settings
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.get("/drones")
        assert response.status_code == 200
        assert response.json()["items"][0]["manufacturer"] == "DJI"
        response = await client.get("/drones/same-slug?brand=parrot")
        assert response.status_code == 200
        assert response.json()["manufacturer"] == "Parrot"
        assert response.json()["official_version"] == "8.5"
        response = await client.get("/drones/same-slug?brand=autel")
        assert response.status_code == 200 and response.json()["manufacturer"] == "Autel"
        assert response.json()["groups"][0]["parameters"][0]["value"] == "42 min"
        assert (await client.get("/drones?brand=autel")).json()["total"] == 1
        for brand, manufacturer in [("hubsan", "Hubsan"), ("skydio", "Skydio")]:
            response = await client.get(f"/drones/same-slug?brand={brand}")
            assert response.status_code == 200 and response.json()["manufacturer"] == manufacturer
        (brand_settings.hubsan_documents_dir / "机型参数目录/技术参数索引.csv").unlink()
        response = await client.get("/drones?brand=hubsan")
        assert response.status_code == 503 and "Hubsan" in response.json()["detail"]
        assert (await client.get("/drones?brand=skydio")).status_code == 200
        (brand_settings.autel_documents_dir / "机型参数目录/技术参数索引.csv").unlink()
        response = await client.get("/drones?brand=autel")
        assert response.status_code == 503 and "Autel" in response.json()["detail"]
        assert (await client.get("/drones?brand=parrot")).status_code == 200
        assert (await client.get("/drones?brand=unknown")).status_code == 422
        assert (await client.get("/drones/same-slug?brand=../dji")).status_code == 422
        assert (await client.get("/drones/missing?brand=parrot")).status_code == 404
        (brand_settings.parrot_documents_dir / "技术参数索引.csv").unlink()
        response = await client.get("/drones?brand=parrot")
        assert response.status_code == 503 and "Parrot" in response.json()["detail"]
        assert (await client.get("/drones")).status_code == 200
