import csv
import json

import pymupdf
import pytest
from app.services.drones import DroneCatalogService

from tools.drone_documents.common import digest
from tools.drone_documents.extract import parse_specifications
from tools.parrot_documents.specifications import (
    build_catalog,
    parse_html,
    parse_pdf,
    parse_pdf_page,
)


def write_index(root, filename, rows):
    with (root / filename).open("w", encoding="utf-8-sig", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=sorted({key for row in rows for key in row}))
        writer.writeheader()
        writer.writerows(rows)


def make_pdf():
    pdf = pymupdf.open()
    page = pdf.new_page(width=600, height=840)
    page.insert_text((40, 60), "Version: 8.5.0.0\nUpdated: October 1st, 2026")
    for number, name, mass, flight in [
        (1, "ANAFI UKR", "1,033 g", "38 min at 6 m/s"),
        (2, "ANAFI UKR GOV", "1,365 g with XLR battery", "70 min at 6.5 m/s"),
        (3, "ANAFI UKR XLR", "1,395 g with XLR battery", "70 min at 6.5 m/s"),
    ]:
        page = pdf.new_page(width=600, height=840)
        page.insert_text((40, 65), f"5.{number}. {name}")
        page.insert_text((40, 100), "AIRCRAFT")
        page.insert_text((45, 125), "l")
        page.insert_text((65, 125), "Mass: " + mass)
        page.insert_text((45, 155), "l")
        page.insert_text((65, 155), "Maximum flight time: " + flight)
        page.insert_text((45, 185), "l")
        page.insert_text((65, 185), "Maximum flight distance: 40 km [1]")
        # Right column begins to the left of the page midpoint. A fixed midpoint
        # would incorrectly assign all aircraft rows to EO IMAGE CHAIN.
        page.insert_text((295, 100), "EO IMAGE CHAIN")
        page.insert_text((302, 125), "l")
        page.insert_text((320, 125), "Sensor: Wide and Telephoto")
    page = pdf.new_page(width=600, height=840)
    page.insert_text(
        (40, 150),
        "[1] Extreme temperatures affect battery autonomy.\n"
        "[2] Folding is not possible with the XLR battery installed.",
    )
    page = pdf.new_page(width=600, height=840)
    page.insert_text((40, 80), "6. Pack contents")
    raw = pdf.tobytes()
    pdf.close()
    return raw


def test_native_pdf_columns_and_conditions_stay_with_their_variant():
    raw = make_pdf()
    for name, mass, flight in [
        ("ANAFI UKR", "1,033 g", "38 min"),
        ("ANAFI UKR GOV", "1,365 g", "70 min"),
        ("ANAFI UKR XLR", "1,395 g", "70 min"),
    ]:
        rows, meta = parse_pdf(raw, name)
        aircraft = {r["name_original"]: r for r in rows if r["group_original"] == "AIRCRAFT"}
        assert mass in aircraft["Mass"]["value"]
        assert flight in aircraft["Maximum flight time"]["value"]
        assert aircraft["Maximum flight distance"]["footnotes"][0]["source_page"] == 5
        assert any(r["group_original"] == "EO IMAGE CHAIN" for r in rows)
        assert not any(r["name_original"] == "Sensor" for r in aircraft.values())
        assert meta["official_version"] == "8.5.0.0"
        assert meta["updated_at"] == "2026-10-01"
    with pytest.raises(ValueError, match="定位不唯一"):
        parse_pdf(raw, "ANAFI UKR NOT-A-VARIANT")


def test_spec_page_html_keeps_nested_values_and_does_not_attach_features_to_mass():
    raw = (
        "<main><h1>Technical specifications</h1><h2>ANAFI Ai</h2>"
        "<h2>ANAFI Ai drone</h2><p>Weight: 898 g</p><p>Ready to fly in 60 seconds</p>"
        "<p>Maximum flight time: 32 min (without wind)</p>"
        "<h2>Imaging system</h2><p>Resolution:</p><ul><li>4K UHD</li><li>1080p</li></ul>"
        "<p>[图像: camera]</p></main>"
    ).encode()
    rows = parse_html(raw, "ANAFI Ai")
    mass = next(r for r in rows if r["name_original"] == "Weight")
    assert mass["value"] == "898 g"
    assert "起飞" not in mass["name"]
    assert next(r for r in rows if r["name_original"] == "Resolution")["value"] == "4K UHD\n1080p"
    assert any("without wind" in r["value"] for r in rows)
    assert not any("图像" in r["value"] for r in rows)


def test_generated_catalog_round_trips_through_dji_readers(tmp_path):
    html = (
        b"<main><h1>Technical specifications</h1><h2>ANAFI</h2><h3>ANAFI Drone</h3>"
        b"<p>Weight: 320g</p><p>Max flight time: 25 min</p></main>"
    )
    (tmp_path / "web.html").write_bytes(html)
    raw = make_pdf()
    (tmp_path / "ukr.pdf").write_bytes(raw)
    write_index(
        tmp_path,
        "网页采集记录.csv",
        [
            {
                "title": "Technical specifications",
                "status": "existing",
                "local_path": "web.html",
                "url": "https://www.parrot.com/en/drones/anafi/technical-specifications",
                "sha256": digest(html),
            }
        ],
    )
    write_index(
        tmp_path,
        "文档索引.csv",
        [
            {
                "title": "ANAFI UKR User Guide",
                "slug": "anafi-ukr",
                "status": "existing",
                "local_path": "ukr.pdf",
                "sha256": digest(raw),
                "url": "https://www.parrot.com/ukr.pdf",
            },
            {"slug": "bebop-range", "status": "failed", "title": "Bebop", "error": "404"},
        ],
    )
    summary = build_catalog(tmp_path)
    assert summary["models"] == 4
    service = DroneCatalogService(tmp_path)
    assert service.catalog().total == 4
    for item in service.catalog().items:
        assert item.manufacturer == "Parrot"
        detail = service.detail(item.slug)
        assert detail.parameter_count == item.parameter_count
        page = tmp_path / item.slug / "技术参数.html"
        assert len(parse_specifications(page.read_bytes())) == item.parameter_count
        data = json.loads((page.parent / "技术参数.json").read_text())
        assert data["collected_at"] is None
        assert data["source_sha256"] in {digest(raw), digest(html)}
        if item.slug == "anafi-ukr-gov":
            values = " ".join(p.value for g in detail.groups for p in g.parameters)
            assert "70 min" in values and "Extreme temperatures" in values
    assert build_catalog(tmp_path)["parameters"] == summary["parameters"]
    exported = tmp_path / "export"
    assert build_catalog(tmp_path, exported)["parameters"] == summary["parameters"]
    for model in json.loads((exported / "机型参数目录.json").read_text())["items"]:
        snapshot = exported / model["source_snapshot_path"]
        assert snapshot.is_relative_to(exported)
        assert digest(snapshot.read_bytes()) == model["source_sha256"]


def test_empty_native_pdf_cannot_silently_become_a_specification():
    pdf = pymupdf.open()
    page = pdf.new_page()
    page.insert_text((40, 100), "Pack contents")
    with pytest.raises(ValueError, match="AIRCRAFT"):
        parse_pdf_page(page)
    pdf.close()
