from pathlib import Path
from types import SimpleNamespace

import pytest
from app.api import database as database_api
from app.main import app
from app.repositories import document_library
from fastapi import HTTPException
from fastapi.testclient import TestClient

pytestmark = pytest.mark.no_db


def test_database_catalog_has_only_configured_names_and_no_credentials():
    response = TestClient(app).get("/database/catalog")
    assert response.status_code == 200
    targets = response.json()["items"]
    assert {t["id"] for t in targets} == {"primary", "documents", "literature"}
    assert all(set(t) == {"id", "label", "database_name", "collection"} for t in targets)
    assert "postgresql" not in response.text


def test_database_selection_rejects_arbitrary_database_and_table():
    client = TestClient(app)
    assert client.get("/database/tables?database=postgres").status_code == 404
    assert client.get("/database/overview?database=postgres").status_code == 404
    assert client.get("/database/tables/pg_authid/rows?database=documents").status_code == 404


@pytest.mark.parametrize(
    "path", ["/database/catalog", "/document-library", "/document-library/models"]
)
def test_all_document_browser_routes_follow_management_access_setting(monkeypatch, path):
    monkeypatch.setattr(
        database_api,
        "get_settings",
        lambda: SimpleNamespace(
            app_env="prod",
            database_browser_enabled=False,
        ),
    )
    assert TestClient(app).get(path).status_code == 404


def test_assets_remap_host_root_but_cannot_escape_readonly_mount(tmp_path, monkeypatch):
    root = tmp_path / "mounted"
    image = root / "batch" / "assets" / "image.png"
    image.parent.mkdir(parents=True)
    image.write_bytes(b"sample")
    source = Path("/host/project/data/processed/drone-documents")
    monkeypatch.setattr(
        document_library,
        "get_settings",
        lambda: SimpleNamespace(
            document_artifacts_dir=root,
            document_artifacts_source_root=source,
        ),
    )
    assert document_library.resolve_asset(str(source / "batch/assets/image.png")) == image
    outside = tmp_path / "secret"
    outside.write_bytes(b"private")
    (root / "link.png").symlink_to(outside)
    for key in [
        str(outside),
        str(source / "../secret"),
        str(source / "link.png"),
        str(source / "missing.png"),
    ]:
        with pytest.raises(HTTPException) as error:
            document_library.resolve_asset(key)
        assert error.value.status_code == 404


def test_assets_support_local_root_without_remapping(tmp_path, monkeypatch):
    image = tmp_path / "image.png"
    image.write_bytes(b"sample")
    monkeypatch.setattr(
        document_library,
        "get_settings",
        lambda: SimpleNamespace(
            document_artifacts_dir=tmp_path,
            document_artifacts_source_root=None,
        ),
    )
    assert document_library.resolve_asset(str(image)) == image


def test_document_filters_escape_literal_wildcards():
    assert document_library.search_pattern("  100%_test\\  ") == "%100\\%\\_test\\\\%"


def test_document_browser_is_readonly_and_bounds_pagination():
    client = TestClient(app)
    assert client.post("/document-library", json={}).status_code == 405
    assert client.get("/document-library?limit=100000").status_code == 422
    assert client.get("/document-library?offset=-1").status_code == 422
    assert client.get("/document-library?q=" + "x" * 201).status_code == 422
