import csv

import pymupdf
import pytest

from tools.drone_documents.common import digest, read_jsonl, validate_bundle, write_jsonl
from tools.drone_documents.evaluate import answer_source_text
from tools.drone_documents.extract import annotate_software_sections, prepare, selected_text
from tools.drone_documents.language import language, page_marker
from tools.drone_documents.storage import configuration


def test_mixed_line_keeps_simplified_labels_and_technical_identifiers():
    text = (
        "Mode 2 / 美国手 / 美國手 / モード 2\n有标记\n有標記\n"
        "检查电量：短按一次。\nCheck battery level: press once.\nDJI Fly App"
    )
    selected = selected_text(text, dominant=False, marker=None)
    assert "美国手" in selected and "Mode 2" in selected and "DJI Fly App" in selected
    assert "有标记" in selected and "检查电量" in selected
    assert "モード" not in selected and "美國手" not in selected and "有標記" not in selected
    assert "Check battery" not in selected


def test_language_markers_and_japanese_are_not_treated_as_chinese():
    assert page_marker("CHS\n安全概要") == "zh-Hans"
    assert page_marker("CHS\nCHT\nJP\n目录") is None
    assert language("安全に関するガイドライン").language == "ja"
    assert selected_text("注意飞行安全", dominant=True, marker="zh-Hant") == ""


def test_native_pdf_keeps_original_page_coordinates_and_excludes_foreign_section(tmp_path):
    source = tmp_path / "input"
    source.mkdir()
    pdf = pymupdf.open()
    page = pdf.new_page()
    page.insert_text((40, 40), "EN")
    page.insert_text((40, 80), "The aircraft safety instructions are in this English section.")
    page = pdf.new_page()
    page.insert_text((40, 40), "CHS")
    page.insert_text((40, 90), "检查电量，短按一次。安装带标记的螺旋桨。", fontname="china-s")
    page.draw_rect(pymupdf.Rect(50, 130, 200, 260), color=(0, 0, 0))
    page.draw_line((60, 200), (180, 200), color=(0, 0, 0))
    page = pdf.new_page()
    page.insert_text((40, 40), "CHT")
    page.insert_text((40, 90), "檢查電量，短按一次。安裝帶標記的螺旋槳。", fontname="china-t")
    data = pdf.tobytes()
    (source / "sample.pdf").write_bytes(data)
    with (source / "文档索引.csv").open("w", encoding="utf-8-sig", newline="") as file:
        writer = csv.DictWriter(
            file,
            fieldnames=[
                "slug",
                "name",
                "title",
                "date",
                "url",
                "sha256",
                "local_path",
            ],
        )
        writer.writeheader()
        writer.writerow(
            {
                "slug": "air-2s",
                "name": "DJI Air 2S",
                "title": "用户手册 v1.0",
                "date": "2021-01-01",
                "url": "https://example.com/sample.pdf",
                "sha256": digest(data),
                "local_path": "sample.pdf",
            }
        )
    output = tmp_path / "bundle"
    prepare(source, output, "air-2s", include_specs=False)
    validate_bundle(output)
    pages = read_jsonl(output / "pages.jsonl")
    assert [p["source_page_no"] for p in pages if p["included"]] == [2]
    elements = read_jsonl(output / "elements.jsonl")
    assert all(r["source_page_no"] == 2 for e in elements for r in e["regions"])
    assert any(e["element_type"] == "figure" for e in elements)
    text = "\n".join(c["display_text"] for c in read_jsonl(output / "chunks.jsonl"))
    assert "检查电量" in text and "English section" not in text and "檢查電量" not in text
    asset = read_jsonl(output / "assets.jsonl")[0]
    (output / asset["storage_key"]).write_bytes(b"corrupt")
    with pytest.raises(ValueError, match="资源校验失败"):
        validate_bundle(output)


def test_document_storage_rejects_original_business_targets():
    with pytest.raises(ValueError, match="独立文档数据库"):
        configuration("postgresql://user:pass@localhost/intelligence_rag")
    with pytest.raises(ValueError, match="collection"):
        configuration(collection="content_chunks_bge_m3_v1")


def test_software_workflows_are_kept_separate_within_shared_guide(tmp_path):
    pdf = pymupdf.open()
    for heading in [
        "使用Blackmagic Davinci Resolve",
        "Adobe Premiere Pro CC",
        "使用Apple Final Cut Pro X",
    ]:
        page = pdf.new_page()
        page.insert_text((30, 40), heading, fontname="china-s")
    (tmp_path / "guide.pdf").write_bytes(pdf.tobytes())
    write_jsonl(
        tmp_path / "documents.jsonl",
        [
            {
                "id": "doc",
                "processing_id": "run",
                "document_type": "shared_reference",
                "source_asset_id": "source",
            }
        ],
    )
    write_jsonl(tmp_path / "assets.jsonl", [{"id": "source", "storage_key": "guide.pdf"}])
    write_jsonl(
        tmp_path / "pages.jsonl",
        [
            {"id": str(i), "processing_id": "run", "source_page_no": i, "metadata_json": {}}
            for i in range(1, 4)
        ],
    )
    write_jsonl(
        tmp_path / "elements.jsonl",
        [
            {
                "id": str(i),
                "processing_id": "run",
                "text": "安装 LUT 的操作",
                "regions": [{"source_page_no": i}],
                "metadata_json": {},
            }
            for i in range(1, 4)
        ],
    )
    annotate_software_sections(tmp_path)
    elements = read_jsonl(tmp_path / "elements.jsonl")
    assert [e["metadata_json"]["software_context"] for e in elements] == [
        "davinci_resolve",
        "premiere_pro",
        "final_cut_pro",
    ]
    assert all(e["text"] == "安装 LUT 的操作" for e in elements)


def test_generated_image_description_is_not_used_as_primary_answer_evidence():
    row = {"chunk_type": "figure_description", "text": "模型误读：必须购买软件才能使用。"}
    assert "必须购买" not in answer_source_text(row)
    assert "派生描述已从事实证据中排除" in answer_source_text(row)
    row["chunk_type"] = "text"
    assert answer_source_text(row) == row["text"]
