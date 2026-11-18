import csv
import json

import pytest
from app.services.drones import DroneCatalogService

from tools.autel_documents.collector import digest
from tools.autel_documents.display_catalog import build_catalog, project_column


def test_display_catalog_separates_official_generations_and_checks_source(tmp_path):
    source = tmp_path / "source.html"
    source.write_text("""<html>
    <div class="insrecruit-list" data-type="V3"><ul><li>
    <div class="rectue-top"><span class="name">飞行器</span></div>
    <table><tr><td>重量</td><td>1250 克（含 RTK）</td></tr></table></li></ul></div>
    <div class="insrecruit-list" data-type="V2"><ul><li>
    <div class="rectue-top"><span class="name">飞行器</span></div>
    <table><tr><td>重量</td><td>1200 克（含 RTK）</td></tr></table></li></ul></div></html>""")
    row = {
        "slug": "evo-ii-rtk",
        "name": "EVO II RTK",
        "source_html_path": "source.html",
        "source_sha256": digest(source.read_bytes()),
        "source_url": "https://www.autelrobotics.cn/rtk",
        "collected_at": "2026-11-12T00:00:00Z",
        "scope_kind": "aircraft_or_family",
    }
    with (tmp_path / "技术参数索引.csv").open("w", newline="", encoding="utf-8-sig") as file:
        writer = csv.DictWriter(file, fieldnames=list(row))
        writer.writeheader()
        writer.writerow(row)
    (tmp_path / "采集状态.json").write_text(json.dumps({"pages": {}}))
    definitions = [
        (
            f"evo-ii-rtk-{version.lower()}",
            f"EVO II RTK {version}",
            "evo-ii-rtk",
            version,
            "行业应用",
        )
        for version in ["V3", "V2"]
    ]
    report = build_catalog(tmp_path, definitions=definitions, dragonfish=False)
    assert report["model_count"] == 2 and report["parameter_count"] == 2
    service = DroneCatalogService(tmp_path / "机型参数目录", manufacturer="Autel")
    assert service.detail("evo-ii-rtk-v3").groups[0].parameters[0].value == "1250 克（含 RTK）"
    assert service.detail("evo-ii-rtk-v2").groups[0].parameters[0].value == "1200 克（含 RTK）"
    source.write_text("corrupt snapshot")
    with pytest.raises(ValueError, match="hash mismatch"):
        build_catalog(tmp_path, definitions=definitions, dragonfish=False)


def test_dragonfish_projection_requires_explicit_column_headers_and_preserves_conditions():
    source = """<div class="insrecruit-list"><ul><li>
    <div class="rectue-top"><span class="name">无人机</span></div>
    <table><tr><td>参数</td><td>龙鱼standard</td><td>龙鱼Pro</td></tr>
    <tr><td>最大起飞重量</td><td>9 千克</td><td>19 千克</td></tr>
    <tr><td>工作频率</td><td>900MHz<br>*仅适用于 FCC</td><td>900MHz<br>*仅适用于 FCC</td></tr>
    </table></li></ul></div>"""
    standard, _ = project_column(source, "龙鱼standard")
    pro, _ = project_column(source, "龙鱼Pro")
    assert standard[0]["parameters"][0]["value"] == "9 千克"
    assert pro[0]["parameters"][0]["value"] == "19 千克"
    assert pro[0]["parameters"][1]["value"] == "900MHz\n*仅适用于 FCC"
    with pytest.raises(ValueError, match="explicit"):
        project_column(source, "Unknown model")
