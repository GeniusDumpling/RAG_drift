import csv
import math
from copy import deepcopy
from types import SimpleNamespace

import pymupdf
import pytest

from tools.drone_documents.answer_checks import critical_facts, deterministic_checks
from tools.drone_documents.caption_quality import description_issues
from tools.drone_documents.common import digest, read_jsonl, validate_bundle
from tools.drone_documents.enrich import enrich
from tools.drone_documents.evaluate import answer_with_images
from tools.drone_documents.extract import prepare
from tools.drone_documents.source_fallback import source_fallback, verify_proofs
from tools.drone_documents.symbols import recover_led_tables


def circle(page, x, y, state):
    page.draw_circle((x, y), 3, fill=(0.5, 0.5, 0.5) if state != "熄灭" else None)
    if state == "闪烁":
        shape = page.new_shape()
        for i in range(12):
            angle = i * math.pi / 6
            shape.draw_line(
                (x + 4 * math.cos(angle), y + 4 * math.sin(angle)),
                (x + 5 * math.cos(angle), y + 5 * math.sin(angle)),
            )
        shape.finish(color=(0, 0, 0))
        shape.commit()


def symbolic_page():
    pdf = pymupdf.open()
    page = pdf.new_page()
    for y, state in [(50, "常亮"), (80, "闪烁"), (110, "熄灭")]:
        circle(page, 40, y, state)
        page.insert_text((50, y + 3), "表示LED灯" + state, fontname="china-s", fontsize=10)
    for i, x in enumerate([50, 90, 130, 170], 1):
        page.insert_text((x - 5, 200), "LED" + str(i), fontsize=10)
    rows = [
        ["常亮", "常亮", "闪烁", "熄灭", "50%≤电量＜63%"],
        ["常亮", "熄灭", "熄灭", "熄灭", "13%≤电量＜25%"],
    ]
    for n, row in enumerate(rows):
        y = 220 + n * 20
        for x, state in zip([50, 90, 130, 170], row[:-1], strict=True):
            circle(page, x, y, state)
        page.insert_text((220, y + 3), row[-1], fontname="china-s", fontsize=10)
    return pdf, rows


def test_led_states_recovered_from_geometry_and_explicit_legend():
    pdf, rows = symbolic_page()
    tables = recover_led_tables(pdf[0])
    assert len(tables) == 1
    assert tables[0]["rows"][1:] == rows
    assert {v["state"] for v in tables[0]["legend"].values()} == {"常亮", "闪烁", "熄灭"}
    assert all(c["bbox"] for row in tables[0]["cells"] for c in row)


def test_missing_legend_does_not_guess_states():
    pdf, _ = symbolic_page()
    page = pdf[0]
    page.add_redact_annot(pymupdf.Rect(30, 20, 350, 150))
    page.apply_redactions()
    assert recover_led_tables(page) == []


def good_caption():
    return {
        "image_type": "screenshot",
        "description_zh": "软件界面截图，包含预览窗口。",
        "visible_labels": ["DaVinci Resolve"],
        "visible_warnings": [],
        "uncertainties": [],
        "is_decorative": False,
    }


def test_caption_gate_rejects_invented_number_enumeration_and_duplicates():
    result = good_caption()
    assert description_issues(result) == []
    result["visible_labels"] = ["节点" + str(i) for i in range(1, 101)]
    issues = description_issues(result)
    assert "visible_labels_over_limit" in issues
    assert "visible_labels_suspicious_numbered_sequence" in issues
    result["visible_labels"] = ["DJI", "DJI"]
    assert "visible_labels_duplicates" in description_issues(result)


def test_caption_gate_checks_cached_result_types():
    result = good_caption()
    result["visible_warnings"] = "not an array"
    assert "visible_warnings_invalid" in description_issues(result)


def write_csv(path, rows):
    with path.open("w", encoding="utf-8-sig", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def test_footnote_shared_marker_and_source_condition_difference(tmp_path):
    pdf = pymupdf.open()
    page = pdf.new_page()
    page.insert_text(
        (40, 80), "最长飞行时间\n31 分钟（无风环境 19.4 km/h 匀速飞行）", fontname="china-s"
    )
    pdf.set_toc([[1, "规格参数", 1]])
    raw = pdf.tobytes()
    (tmp_path / "manual.pdf").write_bytes(raw)
    write_csv(
        tmp_path / "文档索引.csv",
        [
            {
                "slug": "test",
                "name": "测试机型",
                "title": "用户手册 v1.0",
                "date": "2021-01-01",
                "url": "https://example.com/manual",
                "sha256": digest(raw),
                "local_path": "manual.pdf",
            }
        ],
    )
    html = """<h2>飞行器</h2><div><strong>最长飞行时间</strong><span>飞行31分钟 *</span></div>
    <div><strong>最长悬停时间</strong><span>悬停30分钟 *
    <sup>* 无风环境以32.4km/h匀速飞行。</sup></span></div>
    <h2>电池</h2><div><strong>说明</strong><span>不关联其他章节的 *</span></div>""".encode()
    (tmp_path / "spec.html").write_bytes(html)
    write_csv(
        tmp_path / "技术参数索引.csv",
        [
            {
                "slug": "test",
                "name": "测试机型",
                "html_path": "spec.html",
                "sha256": digest(html),
                "parameter_count": "3",
                "source_url": "https://example.com/spec",
                "collected_at": "2026-10-27",
            }
        ],
    )
    output = tmp_path / "out"
    prepare(tmp_path, output, "test")
    validate_bundle(output)
    claims = read_jsonl(output / "parameter_claims.jsonl")
    flight = [c for c in claims if c["parameter_key"] == "max_flight_time"]
    assert {c["conditions"]["test_speed_kmh"] for c in flight} == {19.4, 32.4}
    hover = next(c for c in claims if c["parameter_key"] == "max_hover_time")
    assert "test_speed_kmh" not in hover["conditions"]
    assert len(read_jsonl(output / "source_differences.jsonl")) == 1
    elements = read_jsonl(output / "elements.jsonl")
    flight_html = next(
        e
        for e in elements
        if e["metadata_json"].get("footnote_ids")
        and e["metadata_json"].get("parameter_name") == "最长飞行时间"
    )
    assert "32.4" in flight_html["text"]
    assert flight_html["metadata_json"]["footnotes"][0]["binding"] == "shared_marker_same_section"
    assert any(
        r["relation_type"] == "qualifies_parameter" for r in read_jsonl(output / "links.jsonl")
    )
    assert not next(e for e in elements if e["metadata_json"].get("parameter_name") == "说明")[
        "metadata_json"
    ]["footnotes"]


def obstacle_evidence():
    return [
        {
            "chunk_type": "text",
            "text": "当前方检测到障碍物时，上升至前方没有障碍物后继续上升5 m，再继续前进。",
        }
    ]


def test_answer_check_catches_omitted_distance_despite_valid_citation():
    evidence = obstacle_evidence()
    facts = critical_facts("返航遇到障碍物怎样处理？", evidence)
    assert facts
    assert not deterministic_checks("上升至前方无障碍物后前进[1]。", evidence, facts)["passed"]
    assert deterministic_checks(
        "上升至前方无障碍物后再上升5米，然后继续前进[1]。", evidence, facts
    )["passed"]
    assert not deterministic_checks("再上升5米[99]。", evidence, facts)["passed"]


def test_led_answer_checks_preserve_inclusive_exclusive_bounds():
    evidence = [
        {
            "chunk_type": "table",
            "text": "灯态表",
            "evidence_metadata": {
                "table_kind": "led_battery_states",
                "symbol_rows": [
                    ["LED1", "LED2", "LED3", "LED4", "电量"],
                    ["常亮", "常亮", "闪烁", "熄灭", "50%≤电量＜63%"],
                ],
            },
        }
    ]
    facts = critical_facts("LED1常亮、LED2常亮、LED3闪烁、LED4熄灭，电量是多少？", evidence)
    assert facts
    assert deterministic_checks("50%≤电量<63%[1]。", evidence, facts)["passed"]
    assert not deterministic_checks("50%<电量≤63%[1]。", evidence, facts)["passed"]


def test_obstacle_answer_must_distinguish_phases_and_include_source_limits():
    evidence = [
        {
            "chunk_type": "text",
            "text": "上升过程中：暂停。前进过程中："
            "上升至前方没有障碍物后继续上升5m，再继续前进。"
            "若飞行速度大于15m/s（最大避障速度），无法避障。"
            "光照等环境条件不符合视觉系统的需求时，无法避障。",
        }
    ]
    facts = critical_facts("返航遇到障碍物怎么办？", evidence)
    assert {f["id"] for f in facts} == {
        "obstacle_extra_climb",
        "return_phase_distinction",
        "avoidance_speed_limit",
        "vision_environment",
    }
    assert not deterministic_checks("再上升5米[1]。", evidence, facts)["passed"]


def test_source_difference_must_be_disclosed_in_answer():
    difference = {
        "id": "diff",
        "dimension": "test_speed_kmh",
        "claims": [
            {"conditions": {"test_speed_kmh": 19.4}},
            {"conditions": {"test_speed_kmh": 32.4}},
        ],
    }
    evidence = [
        {
            "chunk_type": "parameter",
            "text": "31分钟",
            "evidence_metadata": {
                "source_differences": [difference],
            },
        }
    ]
    facts = critical_facts("最长飞行时间是多少？", evidence)
    assert not deterministic_checks("31分钟，19.4km/h[1]。", evidence, facts)["passed"]
    assert deterministic_checks(
        "两处测试速度分别为19.4和32.4km/h，差异未核实[1]。", evidence, facts
    )["passed"]


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["bad_labels", "invalid_json"])
async def test_bad_captions_are_quarantined_and_excluded_from_chunks(tmp_path, monkeypatch, kind):
    pdf = pymupdf.open()
    page = pdf.new_page()
    page.insert_text((40, 60), "检查安装说明。", fontname="china-s")
    page.draw_rect(pymupdf.Rect(40, 120, 200, 260))
    raw = pdf.tobytes()
    (tmp_path / "manual.pdf").write_bytes(raw)
    write_csv(
        tmp_path / "文档索引.csv",
        [
            {
                "slug": "test",
                "name": "测试型号",
                "title": "用户手册",
                "date": "",
                "url": "https://example.com/manual",
                "sha256": digest(raw),
                "local_path": "manual.pdf",
            }
        ],
    )
    output = tmp_path / "bundle"
    prepare(tmp_path, output, "test")
    monkeypatch.setattr(
        "tools.drone_documents.enrich.get_settings",
        lambda: SimpleNamespace(vlm_api_key="test", vlm_model="test"),
    )

    async def bad_response(*args, **kwargs):
        if kind == "invalid_json":
            raise ValueError("invalid model JSON after retries")
        return {**good_caption(), "visible_labels": ["节点" + str(i) for i in range(1, 101)]}

    monkeypatch.setattr("tools.drone_documents.enrich.chat_json", bad_response)
    result = await enrich(output)
    assert result["figures_quarantined"] == 1
    assert result["enrichment_failures"] == []
    assert not any(
        c["chunk_type"] == "figure_description" for c in read_jsonl(output / "chunks.jsonl")
    )
    assert next(e for e in read_jsonl(output / "elements.jsonl") if e["element_type"] == "figure")[
        "asset_id"
    ]
    validate_bundle(output)


def answer_result():
    return {
        "question": "智能返航遇到障碍物怎样处理？",
        "evidence": [
            {
                **obstacle_evidence()[0],
                "title": "用户手册",
                "section_path": "返航",
                "source_pages": [15],
                "scope_note": "",
                "assets": [],
            }
        ],
    }


@pytest.mark.asyncio
async def test_answer_repairs_missing_numeric_condition_even_if_reviewer_passes(monkeypatch):
    monkeypatch.setattr(
        "tools.drone_documents.evaluate.get_settings", lambda: SimpleNamespace(vlm_model="test")
    )
    responses = iter(
        [
            {"answer_zh": "上升至前方无障碍物后前进[1]。"},
            {
                "passed": True,
                "unsupported_claims": [],
                "missing_conditions": [],
                "citation_problems": [],
            },
            {"answer_zh": "上升至前方无障碍物后再上升5米，然后继续前进[1]。"},
            {
                "passed": True,
                "unsupported_claims": [],
                "missing_conditions": [],
                "citation_problems": [],
            },
        ]
    )

    async def response(*args, **kwargs):
        return next(responses)

    monkeypatch.setattr("tools.drone_documents.evaluate.chat_json", response)
    result = await answer_with_images(answer_result())
    assert result["answer_verification"]["status"] == "passed"
    history = result["answer_verification"]["history"]
    assert len(history) == 2 and not history[0]["deterministic"]["passed"]


@pytest.mark.asyncio
async def test_failed_entailment_review_never_reports_verified_answer(monkeypatch):
    monkeypatch.setattr(
        "tools.drone_documents.evaluate.get_settings", lambda: SimpleNamespace(vlm_model="test")
    )

    async def response(client, settings, messages, **kwargs):
        if "核验员" in messages[0]["content"]:
            return {
                "passed": False,
                "unsupported_claims": ["unsupported extra assertion"],
                "missing_conditions": [],
                "citation_problems": [],
            }
        return {"answer_zh": "再上升5米，然后继续前进[1]。"}

    monkeypatch.setattr("tools.drone_documents.evaluate.chat_json", response)
    result = await answer_with_images(answer_result())
    assert result["answer_verification"]["status"] == "needs_review"
    assert len(result["answer_verification"]["history"]) == 3


def test_source_excerpt_fallback_preserves_phase_headings_and_proves_exact_spans():
    text = (
        "返航避障过程\n上升过程中：前方有障碍物时停止上升。\n前进过程中："
        "上升至前方没有障碍物后继续上升5 m，再继续前进。"
    )
    evidence = [{"chunk_type": "text", "text": text}]
    fallback = source_fallback("返航遇到障碍物怎么办？", evidence)
    assert fallback and not fallback["model_generated"]
    assert "上升过程中" in fallback["answer_zh"] and "前进过程中" in fallback["answer_zh"]
    assert verify_proofs(fallback["proofs"], evidence)
    changed = deepcopy(fallback["proofs"])
    changed[0]["text"] += "invented"
    assert not verify_proofs(changed, evidence)


def test_source_parameter_fallback_requires_values_to_exist_in_the_source():
    evidence = []
    for speed in [19.4, 32.4]:
        claim = {
            "parameter_key": "max_flight_time",
            "value": {"number": 31.0, "unit": "min"},
            "conditions": {"test_speed_kmh": speed},
        }
        evidence.append(
            {
                "chunk_type": "parameter",
                "title": "来源" + str(speed),
                "text": f"最长飞行时间31分钟，无风{speed}km/h。",
                "evidence_metadata": {"claim": claim, "source_differences": [{"id": "diff"}]},
            }
        )
    fallback = source_fallback("最长飞行时间是多少？", evidence)
    assert fallback and "19.4" in fallback["answer_zh"] and "32.4" in fallback["answer_zh"]
    assert "差异尚未核实" in fallback["answer_zh"]
    evidence[0]["evidence_metadata"]["claim"]["value"]["number"] = 999
    assert source_fallback("最长飞行时间是多少？", evidence) is None
