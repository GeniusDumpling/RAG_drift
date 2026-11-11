"""Evidence, model I/O and evaluation invariants; fixtures do not measure model quality."""

import copy
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from app.services.document_graph.client import VisionClient
from app.services.document_graph.extraction import checked_decisions, review, review_payload
from app.services.document_graph.grounding import resolve_evidence, validate_result
from app.services.document_graph.schema import Evidence, Fact, Proposal
from app.services.document_graph.source import add_asset, checked_path, load_bundle, save_json
from app.services.video_graph.identity import stable_hash

pytestmark = pytest.mark.no_db


@pytest.fixture
def source(tmp_path):
    root = tmp_path / "source"
    image = add_asset(root, b"fixture image bytes, no VLM in these tests", ".png", "image/png")
    pdf = add_asset(root, b"fixture PDF bytes", ".pdf", "application/pdf")
    manuals, cases = [], []
    for n in (1, 2):
        sid = str(n) * 64
        page = dict(
            id=f"{sid[:16]}:p1",
            number=1,
            asset_id=image["id"],
            lines=[
                dict(id=f"{sid[:16]}:p1:l0", text="1. Camera", bbox=[0, 0, 100, 20]),
                dict(id=f"{sid[:16]}:p1:l1", text="Mass: 898 g", bbox=[0, 20, 100, 40]),
            ],
            regions=[dict(id=f"{sid[:16]}:p1:r0", bbox=[0, 100, 500, 500], asset_id=image["id"])],
        )
        manuals.append(
            dict(
                id=sid,
                sha256=sid,
                model=f"Test drone {n}",
                title=f"Test manual {n}",
                manual_version="1",
                source_url="https://example.invalid/manual.pdf",
                pdf_asset_id=pdf["id"],
                pages={"1": page},
            )
        )
        cases.append(dict(id=f"C{n}", group_id=f"C{n}", source_id=sid, kind="parts", pages=[1]))
    bundle = dict(manuals=manuals, cases=cases, assets={image["id"]: image, pdf["id"]: pdf})
    bundle["source_hash"] = stable_hash(bundle)
    save_json(root / "source.json", bundle)
    return root, bundle


def proposal():
    return Proposal.model_validate(
        dict(
            entities=[
                dict(
                    key="e1",
                    surface_text="Camera",
                    entity_type="component",
                    evidence=[dict(page=1, text_ids=["l0"], region_ids=["r0"], visual_label="1")],
                )
            ],
            facts=[
                dict(
                    subject="e0",
                    predicate="contains",
                    object="e1",
                    evidence=[dict(page=1, text_ids=["l0"], region_ids=["r0"], visual_label="1")],
                )
            ],
        )
    )


def accepting_client():
    decisions = dict(
        entities=[dict(key="e1", accepted=True, reason="fixture")],
        facts=[dict(index=0, supported=True, reason="fixture")],
    )
    return SimpleNamespace(
        request=AsyncMock(return_value={"observations": [], "labels": [1]}),
        request_text=AsyncMock(return_value=decisions),
    )


async def approved(source, case_index=0):
    root, bundle = source
    return await review(bundle, bundle["cases"][case_index], proposal(), accepting_client(), root)


def test_bundle_hash_and_pixel_integrity(source):
    root, bundle = source
    assert load_bundle(root) == bundle
    path = root / next(iter(bundle["assets"].values()))["storage_key"]
    path.write_bytes(b"changed pixels")
    with pytest.raises(ValueError, match="资源损坏"):
        load_bundle(root)


def test_bundle_metadata_corruption(source):
    root, bundle = source
    bundle["manuals"][0]["manual_version"] = "2"
    save_json(root / "source.json", bundle)
    with pytest.raises(ValueError, match="校验失败"):
        load_bundle(root)


@pytest.mark.parametrize("path", ["../secret", "/etc/passwd"])
def test_paths_cannot_escape(tmp_path, path):
    with pytest.raises(ValueError, match="越界"):
        checked_path(tmp_path, path)


@pytest.mark.parametrize(
    "evidence",
    [
        dict(page=2, text_ids=["l0"]),
        dict(page=1, text_ids=["l8"]),
        dict(page=1, region_ids=["r9"]),
        dict(page=1, text_ids=["l0", "l0"]),
    ],
)
def test_wrong_page_id_or_duplicate_references(source, evidence):
    _, b = source
    with pytest.raises(ValueError):
        resolve_evidence(b, b["cases"][0], [Evidence.model_validate(evidence)])


def test_led_states_need_operating_mode_and_picture():
    with pytest.raises(ValueError):
        Fact(
            subject="e0",
            predicate="indicates",
            value="Full",
            led_pattern=["off"] * 4,
            evidence=[Evidence(page=1, text_ids=["l0"])],
        )


async def test_review_cannot_accept_bad_source_ids(source):
    root, b = source
    p = proposal()
    p.facts[0].evidence[0].region_ids = ["r8"]
    result = await review(b, b["cases"][0], p, accepting_client(), root)
    assert not result["facts"]
    assert result["rejected"][0]["kind"] == "fact"


async def test_review_receipt_binds_pixels_names_facts_and_policy(source):
    _, b = source
    r = await approved(source)
    c = b["cases"][0]
    assert validate_result(b, c, r, r["review"]["policy"]) == r
    for change in ("name", "source", "policy", "fact"):
        tampered = copy.deepcopy(r)
        if change == "name":
            tampered["entities"][1]["canonical_name"] = "other"
        elif change == "source":
            tampered["facts"][0]["source"][0]["regions"][0]["asset_id"] = "0" * 64
        elif change == "policy":
            tampered["review"]["policy"] = "other"
        else:
            tampered["facts"][0]["proposal"]["predicate"] = "uses"
        with pytest.raises(ValueError):
            validate_result(b, c, tampered, r["review"]["policy"])


def test_review_does_not_rescue_wrong_picture_from_fullpage(source):
    _, b = source
    payload, images = review_payload(b, b["cases"][0], proposal())
    assert len(images) == 1 and images[0][0] == "p1/r0"
    assert payload["facts"][0]["source"][0]["regions"][0]["id"] == "r0"


@pytest.mark.parametrize(
    "rows",
    [
        [],
        [dict(index=0, supported=True), dict(index=0, supported=False)],
        [dict(index=0, supported="true")],
        [dict(index=2, supported=True)],
    ],
)
def test_independent_review_coverage_is_exact(rows):
    with pytest.raises(ValueError):
        checked_decisions(rows, {0}, "index", "supported")


def settings():
    return SimpleNamespace(
        vlm_model="fixture", vlm_api_key="private", vlm_base_url="https://test.invalid"
    )


async def test_exact_request_cache_and_corruption(source, tmp_path, monkeypatch):
    from tools.drone_documents.rate_limit import ModelPacer

    monkeypatch.setattr(ModelPacer, "acquire", AsyncMock())
    client = VisionClient(settings(), tmp_path / "responses")
    client._stream = AsyncMock(
        return_value=dict(
            choices=[dict(message=dict(content='{"ok":true}'), finish_reason="stop")], usage=None
        )
    )
    root, _ = source
    assert await client.request("system", {"a": 1}, [], root=root, stage="test") == {"ok": True}
    offline = VisionClient(settings(), tmp_path / "responses", cache_only=True)
    assert await offline.request("system", {"a": 1}, [], root=root, stage="test") == {"ok": True}
    assert offline.usage.snapshot()["test"] == {"checkpoint_replays": 1}
    with pytest.raises(ValueError, match="缺少精确"):
        await offline.request("system", {"a": 2}, [], root=root, stage="test")
    path = next((tmp_path / "responses").glob("*.json"))
    saved = json.loads(path.read_text())
    saved["response"]["ok"] = False
    save_json(path, saved)
    with pytest.raises(ValueError, match="缓存损坏"):
        await offline.request("system", {"a": 1}, [], root=root, stage="test")
    assert "private" not in path.read_text()


async def test_cache_does_not_bypass_image_integrity(source, tmp_path):
    root, b = source
    image = next(iter(b["assets"].values()))
    (root / image["storage_key"]).write_bytes(b"corrupt")
    with pytest.raises(ValueError, match="图片哈希"):
        await VisionClient(settings(), tmp_path / "cache", cache_only=True).request(
            "s", {}, [("image", image)], root=root, stage="test"
        )


@pytest.mark.parametrize("done,finish", [(False, "stop"), (True, "length"), (True, "stop")])
async def test_stream_completion_and_unknown_usage(tmp_path, done, finish):
    rows = [dict(choices=[dict(delta=dict(content="{}"), finish_reason=finish)])]
    text = "".join("data: " + json.dumps(r) + "\n\n" for r in rows)
    if done:
        text += "data: [DONE]\n\n"
    transport = httpx.MockTransport(lambda r: httpx.Response(200, text=text))
    client = VisionClient(settings(), tmp_path)
    async with httpx.AsyncClient(transport=transport) as http:
        if not done:
            with pytest.raises(ValueError, match="未完整结束"):
                await client._stream(http, {}, "test")
        else:
            result = await client._stream(http, {}, "test")
            assert result["choices"][0]["finish_reason"] == finish
    assert client.usage.snapshot()["test"]["unknown_usage_responses"] == 1


async def test_evaluator_rejects_wrong_image_even_if_entity_cites_right_one(source):
    from scripts.evaluate_document_graph_gold import link_checks

    result = await approved(source)
    entities = {e["key"]: e for e in result["entities"]}
    target = dict(
        evidence=dict(page=1, text_contains=["Camera"], bbox=[0, 100, 500, 500], visual_label="1")
    )
    fact = result["facts"][0]
    assert all(link_checks(target, fact, entities).values())
    fact = copy.deepcopy(fact)
    fact["source"][0]["regions"][0]["bbox"] = [500, 500, 1000, 900]
    assert not link_checks(target, fact, entities)["image_region"]


async def test_duplicate_wrong_link_is_not_hidden_by_correct_prediction(source):
    from scripts.evaluate_document_graph_gold import evaluate

    _, b = source
    r = await approved(source)
    gold = dict(
        manuals=b["manuals"],
        annotation_scope="targeted",
        human_review_required=True,
        cases=[
            dict(
                id="C1",
                targets=[
                    dict(
                        label="Camera",
                        subject=["Test drone 1"],
                        object=["Camera"],
                        predicate="contains",
                        evidence=dict(page=1, text_contains=["Camera"], bbox=[0, 100, 500, 500]),
                    )
                ],
            )
        ],
        negative_associations=[],
    )
    bad = copy.deepcopy(r["facts"][0])
    bad["index"] = 1
    bad["proposal"]["evidence"][0]["region_ids"] = []
    bad["proposal"]["evidence"][0].pop("visual_label")
    from app.services.document_graph.grounding import result_hash, validate_fact

    bad["source"] = validate_fact(
        b, b["cases"][0], Fact.model_validate(bad["proposal"]), {"e0", "e1"}
    )
    r["facts"].append(bad)
    r["review"]["decisions"]["facts"].append(dict(index=1, supported=True))
    r["review"]["decisions_hash"] = stable_hash(r["review"]["decisions"])
    r["review"]["result_hash"] = result_hash(r)
    report = evaluate(b, gold, {"C1": dict(output=r)})
    assert report["metrics"]["evidence_link_precision"] == 0.5
    assert report["metrics"]["correctly_linked_targets"] == 0


def test_led_value_must_belong_to_the_cited_table(source):
    from app.services.document_graph.grounding import validate_fact

    _, bundle = source
    page = bundle["manuals"][0]["pages"]["1"]
    page["lines"][1]["text"] = "Fully charged"
    fact = Fact(
        subject="e0",
        predicate="indicates",
        value="Fully charged",
        led_pattern=["off"] * 4,
        operating_state="charging",
        evidence=[Evidence(page=1, text_ids=["l1"], region_ids=["r0"])],
    )
    # Correct words outside the cited table must not rescue a wrong-image link.
    with pytest.raises(ValueError, match="不属于所引用图片表格"):
        validate_fact(bundle, bundle["cases"][0], fact, {"e0"})
    page["lines"][1]["bbox"] = [10, 200, 100, 220]
    assert validate_fact(bundle, bundle["cases"][0], fact, {"e0"})


async def test_image_review_is_blind_to_candidate_before_text_judgment(source):
    root, b = source
    client = accepting_client()
    await review(b, b["cases"][0], proposal(), client, root)
    blind = client.request.call_args
    assert set(blind.args[1]) == {"image"}
    assert "facts" not in blind.args[1] and "entities" not in blind.args[1]
    judgment = client.request_text.call_args
    assert "independent_image_observations" in judgment.args[1]


async def test_text_model_uses_its_own_account_cache_identity(tmp_path, monkeypatch):
    cfg = settings()
    cfg.base_url = "https://text.invalid"
    cfg.llm_api_key = "text-private"
    cfg.llm_model = "text"
    seen = []

    async def fake_request(self, system, payload, images, **kwargs):
        seen.append(
            (self.settings.vlm_model, self.settings.vlm_base_url, self.endpoint_hash, images)
        )
        self.trace.append({"stage": "text"})
        return {"ok": True}

    monkeypatch.setattr(VisionClient, "request", fake_request)
    client = VisionClient(cfg, tmp_path)
    assert await client.request_text("s", {}, root=tmp_path, stage="text") == {"ok": True}
    assert seen[0][0:2] == ("text", "https://text.invalid")
    assert seen[0][2] != client.endpoint_hash and seen[0][3] == []
    assert client.trace == [{"stage": "text"}]


def test_native_parameter_review_keeps_layout_without_replacing_text_with_ocr(source):
    _, b = source
    case = {**b["cases"][0], "kind": "specifications"}
    p = Proposal(
        entities=[],
        facts=[
            Fact(
                subject="e0",
                predicate="has_parameter",
                attribute="Mass",
                value="898 g",
                evidence=[Evidence(page=1, text_ids=["l1"])],
            )
        ],
    )
    payload, images = review_payload(b, case, p)
    assert images == []
    assert payload["native_pages"][0]["lines"][1]["text"] == "Mass: 898 g"
    assert payload["native_pages"][0]["lines"][1]["bbox"]


def test_native_marker_coordinate_cannot_be_bound_to_other_picture(source):
    _, b = source
    page = b["manuals"][0]["pages"]["1"]
    page["lines"].append(dict(id="marker", text="1", bbox=[700, 700, 710, 710]))
    with pytest.raises(ValueError, match="编号原文坐标"):
        resolve_evidence(b, b["cases"][0], proposal().facts[0].evidence)


async def test_deepseek_requests_disable_reasoning_and_use_plain_text(tmp_path, monkeypatch):
    from tools.drone_documents.rate_limit import ModelPacer

    monkeypatch.setattr(ModelPacer, "acquire", AsyncMock())
    cfg = settings()
    cfg.vlm_model = "deepseek-fixture"
    client = VisionClient(cfg, tmp_path)

    async def stream(http, body, stage):
        assert body["thinking"] == {"type": "disabled"} and body["reasoning_effort"] == "none"
        assert isinstance(body["messages"][1]["content"], str)
        return dict(choices=[dict(finish_reason="stop", message=dict(content="{}"))], usage=None)

    client._stream = stream
    assert await client.request("s", {}, [], root=tmp_path, stage="text") == {}


def test_prepared_crop_scales_both_axes_without_rotation(tmp_path, monkeypatch):
    import sys

    import pymupdf
    from app.services.document_graph.source import prepare, sha256

    monkeypatch.setitem(sys.modules, "tools.drone_documents.extract", None)
    monkeypatch.setitem(sys.modules, "langdetect", None)

    doc = pymupdf.open()
    page = doc.new_page(width=300, height=400)
    page.draw_rect(pymupdf.Rect(50, 100, 250, 220), color=(0, 0, 0), fill=(0.8, 0.8, 0.8))
    page.insert_text((60, 160), "Diagram 1")
    raw = doc.tobytes()
    doc.close()
    (tmp_path / "manual.pdf").write_bytes(raw)
    selection = dict(
        manuals=[
            dict(
                brand="DJI",
                model="DJI test drone",
                title="Test manual",
                language="en",
                manual_version="1",
                manual_date="2026",
                date_basis="fixture",
                source_url="https://example.invalid/manual.pdf",
                source_path="manual.pdf",
                sha256=sha256(raw),
                pdf_pages=1,
                sample_pages=dict(parts=[1]),
            )
        ]
    )
    save_json(tmp_path / "selection.json", selection)
    root = tmp_path / "prepared"
    b = prepare(tmp_path / "selection.json", root, source_root=tmp_path)
    region = b["manuals"][0]["pages"]["1"]["regions"][0]
    pix = pymupdf.Pixmap((root / b["assets"][region["asset_id"]]["storage_key"]).read_bytes())
    width = (region["bbox"][2] - region["bbox"][0]) / 1000 * 300
    height = (region["bbox"][3] - region["bbox"][1]) / 1000 * 400
    assert abs(pix.width - width * 2) < 2
    assert abs(pix.height - height * 2) < 2
    assert prepare(tmp_path / "selection.json", root, source_root=tmp_path) == b


def test_runtime_model_and_account_changes_invalidate_policy():
    from app.services.document_graph.extraction import policy_id

    cfg = SimpleNamespace(
        llm_model="text",
        vlm_model="omni",
        document_graph_vlm_model="vision",
        base_url="https://text.invalid",
        llm_api_key="private",
        vlm_base_url="https://vision.invalid",
        vlm_api_key="vision-private",
    )
    original = policy_id(cfg)
    cfg.document_graph_vlm_model = "different"
    assert policy_id(cfg) != original
    cfg.document_graph_vlm_model = "vision"
    cfg.llm_api_key = "changed"
    assert policy_id(cfg) != original
    assert "private" not in original and "invalid" not in original


@pytest.mark.parametrize(
    "raw",
    [
        '{"observations":[{"image":"p1/r0","visible_labels":[1,2]}]',
        '{"observations":[]}',
        '```json\n{"observations":[]}\n```',
    ],
)
def test_json_parser_can_close_complete_records_without_inventing_values(raw):
    from app.services.document_graph.client import parse_json_object

    assert isinstance(parse_json_object(raw)["observations"], list)


@pytest.mark.parametrize("raw", ['{"value":"unclosed', '{"value":', '{"value":[1,2}', "[1,2]"])
def test_json_parser_rejects_incomplete_values_and_wrong_root(raw):
    from app.services.document_graph.client import parse_json_object

    with pytest.raises(ValueError):
        parse_json_object(raw)


def test_protocol_normalization_preserves_values_and_rejects_identity_changes(source):
    from app.services.document_graph.extraction import parse_proposal

    _, b = source
    m = b["manuals"][0]
    p = proposal().model_dump(exclude_none=True)
    p["entities"].append(
        dict(
            key="e0",
            surface_text=m["model"],
            entity_type="product",
            evidence=[dict(page=1, text_ids=["l0"])],
        )
    )
    p["type"] = "json_object"
    normalized = parse_proposal(p, m)
    assert [e.key for e in normalized.entities] == ["e1"]
    assert normalized.facts[0].object == "e1"
    p["entities"][-1]["surface_text"] = "different model"
    with pytest.raises(ValueError, match="机型身份"):
        parse_proposal(p, m)


def test_conflicting_nested_led_fields_are_never_repaired(source):
    from app.services.document_graph.extraction import parse_proposal

    raw = dict(facts=[dict(qualifiers=dict(led_pattern=["off"] * 4), led_pattern=["steady"] * 4)])
    with pytest.raises(ValueError, match="冲突"):
        parse_proposal(raw, source[1]["manuals"][0])


def test_reports_cannot_overwrite_original_evidence(source):
    from app.services.document_graph.source import ensure_report_path

    root, b = source
    for path in [root / "source.json", root / next(iter(b["assets"].values()))["storage_key"]]:
        with pytest.raises(ValueError, match="不能覆盖"):
            ensure_report_path(root, path)
    ensure_report_path(root, root.parent / "report.json")


def test_vector_led_decoder_reads_off_and_radial_blink_from_original_pdf(tmp_path):
    import math

    import pymupdf
    from app.services.document_graph.symbols import led_measurements

    doc = pymupdf.open()
    page = doc.new_page(width=300, height=180)
    for i, x in enumerate([40, 80, 120, 160], 1):
        page.insert_text((x, 40), f"LED{i}", fontsize=6)
    for y, value, blink in [(78, "Fully charged", False), (108, "0 - 25% charged", True)]:
        page.insert_text((210, y + 3), value, fontsize=7)
        for i, x in enumerate([48, 88, 128, 168]):
            page.draw_circle(
                pymupdf.Point(x, y),
                4,
                color=(0, 0, 0),
                fill=(0.84, 0.84, 0.84) if blink and i == 0 else None,
            )
            if blink and i == 0:
                shape = page.new_shape()
                for step in range(8):
                    angle = step * math.pi / 4
                    shape.draw_line(
                        (x + 5 * math.cos(angle), y + 5 * math.sin(angle)),
                        (x + 7 * math.cos(angle), y + 7 * math.sin(angle)),
                    )
                shape.finish(color=(0.4, 0.4, 0.4))
                shape.commit()
    lines = []
    for block in page.get_text("dict")["blocks"]:
        for line in block.get("lines", []):
            rect = line["bbox"]
            lines.append(
                dict(
                    id=f"p1:l{len(lines)}",
                    text="".join(s["text"] for s in line["spans"]),
                    bbox=[
                        rect[0] / 300 * 1000,
                        rect[1] / 180 * 1000,
                        rect[2] / 300 * 1000,
                        rect[3] / 180 * 1000,
                    ],
                )
            )
    asset = add_asset(tmp_path, doc.tobytes(), ".pdf", "application/pdf")
    doc.close()
    b = dict(
        assets={asset["id"]: asset},
        manuals=[
            dict(
                id="source",
                sha256="source",
                pdf_asset_id=asset["id"],
                pages={
                    "1": dict(
                        number=1, lines=lines, regions=[dict(id="p1:r0", bbox=[0, 0, 1000, 1000])]
                    )
                },
            )
        ],
    )
    rows = led_measurements(b, dict(source_id="source", pages=[1], kind="led_states"), tmp_path)
    assert {r["value"]: r["led_pattern"] for r in rows} == {
        "Fully charged": ["off"] * 4,
        "0 - 25% charged": ["blinking", "off", "off", "off"],
    }
    assert all(c["method"] == "vector" for r in rows for c in r["cells"])


def test_procedure_evidence_cannot_cross_native_section_boundaries():
    from app.services.document_graph.layout import validate_section_links

    layout = [
        dict(
            page=1,
            sections=[
                dict(title="Installing card A", start=60, end=500),
                dict(title="Installing card B", start=500, end=1000),
            ],
        )
    ]
    evidence = [
        dict(
            page=1,
            text_rows=[dict(bbox=[100, 300, 500, 320])],
            regions=[dict(bbox=[100, 600, 800, 850])],
        )
    ]
    with pytest.raises(ValueError, match="不同章节"):
        validate_section_links(evidence, layout)
    evidence[0]["regions"] = [dict(bbox=[100, 100, 800, 260])]
    validate_section_links(evidence, layout)
    # A figure spanning two subsections can legitimately support both.
    evidence[0]["regions"] = [dict(bbox=[100, 200, 800, 850])]
    validate_section_links(evidence, layout)


def test_native_font_headings_establish_procedure_sections(tmp_path):
    import pymupdf
    from app.services.document_graph.layout import procedure_layout

    doc = pymupdf.open()
    page = doc.new_page(width=300, height=400)
    page.insert_text((30, 40), "Installing card A", fontsize=12)
    page.insert_text(
        (30, 65),
        "This is a longer body paragraph describing the first card and its slot.",
        fontsize=10,
    )
    page.insert_text((30, 220), "Installing card B", fontsize=12)
    page.insert_text(
        (30, 245),
        "Another longer body paragraph describes a different card and its slot.",
        fontsize=10,
    )
    asset = add_asset(tmp_path, doc.tobytes(), ".pdf", "application/pdf")
    doc.close()
    b = dict(
        assets={asset["id"]: asset},
        manuals=[
            dict(
                id="source", sha256="source", pdf_asset_id=asset["id"], pages={"1": dict(number=1)}
            )
        ],
    )
    layout = procedure_layout(b, dict(source_id="source", pages=[1], kind="procedure"), tmp_path)
    assert [s["title"] for s in layout[0]["sections"]] == ["Installing card A", "Installing card B"]
    assert layout[0]["sections"][0]["end"] == layout[0]["sections"][1]["start"]


def test_parallel_response_cache_writers_leave_complete_json(tmp_path):
    from concurrent.futures import ThreadPoolExecutor

    path = tmp_path / "response.json"
    with ThreadPoolExecutor(max_workers=4) as pool:
        jobs = [pool.submit(save_json, path, dict(writer=i, values=[i] * 1000)) for i in range(24)]
        for job in jobs:
            job.result()
    result = json.loads(path.read_text())
    assert result["values"] == [result["writer"]] * 1000
    assert not list(tmp_path.glob("*.tmp"))
