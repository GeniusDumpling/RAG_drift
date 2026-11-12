import hashlib
import io
import json
from types import SimpleNamespace

import httpx
from app.main import app
from app.schemas.search import AnswerResponse, EvidenceObject, SearchQueryRead
from app.services.ifixit_media import asset_file, images_for_chunk, query_devices
from fastapi.testclient import TestClient
from PIL import Image

from tools.ifixit_rag.media import fetch_one
from tools.ifixit_rag.media_enrichment import definitions, normalize_observation, recover_caches


def fixture_image(tmp_path, monkeypatch):
    monkeypatch.setenv("IFIXIT_MEDIA_DIR", str(tmp_path))
    image = io.BytesIO()
    Image.new("RGB", (1200, 900), "white").save(image, format="JPEG")
    raw = image.getvalue()
    sha = hashlib.sha256(raw).hexdigest()
    guid = "HB5apup35uvAbdWi"
    url = f"https://guide-images.cdn.ifixit.com/igi/{guid}.full"
    with httpx.Client(
        transport=httpx.MockTransport(lambda _: httpx.Response(200, content=raw))
    ) as client:
        asset = fetch_one(tmp_path, guid, url, client)
    ref = {
        "media_ref_id": "step-image-1",
        "source_guid": guid,
        "source_url": url,
        "step_occurrence_id": "step-1",
    }
    item = SimpleNamespace(
        item_type="repair_guide",
        canonical_url="https://www.ifixit.com/Guide/161708",
        title="DJI Mavic 3 Pro Chip ID",
        metadata_json={
            "ifixit": {"media_refs": [ref]},
            "steps": [
                {
                    "step_occurrence_id": "step-1",
                    "step_order": 1,
                    "title": "Drone Main Board Side A",
                }
            ],
        },
    )
    chunk = SimpleNamespace(chunk_metadata_json={"media_ref_ids": [ref["media_ref_id"]]})
    return item, chunk, asset, raw, sha


def test_exact_source_download_and_step_binding(tmp_path, monkeypatch):
    item, chunk, asset, _, sha = fixture_image(tmp_path, monkeypatch)
    images = images_for_chunk(item, chunk)
    assert len(images) == 1 and images[0].asset_id == sha
    assert images[0].step_order == 1 and images[0].step_title == "Drone Main Board Side A"
    assert images[0].width == 1200
    assert asset["status"] == "confirmed"
    # A different step or remote identity cannot borrow the guide's cover/other image.
    chunk.chunk_metadata_json["media_ref_ids"] = ["different-step-image"]
    assert not images_for_chunk(item, chunk)
    chunk.chunk_metadata_json["media_ref_ids"] = ["step-image-1"]
    item.metadata_json["ifixit"]["media_refs"][0]["source_url"] = "https://example.com/wrong"
    assert not images_for_chunk(item, chunk)


def test_immutable_image_api_and_corruption(tmp_path, monkeypatch):
    _, _, asset, raw, sha = fixture_image(tmp_path, monkeypatch)
    with TestClient(app) as client:
        response = client.get(f"/ifixit-media/assets/{sha}")
        assert response.status_code == 200 and response.content == raw
        assert response.headers["content-type"] == "image/jpeg"
        assert response.headers["etag"] == f'"{sha}"'
        assert client.get("/ifixit-media/assets/not-a-hash").status_code == 404
    (tmp_path / asset["storage_key"]).write_bytes(b"corrupt")
    assert asset_file(sha) is None


def test_generated_observation_is_separate_and_content_bound(tmp_path, monkeypatch):
    item, chunk, asset, _, sha = fixture_image(tmp_path, monkeypatch)
    directory = tmp_path / "observations"
    directory.mkdir()
    path = directory / f"{sha}.json"
    path.write_text(json.dumps({"sha256": "wrong", "status": "success", "description": "wrong"}))
    assert images_for_chunk(item, chunk)[0].observation is None
    path.write_text(
        json.dumps(
            {
                "sha256": sha,
                "status": "success",
                "description": "红框内丝印H6MN",
                "ocr_text": ["H6MN"],
            }
        )
    )
    image = images_for_chunk(item, chunk)[0]
    assert image.description_generated and image.ocr_text == ["H6MN"]
    assert image.source_guid == asset["guid"]


def test_visual_chunk_retains_author_uncertainty_and_exact_step(tmp_path, monkeypatch):
    item, _, asset, _, sha = fixture_image(tmp_path, monkeypatch)
    directory = tmp_path / "observations"
    directory.mkdir()
    (directory / f"{sha}.json").write_text(
        json.dumps(
            {
                "sha256": sha,
                "status": "success",
                "model": "test-vision",
                "description": "主板上的红框芯片",
                "ocr_text": ["H6MN"],
                "uncertainties": ["小字不清晰"],
            }
        )
    )
    ref = {
        **item.metadata_json["ifixit"]["media_refs"][0],
        "document_id": "ifixit:161708:en",
        "role": "step_image",
    }
    step = {
        "step_occurrence_id": "step-1",
        "step_order": 1,
        "title": "Drone Main Board Side A",
        "display_text": "Likely Leadcore H6MN applications processor",
    }
    guide = {
        "document_id": "ifixit:161708:en",
        "guide_id": 161708,
        "category_raw": "DJI Mavic 3 Pro",
        "title": item.title,
    }
    rows = definitions(
        tmp_path,
        {
            "guides": [guide],
            "guides_by_id": {guide["document_id"]: guide},
            "steps": [step],
            "media_refs": [ref],
        },
    )
    assert len(rows) == 1
    assert "Likely Leadcore H6MN" in rows[0]["body"]
    assert "生成内容，需结合原图核验" in rows[0]["body"]
    assert rows[0]["metadata"]["media_ref_ids"] == ["step-image-1"]


def test_answer_images_only_follow_cited_evidence(tmp_path, monkeypatch):
    item, chunk, _, _, _ = fixture_image(tmp_path, monkeypatch)
    correct = images_for_chunk(item, chunk)[0]
    wrong = correct.model_copy(update={"asset_id": "b" * 64, "caption": "DJI Mini 3"})
    answer = AnswerResponse(
        query=SearchQueryRead.model_construct(),
        answer="Mavic 3 Pro 的资料标注 H6MN。[2]",
        supporting_evidence=[
            EvidenceObject.model_construct(images=[wrong]),
            EvidenceObject.model_construct(images=[correct]),
        ],
    )
    assert len(answer.images) == 1 and answer.images[0].asset_id == correct.asset_id
    assert answer.images[0].citation_index == 2


def test_device_scope_preserves_variants_and_comparisons(tmp_path, monkeypatch):
    monkeypatch.setenv("IFIXIT_MEDIA_DIR", str(tmp_path))
    (tmp_path / "devices.json").write_text(
        json.dumps(
            {
                "categories": [
                    "DJI Mavic Pro",
                    "DJI Mavic 3",
                    "DJI Mavic 3 Pro",
                    "DJI Mini 3",
                    "DJI Mini 2",
                    "DJI Mini 2 SE",
                ]
            }
        )
    )
    assert query_devices("DJI Mavic 3 pro的主板芯片是什么型号", []) == {"mavic3pro"}
    assert query_devices("DJIMavic3Pro主板", []) == {"mavic3pro"}
    assert query_devices("Mini2 SE电池", []) == {"mini2se"}
    assert query_devices("Mavic 3 和 Mavic 3 Pro 有什么区别", []) == {"mavic3", "mavic3pro"}
    assert not query_devices("H6MN是什么芯片", [])


def test_long_ocr_response_preserves_every_character():
    source = "H9HCNNNBKMMLXR-NEE " * 30 + "尾部丝印"
    observation = normalize_observation(
        {"description": "芯片上的文字", "ocr_text": [source], "uncertainties": []}
    )
    assert "".join(observation["ocr_text"]) == source
    assert all(len(line) <= 120 for line in observation["ocr_text"])


def test_intro_uses_only_explicit_guide_cover(tmp_path, monkeypatch):
    item, chunk, _, _, sha = fixture_image(tmp_path, monkeypatch)
    item.metadata_json["ifixit"]["media_refs"][0].update(role="cover", step_occurrence_id=None)
    chunk.chunk_metadata_json = {"chunk_type": "guide_intro"}
    images = images_for_chunk(item, chunk)
    assert len(images) == 1 and images[0].asset_id == sha
    assert images[0].step_order is None and "指南封面" in images[0].caption
    chunk.chunk_metadata_json = {"chunk_type": "repair_step"}
    assert not images_for_chunk(item, chunk)


def test_interrupted_caches_are_preserved_and_resume_can_rebuild(tmp_path):
    from app.services.video_graph.identity import stable_hash

    for directory in ("observations", "vision-responses", "embedding-cache"):
        (tmp_path / directory).mkdir()
    observation = tmp_path / "observations" / ("a" * 64 + ".json")
    observation.write_text(json.dumps({"sha256": "a" * 64, "description": "主板"}))
    response = {"description": "主板", "ocr_text": [], "uncertainties": []}
    cached = tmp_path / "vision-responses" / ("b" * 64 + ".json")
    cached.write_text(
        json.dumps(
            {"request_hash": "b" * 64, "response": response, "response_hash": stable_hash(response)}
        )
    )
    originals = {p: p.read_bytes() for p in (observation, cached)}
    broken = [
        tmp_path / "observations" / ("c" * 64 + ".json"),
        tmp_path / "vision-responses" / ("d" * 64 + ".json"),
    ]
    for path in broken:
        path.write_bytes(b"")
    bad_vector = tmp_path / "embedding-cache" / "bad-vector.json"
    bad_vector.write_text('{"vector":[NaN]}')
    broken.append(bad_vector)
    # An active writer's temporary file must remain available for its rename.
    temporary = tmp_path / "observations" / "current.tmp"
    temporary.write_text("unfinished")
    result = recover_caches(tmp_path)
    assert result["quarantined"] == 3 and not any(p.exists() for p in broken)
    assert all(p.read_bytes() == raw for p, raw in originals.items())
    assert temporary.read_text() == "unfinished"
    assert all((tmp_path / row["quarantined_as"]).exists() for row in result["files"])
    assert recover_caches(tmp_path)["quarantined"] == 0
