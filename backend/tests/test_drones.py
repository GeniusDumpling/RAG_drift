import csv
from pathlib import Path

import pytest
from app.api.drones import get_drone_catalog
from app.main import create_app
from app.services.drones import DroneCatalogService, parse_parameter_groups
from fastapi.testclient import TestClient

pytestmark = pytest.mark.no_db


@pytest.fixture
def catalog_root(tmp_path: Path) -> Path:
    model = tmp_path / "mavic-4-pro"
    model.mkdir()
    (model / "技术参数.html").write_text(
        "<h2>技术参数</h2><h2>飞行器</h2>"
        "<div><strong>最长飞行时间</strong><br><span>51 分钟<br>无风环境测得。</span></div>"
        "<h2>电池</h2><div><strong>容量</strong><br><div>6654 毫安时</div></div>",
        encoding="utf-8",
    )
    fields = [
        "slug", "name", "status", "group_count", "parameter_count", "source_url",
        "collected_at", "html_path", "error",
    ]
    with (tmp_path / "技术参数索引.csv").open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        writer.writerow({
            "slug": "mavic-4-pro", "name": "DJI Mavic 4 Pro", "status": "partial",
            "group_count": "2", "parameter_count": "2",
            "source_url": "https://www.dji.com/cn/support/product/mavic-4-pro",
            "collected_at": "2026-10-22T15:56:36+08:00",
            "html_path": "mavic-4-pro\\技术参数.html", "error": "部分配件参数待补齐。",
        })
    return tmp_path


def client_for(root: Path) -> TestClient:
    app = create_app(frontend_dist_dir=root / "no-frontend")
    app.dependency_overrides[get_drone_catalog] = lambda: DroneCatalogService(root)
    return TestClient(app)


def test_catalog_and_detail_read_local_index_without_database(catalog_root: Path) -> None:
    client = client_for(catalog_root)
    response = client.get("/drones")
    assert response.status_code == 200
    catalog = response.json()
    assert (catalog["total"], catalog["parameter_count"], catalog["partial_count"]) == (1, 2, 1)
    assert catalog["items"][0]["category"] == "消费航拍"
    assert "html_path" not in catalog["items"][0]

    response = client.get("/drones/mavic-4-pro")
    assert response.status_code == 200
    detail = response.json()
    assert detail["note"] == "部分配件参数待补齐。"
    assert detail["group_count"] == 2
    assert detail["groups"][0]["parameters"][0] == {
        "name": "最长飞行时间", "value": "51 分钟\n无风环境测得。",
    }
    assert detail["groups"][1]["parameters"][0]["value"] == "6654 毫安时"
    assert client.get("/drones/unknown").status_code == 404


def test_parser_keeps_variant_groups_and_legacy_table_values() -> None:
    groups = parse_parameter_groups(
        "<script>alert('unsafe')</script><h2>技术参数</h2><h3>M200</h3><h2>飞行器</h2>"
        "<div><strong>重量</strong><br><span>3.8 kg<sup>含电池</sup></span></div>"
        "<h3>M210 RTK</h3><h2>飞行器</h2>"
        "<div><div><h5><strong>重量</strong></h5><br></div><div>4.4 kg<br>含电池</div></div>"
    )
    assert [g.name for g in groups] == ["M200 / 飞行器", "M210 RTK / 飞行器"]
    assert groups[0].parameters[0].value == "3.8 kg\n含电池"
    assert groups[1].parameters[0].value == "4.4 kg\n含电池"


def test_unavailable_index_and_missing_detail_have_explicit_errors(catalog_root: Path) -> None:
    assert client_for(catalog_root / "missing").get("/drones").status_code == 503
    (catalog_root / "mavic-4-pro" / "技术参数.html").unlink()
    response = client_for(catalog_root).get("/drones/mavic-4-pro")
    assert response.status_code == 503
    assert "技术参数文件暂不可用" in response.json()["detail"]


def test_catalog_paths_cannot_read_outside_document_directory(catalog_root: Path) -> None:
    index = catalog_root / "技术参数索引.csv"
    index.write_text(
        index.read_text(encoding="utf-8-sig").replace(
            "mavic-4-pro\\技术参数.html", "../outside.html",
        ),
        encoding="utf-8-sig",
    )
    response = client_for(catalog_root).get("/drones/mavic-4-pro")
    assert response.status_code == 503


def test_unknown_drone_api_does_not_serve_frontend_html(tmp_path: Path) -> None:
    (tmp_path / "index.html").write_text("frontend shell", encoding="utf-8")
    client = TestClient(create_app(frontend_dist_dir=tmp_path))
    response = client.get("/drones/unknown/route")
    assert response.status_code == 404
    assert "frontend shell" not in response.text
