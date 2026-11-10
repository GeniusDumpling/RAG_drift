"""Publication readback and the production extractor run in isolated database fixtures."""

import importlib.util
import json
import os
import uuid
from pathlib import Path
from unittest.mock import Mock, patch

import pytest
from app.core.config import Settings
from app.models.content import ContentChunk, ContentItem
from app.schemas.video_graph import VideoAssessment
from app.services.video_graph import pipeline as pipeline
from app.services.video_graph.extraction import LLMVideoExtractor
from app.services.video_graph.storage import GraphStore
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from backend.tests import video_graph_fixtures as graph_fixtures
from backend.tests.video_graph_fixtures import StubExtractor

pytestmark = pytest.mark.no_db
ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def sources():
    if not os.environ.get("VIDEO_GRAPH_TEST_DATABASE_URL"):
        pytest.skip("VIDEO_GRAPH_TEST_DATABASE_URL must select a test PostgreSQL")
    fixture = graph_fixtures.SourceDatabase(methodName="runTest")
    fixture.setUp()
    try:
        yield fixture
    finally:
        fixture.tearDown()


@pytest.fixture
def backend_store():
    uri = os.environ.get("NEO4J_TEST_URI")
    if not uri:
        pytest.skip("NEO4J_TEST_URI must select an empty isolated graph")
    settings = Settings(
        NEO4J_URI=uri,
        NEO4J_PASSWORD=os.environ.get("NEO4J_TEST_PASSWORD"),
        NEO4J_DATABASE=os.environ.get("NEO4J_TEST_DATABASE", "neo4j"),
    )
    with GraphStore(settings) as store:
        count = store.read(
            'MATCH (n) WHERE any(l IN labels(n) WHERE l STARTS WITH "VG") RETURN count(n) AS n'
        )[0]["n"]
        assert count == 0, "Refusing to alter a graph with existing VG nodes"
        store.initialize()
        try:
            yield store
        finally:
            with store.transaction() as tx:
                tx.run(
                    'MATCH (n) WHERE any(l IN labels(n) WHERE l STARTS WITH "VG") DETACH DELETE n'
                ).consume()


def run(sources, extractor, tmp_path, store, *, content_ids=None, **kwargs):
    return list(
        pipeline.run_pipeline(
            sources.engine,
            extractor,
            tmp_path / "plan.json",
            tmp_path / "report.json",
            store=store,
            content_ids=content_ids,
            **kwargs,
        )
    )


def test_build_publish_readback_and_idempotent_resume(sources, backend_store, tmp_path):
    cid = sources.seed_video(chunks=2)
    extractor = StubExtractor()
    from app.services.video_graph.usage import TokenUsage

    extractor.token_usage = TokenUsage()
    events = run(sources, extractor, tmp_path, backend_store, content_ids=[cid])
    assert events[-1]["status"] == "success"
    result = next(e for e in events if e["stage"] == "pipeline_verification")
    assert all(result["checks"].values())
    assert result["counts"] == dict(entities=2, relations=1, mentions=4, evidence=2)
    assert extractor.calls == 2 and extractor.assessments == 1
    from sqlalchemy import text

    with sources.engine.connect() as connection:
        assert connection.scalar(text("SELECT count(*) FROM content_chunks")) == 2
        assert connection.scalar(text("SELECT count(*) FROM video_graph_builds")) == 0
        assert connection.scalar(text("SELECT count(*) FROM video_graph_evidence")) == 0
    assert json.loads((tmp_path / "report.json").read_text())["token_usage"] == {}
    again = run(sources, extractor, tmp_path, backend_store)
    assert again[-1]["status"] == "success" and extractor.calls == 2
    report = json.loads((tmp_path / "report.json").read_text())
    assert report["results"][str(cid)]["construction"]["status"] == "already_current"
    # A new plan still checks already-current data instead of silently doing no work.
    fresh = tmp_path / "second"
    assert (
        run(sources, extractor, fresh, backend_store, content_ids=[cid])[-1]["status"] == "success"
    )
    assert extractor.calls == 2


def test_pipeline_enqueues_only_its_bounded_plan(sources, backend_store, tmp_path):
    ids = [sources.seed_video() for _ in range(3)]
    extractor = StubExtractor()
    events = run(sources, extractor, tmp_path, backend_store, limit=1)
    plan = json.loads((tmp_path / "plan.json").read_text())
    assert len(plan["items"]) == extractor.calls == 1
    selected = uuid.UUID(plan["items"][0]["content_id"])
    assert selected in ids and events[-1]["status"] == "success"
    for cid in set(ids) - {selected}:
        with Session(sources.engine) as session:
            assert not pipeline.state_snapshot(session, cid, backend_store).get("active_id")
            assert not backend_store.read("MATCH (v:VGVideo {id:$id}) RETURN v", id=str(cid))


def test_dry_run_never_writes_plans_or_calls_model(sources, backend_store, tmp_path):
    cid = sources.seed_video()
    extractor = StubExtractor()
    events = run(sources, extractor, tmp_path, backend_store, content_ids=[cid], dry_run=True)
    assert len(events) == 1 and events[0]["mode"] == "read_only"
    assert not (tmp_path / "plan.json").exists() and not (tmp_path / "report.json").exists()
    assert extractor.calls == extractor.assessments == 0


def test_missing_or_withdrawn_source_is_not_silently_reported_as_built(
    sources, backend_store, tmp_path
):
    with pytest.raises(ValueError, match="找不到视频"):
        run(sources, StubExtractor(), tmp_path, backend_store, content_ids=[uuid.uuid4()])
    assert not (tmp_path / "plan.json").exists()


def test_provider_failure_resumes_checkpoint_and_requires_explicit_retry(
    sources, backend_store, tmp_path
):
    cid = sources.seed_video(chunks=2)
    failed = StubExtractor(fail_at=2)
    assert (
        run(sources, failed, tmp_path, backend_store, content_ids=[cid])[-1]["status"]
        == "incomplete"
    )
    resumed = StubExtractor()
    assert run(sources, resumed, tmp_path, backend_store)[-1]["status"] == "incomplete"
    assert resumed.calls == 0
    events = run(sources, resumed, tmp_path, backend_store, retry_failed=True)
    assert events[-1]["status"] == "success"
    assert resumed.calls == 1 and resumed.assessments == 0


def test_partial_projection_is_not_complete_and_can_be_retried(sources, backend_store, tmp_path):
    class InvalidSecond(StubExtractor):
        def extract(self, *args):
            result = super().extract(*args)
            if self.calls == 2:
                result.relations[0].quote = "Not in the original transcript."
            return result

    cid = sources.seed_video(chunks=2)
    events = run(sources, InvalidSecond(), tmp_path, backend_store, content_ids=[cid])
    assert events[-1]["status"] == "incomplete"
    report = json.loads((tmp_path / "report.json").read_text())
    assert report["results"][str(cid)]["construction"]["status"] == "partial_success"
    assert not report["results"][str(cid)]["verification"]["verified"]
    repaired = StubExtractor()
    assert (
        run(sources, repaired, tmp_path, backend_store, retry_failed=True)[-1]["status"]
        == "success"
    )
    assert repaired.calls == 1


def test_stale_manifest_and_corrupted_evidence_fail_closed(sources, backend_store, tmp_path):
    cid = sources.seed_video()
    extractor = StubExtractor()
    assert (
        run(sources, extractor, tmp_path, backend_store, content_ids=[cid])[-1]["status"]
        == "success"
    )
    with backend_store.transaction() as tx:
        tx.run("MATCH (e:VGEvidence) DETACH DELETE e").consume()
    events = run(sources, extractor, tmp_path, backend_store)
    assert events[-1]["status"] == "incomplete"
    report = json.loads((tmp_path / "report.json").read_text())
    checks = report["results"][str(cid)]["verification"]["checks"]
    assert not checks["evidence_equal"] and not checks["relations_equal"]
    assert extractor.calls == 1
    sources.change_transcript(cid)
    assert run(sources, extractor, tmp_path, backend_store)[-1]["status"] == "incomplete"
    report = json.loads((tmp_path / "report.json").read_text())
    assert report["results"][str(cid)]["construction"]["status"] == "stale_source"


def test_current_optimized_extractor_reaches_graph_and_retrieval(sources, backend_store, tmp_path):
    from scripts.evaluate_entity_gold import case_text
    from scripts.video_graph_response_cache import CachedEvaluationExtractor, ModelResponseCache

    directory = ROOT / "data/video_graph_runs/token-baseline-responses-20261106"
    if not directory.is_dir() or not Settings().llm_api_key:
        pytest.skip("Exact reviewed model cache and configured cache identity required")
    gold = json.loads((ROOT / "scripts/evaluation_data/entity_gold_v3.json").read_text())
    case = next(c for c in gold["cases"] if c["id"] == "EG003")
    cid = sources.seed_video()
    with Session(sources.engine) as session, session.begin():
        item = session.get(ContentItem, cid)
        item.title, item.canonical_url, item.cleaned_text = (
            case["source"]["title"],
            case["source"]["url"],
            "",
        )
        item.metadata_json = {"caption_language": case["language"]}
        chunk = session.scalar(select(ContentChunk).where(ContentChunk.content_item_id == cid))
        chunk.display_text = chunk.embed_text = case_text(case)
        chunk.chunk_metadata_json = dict(
            evidence_type="transcript_segment",
            start_seconds=case["source"]["start_seconds"],
            end_seconds=case["source"]["end_seconds"],
        )

    class CachedProductionExtractor(CachedEvaluationExtractor):
        def assess(self, material):
            return VideoAssessment(relevant=True, reason="Reviewed UAV fixture")

    extractor = CachedProductionExtractor(
        Settings(),
        cache=ModelResponseCache(directory, cache_only=True),
        namespace="entity:EG003:attempt0",
    )
    assert isinstance(extractor, LLMVideoExtractor)
    events = run(sources, extractor, tmp_path, backend_store, content_ids=[cid])
    assert events[-1]["status"] == "success", events
    result = next(e for e in events if e["stage"] == "pipeline_verification")
    assert result["counts"]["entities"] == 2 and result["counts"]["relations"] == 1
    assert result["checks"]["retrievable"]
    assert (
        extractor.cache_stats["live_stages"] == 0 and extractor.cache_stats["replayed_stages"] >= 4
    )


def load_cli():
    spec = importlib.util.spec_from_file_location(
        "video_graph_pipeline_cli", ROOT / "tools/video-knowledge-graph/build_graph.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_cli_dry_run_does_not_require_model_credentials(tmp_path):
    cli = load_cli()
    settings = Settings(_env_file=None, LLM_API_KEY=None)
    events = iter([{"stage": "pipeline_plan", "mode": "read_only"}])
    engine = Mock()
    with (
        patch("app.services.video_graph.storage.GraphStore"),
        patch.object(cli, "get_settings", return_value=settings),
        patch.object(
            cli,
            "create_engine",
            return_value=engine,
        ),
        patch.object(cli, "run_pipeline", return_value=events) as execute,
        patch.object(
            cli,
            "LLMVideoExtractor",
        ) as model,
    ):
        code = cli.main(
            [
                "pipeline",
                "--manifest",
                str(tmp_path / "p.json"),
                "--report",
                str(tmp_path / "r.json"),
                "--dry-run",
            ]
        )
    assert code == 0 and execute.call_args.kwargs["dry_run"]
    model.assert_not_called()


@pytest.mark.parametrize("status,code", [("success", 0), ("incomplete", 1)])
def test_cli_exit_code_comes_from_verification(tmp_path, status, code):
    cli = load_cli()
    settings = Settings(_env_file=None, LLM_API_KEY="test")
    with (
        patch("app.services.video_graph.storage.GraphStore"),
        patch.object(cli, "get_settings", return_value=settings),
        patch.object(
            cli,
            "create_engine",
            return_value=Mock(),
        ),
        patch.object(
            cli,
            "run_pipeline",
            return_value=iter(
                [
                    {"stage": "pipeline_summary", "status": status},
                ]
            ),
        ),
        patch.object(cli, "LLMVideoExtractor"),
    ):
        assert (
            cli.main(
                [
                    "pipeline",
                    "--manifest",
                    str(tmp_path / "p.json"),
                    "--report",
                    str(tmp_path / "r.json"),
                ]
            )
            == code
        )


@pytest.mark.parametrize("mode", ["irrelevant", "summary", "no_chunks"])
def test_legitimate_empty_or_entity_only_graph_can_close(sources, backend_store, tmp_path, mode):
    cid = sources.seed_video(kind="video_summary" if mode == "summary" else "transcript_segment")
    if mode == "no_chunks":
        with Session(sources.engine) as session, session.begin():
            session.execute(delete(ContentChunk).where(ContentChunk.content_item_id == cid))
    extractor = StubExtractor(relevant=mode != "irrelevant")
    events = run(sources, extractor, tmp_path, backend_store, content_ids=[cid])
    assert events[-1]["status"] == "success"
    check = next(e for e in events if e["stage"] == "pipeline_verification")
    assert check["counts"]["relations"] == check["counts"]["evidence"] == 0
    assert check["counts"]["entities"] == (2 if mode == "summary" else 0)
    assert extractor.calls == (1 if mode == "summary" else 0)


@pytest.mark.parametrize("field", ["assessment", "published_coverage", "projection_activated"])
def test_publication_metadata_is_required_not_just_a_success_status(
    sources,
    backend_store,
    tmp_path,
    field,
):
    cid = sources.seed_video()
    extractor = StubExtractor()
    assert (
        run(sources, extractor, tmp_path, backend_store, content_ids=[cid])[-1]["status"]
        == "success"
    )
    with Session(sources.engine) as session:
        snapshot = pipeline._active_snapshot(session, cid, backend_store)
    damaged = dict(snapshot["output"])
    damaged.pop(field)
    with backend_store.transaction() as tx:
        tx.run(
            "MATCH (b:VGBuild {id:$id}) SET b.output_json=$output",
            id=snapshot["id"],
            output=json.dumps(damaged),
        ).consume()
    events = run(sources, extractor, tmp_path, backend_store)
    verification = next(e for e in events if e["stage"] == "pipeline_verification")
    assert events[-1]["status"] == "incomplete"
    assert verification["status"] == "invalid_saved_output"
    assert not verification["checks"]["saved_output_valid"]


def test_withdrawn_video_requires_explicit_restore(sources, backend_store, tmp_path):
    cid = sources.seed_video()
    extractor = StubExtractor()
    assert (
        run(sources, extractor, tmp_path, backend_store, content_ids=[cid])[-1]["status"]
        == "success"
    )
    backend_store.withdraw(cid)
    with pytest.raises(ValueError, match="已撤回"):
        run(sources, extractor, tmp_path, backend_store, content_ids=[cid])
    assert extractor.calls == 1


def test_cli_requires_the_final_pipeline_summary(tmp_path):
    cli = load_cli()
    settings = Settings(_env_file=None, LLM_API_KEY="test")
    with (
        patch("app.services.video_graph.storage.GraphStore"),
        patch.object(cli, "get_settings", return_value=settings),
        patch.object(
            cli,
            "create_engine",
            return_value=Mock(),
        ),
        patch.object(
            cli,
            "run_pipeline",
            return_value=iter(
                [
                    {"stage": "backfill_result", "status": "success"},
                ]
            ),
        ),
        patch.object(cli, "LLMVideoExtractor"),
    ):
        assert (
            cli.main(
                [
                    "pipeline",
                    "--manifest",
                    str(tmp_path / "p.json"),
                    "--report",
                    str(tmp_path / "r.json"),
                ]
            )
            == 1
        )


def seed_cached_case(sources):
    from scripts.entity_gold_dataset import case_text

    case = next(
        c
        for c in json.loads((ROOT / "scripts/evaluation_data/entity_gold_v3.json").read_text())[
            "cases"
        ]
        if c["id"] == "EG003"
    )
    cid = sources.seed_video()
    with Session(sources.engine) as session, session.begin():
        item = session.get(ContentItem, cid)
        item.title = case["source"]["title"]
        item.canonical_url = case["source"]["url"]
        item.cleaned_text = ""
        item.metadata_json = {"caption_language": case["language"]}
        chunk = session.scalar(select(ContentChunk).where(ContentChunk.content_item_id == cid))
        chunk.display_text = chunk.embed_text = case_text(case)
        chunk.chunk_metadata_json = dict(
            evidence_type="transcript_segment",
            start_seconds=case["source"]["start_seconds"],
            end_seconds=case["source"]["end_seconds"],
        )
    return cid


def cached_http_server():
    """Replay only exact recorded request bodies; unknown requests never get fake extractions."""
    from contextlib import contextmanager
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    from threading import Thread

    from app.services.video_graph.identity import stable_hash

    @contextmanager
    def serve():
        prediction = ROOT / "data/video_graph_runs/token-baseline-20261106.json"
        if not prediction.exists():
            pytest.skip("Reviewed exact model-response fixture is unavailable")
        result = json.loads(prediction.read_text())["results"]["EG003"]
        cache = ROOT / "data/video_graph_runs/token-baseline-responses-20261106"
        responses = {}
        for item in result["cache_trace"]:
            path = cache / (item["request_hash"] + ".json")
            if not path.exists():
                pytest.skip("Recorded response cache is unavailable")
            envelope = json.loads(path.read_text())
            responses[stable_hash(envelope["request"])] = envelope["response"]
        observed = []

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                system = body["messages"][0]["content"]
                if system.startswith("判断材料是否与无人机"):
                    response = dict(relevant=True, reason="Reviewed UAV fixture")
                    observed.append("assessment_fixture")
                else:
                    response = responses.get(stable_hash(body))
                    observed.append("exact_model_response" if response else "cache_miss")
                self.send_response(200 if response else 400)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(
                    json.dumps(
                        {
                            "choices": [
                                {"message": {"content": json.dumps(response, ensure_ascii=False)}}
                            ]
                        },
                        ensure_ascii=False,
                    ).encode()
                )

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            yield f"http://127.0.0.1:{server.server_port}/v1", observed
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)

    return serve()


def invoke_pipeline_cli(sources, store, cid, manifest, report, endpoint):
    import subprocess

    from sqlalchemy.engine import make_url

    url = make_url(os.environ["VIDEO_GRAPH_TEST_DATABASE_URL"]).update_query_dict(
        {
            "options": f"-csearch_path={sources.schema}",
        }
    )
    env = {
        **os.environ,
        "DATABASE_URL": url.render_as_string(hide_password=False),
        "SYNC_DATABASE_URL": url.render_as_string(hide_password=False),
        "BASE_URL": endpoint,
        "LLM_API_KEY": "local-exact-response-fixture",
        "LLM_MODEL": "deepseek-v4.1-flash-economy",
        "VIDEO_GRAPH_EXTRACTION_EFFORT": "none",
        "VIDEO_GRAPH_REASONING_EFFORT": "none",
        "NO_PROXY": "127.0.0.1,localhost",
        "no_proxy": "127.0.0.1,localhost",
        "NEO4J_URI": os.environ.get("NEO4J_TEST_URI", "bolt://127.0.0.1:1"),
        "NEO4J_PASSWORD": os.environ.get("NEO4J_TEST_PASSWORD", "unused"),
        "NEO4J_USER": "neo4j",
        "NEO4J_DATABASE": os.environ.get("NEO4J_TEST_DATABASE", "neo4j"),
    }
    return subprocess.run(
        [
            str(ROOT / ".venv/bin/python"),
            str(ROOT / "tools/video-knowledge-graph/build_graph.py"),
            "pipeline",
            "--content-id",
            str(cid),
            "--limit",
            "1",
            "--manifest",
            str(manifest),
            "--report",
            str(report),
        ],
        env=env,
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=90,
    )


def test_real_cli_http_extraction_publication_and_query(sources, backend_store, tmp_path):
    cid = seed_cached_case(sources)
    manifest, report = tmp_path / "cli-plan.json", tmp_path / "cli-report.json"
    with cached_http_server() as (endpoint, requests):
        first = invoke_pipeline_cli(sources, backend_store, cid, manifest, report, endpoint)
        assert first.returncode == 0, first.stdout + first.stderr
        assert requests.count("assessment_fixture") == 1
        assert requests.count("exact_model_response") >= 4 and "cache_miss" not in requests
        count = len(requests)
        first_payload = json.loads(report.read_text())
        first_usage = first_payload["token_usage"]
        assert set(first_usage) == {
            "assessment",
            "entity_extraction",
            "entity_review",
            "relation",
            "relation_review",
        }
        assert all(row["unknown_usage_responses"] >= 1 for row in first_usage.values())
        again = invoke_pipeline_cli(sources, backend_store, cid, manifest, report, endpoint)
        assert again.returncode == 0 and len(requests) == count
    payload = json.loads(report.read_text())
    assert payload["status"] == "success"
    assert payload["token_usage"] == {}
    from app.services.video_graph.identity import policy_id

    assert payload["extractor_version"].endswith(f":{policy_id()}-none-none")
    result = payload["results"][str(cid)]
    assert result["construction"]["status"] == "already_current"
    assert result["verification"]["counts"] == dict(entities=2, relations=1, mentions=2, evidence=1)
    assert all(result["verification"]["checks"].values())


def test_failed_stage_resumes_durable_requests_with_new_worker(sources, backend_store, tmp_path):
    """Replay real baseline bodies, fail final review, and resume using production checkpoints."""
    from json import dumps, loads

    import httpx
    from app.services.video_graph.identity import stable_hash

    cid = seed_cached_case(sources)
    baseline = json.loads((ROOT / "data/video_graph_runs/token-baseline-20261106.json").read_text())
    responses = {}
    for trace in baseline["results"]["EG003"]["cache_trace"]:
        envelope = json.loads(
            (
                ROOT
                / "data/video_graph_runs/token-baseline-responses-20261106"
                / (trace["request_hash"] + ".json")
            ).read_text()
        )
        responses[stable_hash(envelope["request"])] = envelope["response"]
    observed, failed, mismatches = [], [], []

    def post(url, *, json, **kwargs):
        body = json
        system = body["messages"][0]["content"]
        request = httpx.Request("POST", "https://test.invalid")
        payload = loads(body["messages"][1]["content"])
        if (
            system.lstrip().startswith("独立对照完整current_chunk")
            and payload.get("format_correction")
            and len(failed) < 2
        ):
            failed.append(True)
            return httpx.Response(500, request=request)
        if system.startswith("判断材料是否与无人机"):
            response = {"relevant": True, "reason": "UAV fixture"}
        else:
            if stable_hash(body) not in responses:
                mismatches.append(body)
            assert stable_hash(body) in responses, "Unexpected request must not be fabricated"
            response = responses[stable_hash(body)]
        observed.append(stable_hash(body))
        return httpx.Response(
            200,
            request=request,
            json={
                "choices": [{"message": {"content": dumps(response)}}],
            },
        )

    first = LLMVideoExtractor(Settings())
    with (
        patch("app.services.video_graph.extraction.httpx.post", side_effect=post),
        patch("app.services.video_graph.extraction.time.sleep"),
    ):
        events = run(sources, first, tmp_path, backend_store, content_ids=[cid])
        assert events[-1]["status"] == "incomplete"
        assert len(observed) == 5 and len(failed) == 2  # topic + four successful chunk requests
        resumed = LLMVideoExtractor(Settings())  # No process-local cache survives.
        events = run(sources, resumed, tmp_path, backend_store, retry_failed=True)
    assert events[-1]["status"] == "success", (events, mismatches)
    assert len(observed) == 6  # Retry calls only the last review's evidence-format repair.
    report = json.loads((tmp_path / "report.json").read_text())
    usage = report["token_usage"]
    assert sum(row.get("checkpoint_replays", 0) for row in usage.values()) == 4
    assert sum(row.get("http_attempts", 0) for row in usage.values()) == 1
    assert (
        sum(row.get("http_attempts", 0) for row in report["cumulative_token_usage"].values()) == 8
    )
    assert report["usage_history_complete"]
    assert first.request_checkpoint is None and resumed.request_checkpoint is None
    result = next(e for e in events if e["stage"] == "pipeline_verification")
    assert all(result["checks"].values())
    with Session(sources.engine) as session:
        snapshot = pipeline._active_snapshot(session, cid, backend_store)
        assert "request_checkpoints" not in snapshot["output"]
