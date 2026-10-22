from __future__ import annotations

import json
from pathlib import Path

import pytest
from PIL import Image

from tools.ifixit_rag.bundle import load_bundle
from tools.ifixit_rag.chunking import Budget, build_chunks
from tools.ifixit_rag.cleaning import clean_text
from tools.ifixit_rag.cli import context, main
from tools.ifixit_rag.common import digest
from tools.ifixit_rag.prepare import normalize_guide, parse_markdown, prepare

from .conftest import sample_guide, write_guide


def test_clean_preserves_measurements_commands_and_links():
    source = (
        "Use [product|IF145-020|tweezers] for 2.5 mm screws.\n"
        "py tool.py --port <port>  GimbalCalib\n"
        "[link|https://example.com|manual|new_window=true]\n"
        "[video|https://www.youtube.com/watch?v=example]"
    )
    text, refs, warnings = clean_text(source)
    assert "tweezers" in text and "2.5 mm" in text
    assert "<port>  GimbalCalib" in text
    assert "manual (https://example.com)" in text
    assert [r["type"] for r in refs] == ["product", "link", "video"]
    assert not warnings
    assert clean_text("[unknown|keep|this]")[0] == "[unknown|keep|this]"
    assert clean_text("[unknown|keep|this]")[2] == ["unknown_markup"]


def test_html_and_emphasis():
    text, _, _ = clean_text("<p>***Warning***: do not cut.</p><script>bad()</script><p>Next</p>")
    assert "Warning: do not cut." in text
    assert "bad()" not in text
    assert "\n" in text
    assert clean_text("<p>py --port <port> &amp; exit</p>")[0] == "py --port <port> & exit"
    assert clean_text("use &lt;port&gt;")[0] == "use <port>"


def test_parser_checks_fence_id_and_types(tmp_path):
    path = write_guide(tmp_path, sample_guide())
    assert parse_markdown(path.read_text(), path.parent.name)["guideid"] == 1
    with pytest.raises(ValueError, match="directory ID"):
        parse_markdown(path.read_text(), "2_wrong")
    with pytest.raises(ValueError, match="fenced"):
        parse_markdown('{"guideid": 1}', "1_guide")


def test_step_identity_order_origin_and_warning(tmp_path):
    raw = sample_guide()
    step = raw["steps"][0]
    raw["steps"] = [{**step, "orderby": 2}, {**step, "orderby": 1, "guideid": 100}]
    guide, steps, refs = normalize_guide(tmp_path / "1_guide/原始JSON.md", raw)
    assert steps[0]["source_guide_id"] == 1
    assert steps[1]["source_guide_id"] == 100
    assert steps[0]["step_order"] == 1
    assert len({s["step_occurrence_id"] for s in steps}) == 2
    assert steps[0]["lines"][1]["semantic"] == "warning"
    assert "[warning] Do not puncture" in steps[0]["display_text"]
    assert "duplicate_step_id" in guide["quality_flags"]
    assert len({r["media_ref_id"] for r in refs}) == 2


def test_expanded_prerequisite_order_must_not_be_sorted_by_local_order(tmp_path):
    raw = sample_guide()
    template = raw["steps"][0]
    raw["steps"] = [
        {**template, "stepid": 100, "guideid": 10, "orderby": 1},
        {**template, "stepid": 110, "guideid": 20, "orderby": 1},
        {**template, "stepid": 111, "guideid": 20, "orderby": 2},
        {**template, "stepid": 120, "guideid": 1, "orderby": 1},
    ]
    guide, steps, _ = normalize_guide(tmp_path / "1_guide/原始JSON.md", raw)
    assert [s["source_step_id"] for s in steps] == [100, 110, 111, 120]
    assert [s["step_order"] for s in steps] == [1, 2, 3, 4]
    assert [s["source_order"] for s in steps] == [1, 1, 2, 1]
    assert "invalid_step_order" not in guide["quality_flags"]


def test_missing_step_id_is_explicit(tmp_path):
    raw = sample_guide()
    del raw["steps"][0]["stepid"]
    guide, steps, _ = normalize_guide(tmp_path / "1_guide/原始JSON.md", raw)
    assert "missing_step_id" in guide["quality_flags"]
    assert "fallback-order:1" in steps[0]["step_occurrence_id"]


def test_empty_video_and_insufficient_content(make_bundle):
    bundle = make_bundle(
        sample_guide(1, steps=[], introduction_raw="[video|https://youtu.be/abc]"),
        sample_guide(2, steps=[], introduction_raw="Instructions. [video|https://youtu.be/def]"),
    )
    assert bundle["guides_by_id"]["ifixit:1:en"]["content_class"] == "insufficient_content"
    assert not bundle["chunks_by_doc"]["ifixit:1:en"]
    assert bundle["guides_by_id"]["ifixit:2:en"]["content_class"] == "video_reference"
    assert bundle["chunks_by_doc"]["ifixit:2:en"][0]["chunk_type"] == "guide_intro"


def test_prerequisites_included_and_missing(make_bundle):
    child = sample_guide(1)
    parent = sample_guide(
        2,
        prerequisites=[
            {"guideid": 1, "title": "Battery", "locale": "en"},
            {"guideid": 3, "title": "Missing", "locale": "en"},
        ],
    )
    parent["steps"][0]["guideid"] = 1
    bundle = make_bundle(child, parent)
    relations = bundle["guides_by_id"]["ifixit:2:en"]["prerequisites"]
    assert relations[0]["inclusion"] == "included"
    assert relations[0]["origin_guide_steps"] == 1
    assert relations[1]["resolution"] == "unresolved"


def test_assets_dedup_and_explicit_mapping(tmp_path, budget):
    source = tmp_path / "raw"
    path = write_guide(source, sample_guide())
    image = path.parent / "image_001.jpg"
    Image.new("RGB", (300, 225), "white").save(image)
    (path.parent / "image_002.jpg").write_bytes(image.read_bytes())
    output = tmp_path / "unmapped"
    manifest = prepare(source, output, budget)
    bundle = load_bundle(output)
    assert manifest["counts"]["unique_assets"] == 1
    assert manifest["counts"]["image_files"] == 2
    assert bundle["media_refs"][0]["mapping_status"] == "unresolved"
    assert not context(bundle, "ifixit:1:en", 1, 5000)["media_refs"]
    media_map = tmp_path / "media-map.json"
    media_map.write_text(
        json.dumps(
            [
                {
                    "document_id": "ifixit:1:en",
                    "source_guid": "imageA",
                    "local_path": str(image),
                    "sha256": digest(image.read_bytes()),
                    "evidence": "verified download manifest",
                }
            ]
        )
    )
    prepare(source, tmp_path / "mapped", budget, media_map)
    mapped = load_bundle(tmp_path / "mapped")
    assert mapped["media_refs"][0]["mapping_status"] == "confirmed"
    assert len(context(mapped, "ifixit:1:en", 1, 5000)["media_refs"]) == 1


def test_corrupt_image_is_reported(tmp_path, budget):
    path = write_guide(tmp_path / "raw", sample_guide())
    (path.parent / "image_001.jpg").write_bytes(b"not an image")
    prepare(tmp_path / "raw", tmp_path / "out", budget)
    bundle = load_bundle(tmp_path / "out")
    assert bundle["assets"][0]["decode_status"] == "failed"
    assert "image_decode_failed" in bundle["guides"][0]["quality_flags"]


def test_partial_input_and_no_clobber(tmp_path, budget):
    source = tmp_path / "raw"
    write_guide(source, sample_guide())
    bad = source / "2_broken/原始JSON.md"
    bad.parent.mkdir()
    bad.write_text("```json\n{bad}\n```\n")
    output = tmp_path / "out"
    result = prepare(source, output, budget)
    assert result["status"] == "partial" and result["counts"]["parse_errors"] == 1
    assert load_bundle(output)["manifest"]["counts"]["guides"] == 1
    with pytest.raises(ValueError, match="already exists"):
        prepare(source, output, budget)


def test_chunk_offsets_stable_hashes_and_no_truncation(make_bundle, budget):
    one = make_bundle()
    two = make_bundle()
    # Paths are intentionally run-specific, but chunk boundaries and source text are deterministic.
    assert [c["display_text"] for c in one["chunks"]] == [c["display_text"] for c in two["chunks"]]
    guide = one["guides"][0]
    for chunk in one["chunks"]:
        assert (
            guide["cleaned_text"][chunk["char_start"] : chunk["char_end"]] == chunk["display_text"]
        )
    assert build_chunks(guide, budget) == one["chunks"]
    tiny = Budget(max_tokens=16, max_chars=32)
    chunks = build_chunks(guide, tiny)
    assert any(not c["metadata"]["indexable"] for c in chunks)
    assert "Do not puncture the battery." in "\n".join(c["display_text"] for c in chunks)


def test_checksum_tampering_and_cli_dry_run(make_bundle, monkeypatch, capsys):
    bundle = make_bundle()
    directory = Path(bundle["directory"])
    monkeypatch.setenv("SYNC_DATABASE_URL", "invalid://must-not-connect")
    assert main(["import", "--bundle", str(directory), "--dry-run"]) == 0
    assert json.loads(capsys.readouterr().out)["writes"] is False
    (directory / "chunks.jsonl").write_text("{}\n")
    with pytest.raises(ValueError, match="checksum"):
        load_bundle(directory)


def test_context_reports_omission_and_chinese_tags(make_bundle):
    bundle = make_bundle()
    result = context(bundle, "ifixit:1:en", 1, 20)
    assert result["omitted_sections"]
    assert "电池" in bundle["guides"][0]["tags"]
    assert "device:dji mini 2" in bundle["guides"][0]["tags"]
    with pytest.raises(ValueError, match="step not found"):
        context(bundle, "ifixit:1:en", 99, 1000)
