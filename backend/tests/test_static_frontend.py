from pathlib import Path

from fastapi.testclient import TestClient

from app.main import create_app


def _write_frontend_dist(tmp_path: Path) -> Path:
    dist_dir = tmp_path / "dist"
    assets_dir = dist_dir / "assets"
    assets_dir.mkdir(parents=True)
    (dist_dir / "index.html").write_text(
        '<!doctype html><html><body><div id="root">frontend shell</div></body></html>',
        encoding="utf-8",
    )
    (assets_dir / "app.js").write_text("console.log('frontend asset');", encoding="utf-8")
    return dist_dir


def test_create_app_serves_frontend_index_and_assets(tmp_path: Path) -> None:
    dist_dir = _write_frontend_dist(tmp_path)
    client = TestClient(create_app(frontend_dist_dir=dist_dir))

    root_response = client.get("/")
    assert root_response.status_code == 200
    assert "frontend shell" in root_response.text

    asset_response = client.get("/assets/app.js")
    assert asset_response.status_code == 200
    assert "frontend asset" in asset_response.text


def test_create_app_uses_index_fallback_for_frontend_routes(tmp_path: Path) -> None:
    dist_dir = _write_frontend_dist(tmp_path)
    client = TestClient(create_app(frontend_dist_dir=dist_dir))

    response = client.get("/dashboard")

    assert response.status_code == 200
    assert "frontend shell" in response.text


def test_create_app_does_not_fallback_for_unknown_api_paths(tmp_path: Path) -> None:
    dist_dir = _write_frontend_dist(tmp_path)
    client = TestClient(create_app(frontend_dist_dir=dist_dir))

    response = client.get("/search/not-a-real-api-route")

    assert response.status_code == 404
    assert "frontend shell" not in response.text


def test_create_app_skips_frontend_when_dist_is_missing(tmp_path: Path) -> None:
    missing_dist = tmp_path / "missing-dist"
    client = TestClient(create_app(frontend_dist_dir=missing_dist))

    response = client.get("/")

    assert response.status_code == 404
