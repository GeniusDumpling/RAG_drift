import csv
from concurrent.futures import ThreadPoolExecutor

import pymupdf
import pytest

from tools.drone_documents.bulk import create_plan, run_batch, stage_worker
from tools.drone_documents.bulk_finalize import model_bundle, scoped_differences
from tools.drone_documents.common import digest, read_json, read_jsonl, validate_bundle, write_json
from tools.drone_documents.parameters import comparison_eligible, parameter_values
from tools.drone_documents.rate_limit import ModelPacer


def shared_catalog(root):
    root.mkdir()
    pdf = pymupdf.open()
    page = pdf.new_page()
    page.insert_text((40, 80), "飞行前检查电量。", fontname="china-s")
    raw = pdf.tobytes()
    (root / "guide.pdf").write_bytes(raw)
    with (root / "文档索引.csv").open("w", encoding="utf-8-sig", newline="") as file:
        writer = csv.DictWriter(
            file, fieldnames=["slug", "name", "title", "sha256", "local_path", "url", "date"]
        )
        writer.writeheader()
        for slug in ["first", "second"]:
            writer.writerow(
                {
                    "slug": slug,
                    "name": slug,
                    "title": "用户手册",
                    "sha256": digest(raw),
                    "local_path": "guide.pdf",
                    "url": "https://example.com/guide",
                    "date": "",
                }
            )


def test_bulk_plan_deduplicates_sources_and_preparation_resumes(tmp_path, monkeypatch):
    source, output = tmp_path / "input", tmp_path / "batch"
    shared_catalog(source)
    monkeypatch.setenv("DOCUMENT_MODEL_RATE_DIR", str(tmp_path / "rates"))
    plan = create_plan(source, output, exclude=())
    assert len(plan["configuration"]["models"]) == 2
    assert len(plan["jobs"]) == plan["unique_pdfs"] == 1
    job = plan["jobs"][0]
    assert stage_worker(output, job["id"], "prepare") == 0
    bundle = output / "documents" / job["id"]
    before = validate_bundle(bundle)
    assert read_jsonl(bundle / "documents.jsonl")[0]["model_slugs"] == ["first", "second"]
    assert stage_worker(output, job["id"], "prepare") == 0
    assert validate_bundle(bundle) == before
    assert read_json(output / "results" / (job["id"] + "-prepare.json"))["reused"]
    materialized = model_bundle(output, "second", plan)
    assert validate_bundle(materialized)["model_slug"] == "second"
    assert read_jsonl(materialized / "chunks.jsonl") == read_jsonl(bundle / "chunks.jsonl")
    assert validate_bundle(bundle) == before


def test_resume_refuses_a_changed_input_index(tmp_path):
    source, output = tmp_path / "input", tmp_path / "batch"
    shared_catalog(source)
    create_plan(source, output, exclude=())
    path = source / "文档索引.csv"
    path.write_bytes(path.read_bytes() + b"\n")
    with pytest.raises(ValueError, match="索引"):
        create_plan(source, output, exclude=())


def claim(id, doc, minutes, speed, **extra):
    return {
        "id": id,
        "source": {"document_id": doc},
        "parameter_key": "max_flight_time",
        "value": {"number": minutes, "unit": "min"},
        "conditions": {"test_speed_kmh": speed},
        **extra,
    }


def test_cross_model_and_shared_configuration_claims_are_not_merged():
    claims = [
        claim("a1", "d1", 31, 19.4),
        claim("a2", "d2", 31, 32.4),
        claim("b1", "d3", 45, 10),
        claim("b2", "d4", 45, 10),
        claim("shared", "d5", 13, 9),
        claim("multiple", "d6", 34, 12, comparison_eligible=False),
        claim("variant1", "d7", 34, 12),
        claim("variant2", "d7", 45, 12),
    ]
    scopes = {
        "d1": ["a"],
        "d2": ["a"],
        "d3": ["b"],
        "d4": ["b"],
        "d5": ["a", "b"],
        "d6": ["a"],
        "d7": ["a"],
    }
    differences, stats = scoped_differences(claims, scopes)
    assert len(differences) == 1
    assert differences[0]["model_slug"] == "a"
    assert {c["id"] for c in differences[0]["claims"]} == {"a1", "a2"}
    assert stats["shared_model_scope_not_compared"] == 1
    assert stats["ambiguous_or_qualified_values_not_compared"] == 3


def test_multiple_batteries_and_bounded_weights_are_not_universal_scalar_claims():
    value = "34分钟（标准电池）；45分钟（长续航电池）"
    assert [v["number"] for v in parameter_values("max_flight_time", value)] == [34, 45]
    assert not comparison_eligible("max_flight_time", value)
    assert not comparison_eligible("takeoff_weight", "低于249克")
    assert comparison_eligible("takeoff_weight", "595 g")


def test_parallel_shared_cache_writes_remain_atomic(tmp_path):
    path = tmp_path / "cache.json"
    with ThreadPoolExecutor(max_workers=8) as executor:
        list(
            executor.map(lambda n: write_json(path, {"number": n, "vector": [n] * 1024}), range(32))
        )
    data = read_json(path)
    assert data["vector"] == [data["number"]] * 1024


@pytest.mark.asyncio
async def test_global_rate_pacing_and_cooldown_are_shared_between_instances(tmp_path, monkeypatch):
    monkeypatch.setenv("DOCUMENT_MODEL_RATE_DIR", str(tmp_path))
    monkeypatch.setenv("DOCUMENT_VLM_TPM", "60000")
    clock = [100.0]
    monkeypatch.setattr("tools.drone_documents.rate_limit.time.time", lambda: clock[0])

    async def advance(seconds):
        clock[0] += seconds

    monkeypatch.setattr("tools.drone_documents.rate_limit.asyncio.sleep", advance)
    first, second = ModelPacer("test-model"), ModelPacer("test-model")
    await first.acquire(1000)
    await second.acquire(1000)
    assert clock[0] >= 101
    await first.limited(65)
    await second.acquire(1000)
    assert clock[0] >= 166
    state = read_json(next(tmp_path.glob("*.json")))
    assert state["requests"] == 3 and state["rate_limited_requests"] == 1


@pytest.mark.asyncio
async def test_actual_provider_usage_releases_unused_reserved_token_budget(tmp_path, monkeypatch):
    monkeypatch.setenv("DOCUMENT_MODEL_RATE_DIR", str(tmp_path))
    monkeypatch.setenv("DOCUMENT_VLM_TPM", "60000")
    monkeypatch.setattr("tools.drone_documents.rate_limit.time.time", lambda: 100.0)
    pacer = ModelPacer("usage-model")
    await pacer.acquire(10000)
    assert read_json(next(tmp_path.glob("*.json")))["next_at"] == 110
    pacer.succeeded(2000)
    state = read_json(next(tmp_path.glob("*.json")))
    assert state["next_at"] == 102 and state["observed_tokens"] == 2000


@pytest.mark.asyncio
async def test_coordinator_drains_text_lane_before_stopping_index_worker(tmp_path, monkeypatch):
    from tools.drone_documents.enrich import enrich

    source, output = tmp_path / "input", tmp_path / "batch"
    shared_catalog(source)
    create_plan(source, output, exclude=())
    monkeypatch.setenv("DOCUMENT_MODEL_RATE_DIR", str(tmp_path / "rates"))
    monkeypatch.setattr("tools.drone_documents.storage.bootstrap", lambda: None)
    monkeypatch.setattr("tools.drone_documents.bulk.os.killpg", lambda *args: None)
    calls = []

    async def spawn(*args, **kwargs):
        stage, job = args[args.index("--stage") + 1], args[args.index("--job-id") + 1]

        class Process:
            pid, returncode = 99999999, None

            async def wait(self):
                calls.append(stage)
                if stage == "index":
                    write_json(output / "results" / (job + "-index.json"), {"ok": True})
                    self.returncode = 0  # orchestration test; no database/model requests
                elif stage == "prepare":
                    self.returncode = stage_worker(output, job, stage)
                else:
                    details = await enrich(output / "documents" / job, cache_root=output / "cache")
                    write_json(
                        output / "results" / (job + "-enrich.json"),
                        {"ok": True, "details": details},
                    )
                    self.returncode = 0
                return self.returncode

        return Process()

    monkeypatch.setattr("tools.drone_documents.bulk.asyncio.create_subprocess_exec", spawn)
    result = await run_batch(output, parse_workers=1, enrich_workers=1, stage_timeout=15)
    assert result["status"] == "finalizing"
    assert result["stages"] == {"prepare": 1, "enrich": 1, "index": 1}
    assert calls == ["prepare", "enrich", "index"]


@pytest.mark.asyncio
async def test_worker_crash_is_reported_instead_of_hanging_queue_join(tmp_path, monkeypatch):
    source, output = tmp_path / "input", tmp_path / "batch"
    shared_catalog(source)
    create_plan(source, output, exclude=())
    monkeypatch.setattr("tools.drone_documents.storage.bootstrap", lambda: None)

    async def crash(*args, **kwargs):
        raise RuntimeError("worker_spawn_failed")

    monkeypatch.setattr("tools.drone_documents.bulk.asyncio.create_subprocess_exec", crash)
    with pytest.raises(RuntimeError, match="worker_spawn_failed"):
        await run_batch(output, parse_workers=1, enrich_workers=1)
    assert read_json(output / "status.json")["status"] == "partial_failed"
