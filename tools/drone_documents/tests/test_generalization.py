import csv
import json
from pathlib import Path
from types import SimpleNamespace

import pymupdf
import pytest

from tools.drone_documents.catalog import Catalog, indexed_path
from tools.drone_documents.common import digest, read_json, read_jsonl, validate_bundle
from tools.drone_documents.enrich import enrich
from tools.drone_documents.extract import parse_specifications, prepare, selected_text
from tools.drone_documents.language import page_marker
from tools.drone_documents.questions import evaluation_questions
from tools.drone_documents.report import report


def write_index(root, name, rows):
    with (root / name).open("w", encoding="utf-8-sig", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def pdf_index(root, pdf, *, models=("mini-4-pro",), title="用户手册"):
    raw = pdf.tobytes()
    (root / "source.pdf").write_bytes(raw)
    write_index(
        root,
        "文档索引.csv",
        [
            {
                "slug": slug,
                "name": "DJI " + slug,
                "title": title + " " + slug,
                "date": "",
                "url": "https://example.com/manual.pdf",
                "sha256": digest(raw),
                "local_path": "source.pdf",
            }
            for slug in models
        ],
    )


def add_page(pdf, text="", *, diagram=False):
    page = pdf.new_page()
    if text:
        page.insert_text((40, 90), text, fontname="china-s", fontsize=10)
    if diagram:
        page.draw_rect(pymupdf.Rect(50, 130, 200, 260))
        page.draw_line((60, 200), (180, 200))
    return page


def test_shared_sources_have_model_independent_processing_and_chunk_ids(tmp_path):
    pdf = pymupdf.open()
    add_page(pdf, "安装前请检查电池电量。")
    pdf_index(tmp_path, pdf, models=("mini-4-pro", "t30"))
    first, second = tmp_path / "mini", tmp_path / "agras"
    prepare(tmp_path, first, "mini-4-pro")
    prepare(tmp_path, second, "t30")
    assert read_jsonl(first / "documents.jsonl") == read_jsonl(second / "documents.jsonl")
    assert read_jsonl(first / "chunks.jsonl") == read_jsonl(second / "chunks.jsonl")
    doc = read_jsonl(second / "documents.jsonl")[0]
    assert doc["model_slugs"] == ["mini-4-pro", "t30"]
    assert "不得" in doc["scope_note"] and "Air 2S" not in doc["scope_note"]


def test_unmarked_foreign_quick_start_and_blank_pages_are_excluded(tmp_path):
    pdf = pymupdf.open()
    add_page(pdf, "Read the instructions and follow all battery safety precautions.", diagram=True)
    add_page(pdf, "1\n2\nDJI Mini 4 Pro", diagram=True)
    add_page(pdf)
    add_page(pdf, "检查电量，按住电源开关开启飞行器。")
    add_page(pdf, "DE\n中文说明不属于本页应索引的语言。", diagram=True)
    pdf_index(tmp_path, pdf, title="快速入门指南")
    output = tmp_path / "bundle"
    prepare(tmp_path, output, "mini-4-pro", ocr="off")
    pages = read_jsonl(output / "pages.jsonl")
    assert [p["source_page_no"] for p in pages if p["included"]] == [2, 4]
    assert pages[1]["inclusion_reason"] == "shared_technical_visuals"


def test_chinese_majority_does_not_include_unmarked_english_page(tmp_path):
    pdf = pymupdf.open()
    for _ in range(4):
        add_page(pdf, "请在飞行前检查电量并遵守安全操作要求。")
    add_page(pdf, "Keep clear of the aircraft. Read all safety instructions before flight.")
    pdf_index(tmp_path, pdf)
    output = tmp_path / "bundle"
    prepare(tmp_path, output, "mini-4-pro")
    assert not read_jsonl(output / "pages.jsonl")[-1]["included"]


def test_specification_parser_keeps_legacy_values_variants_and_footnotes():
    html = """<h3>M200</h3><h2>飞行器</h2><div><strong>重量</strong><br>3.8 kg</div>
    <h3>M210 RTK</h3><h2>飞行器</h2><div><strong>重量</strong><br>4.5 kg<sup>含电池</sup></div>
    <h3>旧机型</h3><h2>电池</h2><div><div><h5><strong>容量</strong></h5></div>
    <div>4500 mAh<br>测试条件说明</div></div>"""
    parameters = parse_specifications(html)
    assert [p["section"] for p in parameters] == [
        "M200 / 飞行器",
        "M210 RTK / 飞行器",
        "旧机型 / 电池",
    ]
    assert parameters[1]["value"] == "4.5 kg\n含电池"
    assert parameters[2]["value"] == "4500 mAh\n测试条件说明"


def specs_only(root, *, partial=False):
    html = b"<h2>Specs</h2><div><strong>Weight</strong><br>3 kg</div>"
    (root / "specs.html").write_bytes(html)
    write_index(
        root,
        "技术参数索引.csv",
        [
            {
                "slug": "industry-model",
                "name": "行业测试型号",
                "html_path": "specs.html",
                "source_url": "https://example.com/specs",
                "collected_at": "2026-10-26",
                "status": "partial" if partial else "saved",
                "parameter_count": "1",
                "sha256": digest(html),
                "error": "部分子型号缺失" if partial else "",
            }
        ],
    )


def test_specs_only_bundle_dynamic_questions_report_and_legacy_manifest(tmp_path):
    specs_only(tmp_path, partial=True)
    output = tmp_path / "bundle"
    prepare(tmp_path, output, "industry-model")
    validate_bundle(output)
    assert Catalog(tmp_path).summaries()[0]["pdf_count"] == 0
    assert "Air 2S" not in "\n".join(evaluation_questions(output))
    manifest = read_json(output / "manifest.json")
    manifest.pop("model_name")  # Older pilot bundles resolve the name from models.jsonl.
    (output / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    result = report(output)
    assert result["model_name"] == "行业测试型号"
    assert "# 行业测试型号 文档处理结果" in (output / "REPORT.md").read_text()
    assert '"model_name": "行业测试型号"' in (output / "preview.html").read_text()
    assert (
        read_json(output / "quality_report.json")["specification_warnings"][0]["status"]
        == "partial"
    )


@pytest.mark.asyncio
async def test_specs_only_enrichment_does_not_require_vision_credentials(tmp_path, monkeypatch):
    specs_only(tmp_path)
    output = tmp_path / "bundle"
    prepare(tmp_path, output, "industry-model")
    monkeypatch.setattr(
        "tools.drone_documents.enrich.get_settings",
        lambda: SimpleNamespace(vlm_api_key="", vlm_model="unused"),
    )
    result = await enrich(output)
    assert result["figures_total"] == 0 and result["enrichment_failures"] == []
    assert validate_bundle(output)["stage"] == "enriched"


def test_source_paths_and_specification_hashes_are_checked(tmp_path):
    with pytest.raises(ValueError, match="越界"):
        indexed_path(tmp_path, "../escape.pdf")
    specs_only(tmp_path)
    (tmp_path / "specs.html").write_bytes(b"changed")
    with pytest.raises(ValueError, match="哈希"):
        prepare(tmp_path, tmp_path / "bundle", "industry-model")


def test_custom_question_file_is_validated_and_deduplicated(tmp_path):
    path = tmp_path / "questions.json"
    path.write_text('["问题一", " 问题一 ", "问题二"]')
    assert evaluation_questions(tmp_path, path) == ["问题一", "问题二"]
    for invalid in [[], [1], [""], {"question": "text"}]:
        path.write_text(json.dumps(invalid))
        with pytest.raises(ValueError, match="JSON"):
            evaluation_questions(tmp_path, path)


@pytest.mark.parametrize(
    "label", ["DJI Mini 4 Pro", "DJI Matrice 350 RTK", "Agras T30", "Inspire 1"]
)
def test_technical_model_labels_are_not_air2s_specific(label):
    assert selected_text(label, dominant=False, marker=None) == label


@pytest.mark.parametrize("marker,expected", [("DE", "de"), ("FR", "fr"), ("简体中文", "zh-Hans")])
def test_language_section_markers(marker, expected):
    assert page_marker(marker + "\n说明") == expected


def test_unknown_model_and_empty_selection_fail_before_creating_bundle(tmp_path):
    specs_only(tmp_path)
    output = tmp_path / "bundle"
    with pytest.raises(ValueError, match="未找到型号"):
        prepare(tmp_path, output, "missing")
    assert not output.exists()
    with pytest.raises(ValueError, match="没有可处理"):
        prepare(tmp_path, output, "industry-model", include_specs=False)
    assert not output.exists()


@pytest.mark.parametrize("rotation", [0, 90])
def test_scanned_quick_start_uses_chinese_ocr(tmp_path, rotation):
    tessdata = Path(pymupdf.get_tessdata())
    if not (tessdata / "chi_sim.traineddata").exists():
        pytest.skip("Chinese Tesseract data is not installed")
    original = pymupdf.open()
    page = original.new_page()
    page.insert_text((60, 120), "检查电量，请注意飞行安全。", fontname="china-s", fontsize=24)
    image = page.get_pixmap(matrix=pymupdf.Matrix(2, 2)).tobytes("png")
    scanned = pymupdf.open()
    page = scanned.new_page()
    page.insert_image(page.rect, stream=image)
    page.set_rotation(rotation)
    pdf_index(tmp_path, scanned, title="快速入门指南")
    output = tmp_path / "scan"
    prepare(tmp_path, output, "mini-4-pro")
    pages = read_jsonl(output / "pages.jsonl")
    assert pages[0]["included"]
    assert pages[0]["metadata_json"]["source_rotation"] == rotation
    assert pages[0]["metadata_json"]["extraction_method"] == "tesseract_chi_sim_eng"
    text = "".join(e["text"] for e in read_jsonl(output / "elements.jsonl")).replace(" ", "")
    assert "检查电量" in text
