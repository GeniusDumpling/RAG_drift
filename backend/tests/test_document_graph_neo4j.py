"""Real isolated Neo4j lifecycle and evidence-link mutation tests (synthetic model fixtures)."""

import json
import os
from types import SimpleNamespace

import pytest
from app.services.document_graph import queries
from app.services.document_graph.extraction import policy_id
from app.services.document_graph.pipeline import run_pipeline
from app.services.document_graph.storage import DocumentGraphStore
from app.services.video_graph.material import LeaseLostError
from test_document_graph import approved, source  # noqa: F401

# ruff: noqa: F811

pytestmark = pytest.mark.no_db


def write(store, cypher, **params):
    with store.transaction() as tx:
        return tx.run(cypher, **params).data()


@pytest.fixture
def store(source):
    uri = os.getenv("DOCUMENT_GRAPH_TEST_NEO4J_URI")
    if not uri:
        pytest.skip("set DOCUMENT_GRAPH_TEST_NEO4J_URI to isolated localhost:45969")
    if uri != "bolt://localhost:45969":
        raise RuntimeError("refusing to reset non-isolated Neo4j")
    settings = SimpleNamespace(
        document_graph_neo4j_uri=uri,
        document_graph_neo4j_user="neo4j",
        document_graph_neo4j_password="document-pilot-test-password",
        document_graph_neo4j_database="neo4j",
        document_graph_artifacts_dir=source[0].parent,
    )
    with DocumentGraphStore(settings) as store:
        write(store, "MATCH (n) DETACH DELETE n")
        store.initialize()
        yield store
        write(store, "MATCH (n) DETACH DELETE n")


async def publish(store, source):
    root, b = source
    m = b["manuals"][0]
    sid = m["id"]
    bid = store.enqueue(m, b, root, policy_id())
    _, token = store.claim(sid, bid)
    result = await approved(source)
    output = dict(cases={"C1": result})
    store.checkpoint(sid, bid, token, output)
    store.publish(sid, bid, token, b, output)
    return sid, bid, output


async def test_atomic_publish_query_asset_and_idempotence(store, source):
    sid, bid, _ = await publish(store, source)
    check = queries.verify(store, sid, bid)
    assert check["verified"], check
    items = queries.retrieve(store, "Camera", source_id=sid)["items"]
    assert len(items) == 1 and items[0]["evidence"][0]["images"]
    image = items[0]["evidence"][0]["images"][0]
    path, record = queries.asset(store, sid, image["asset_id"])
    assert path.read_bytes() and record["sha256"] == image["sha256"]
    root, b = source
    assert store.enqueue(b["manuals"][0], b, root, policy_id()) == bid
    assert store.claim(sid, bid) is None
    assert queries.verify(store, sid, bid)["verified"]


async def test_checkpoint_recovery_lease_takeover_and_stale_worker(store, source):
    root, b = source
    m = b["manuals"][0]
    sid = m["id"]
    bid = store.enqueue(m, b, root, policy_id())
    _, old = store.claim(sid, bid)
    output = dict(cases={"C1": await approved(source)})
    store.checkpoint(sid, bid, old, output)
    write(store, "MATCH (b:DGBuild {id:$id}) SET b.lease_until=0", id=bid)
    recovered, new = store.claim(sid, bid)
    assert json.loads(recovered["output_json"]) == output and new != old
    with pytest.raises(LeaseLostError):
        store.checkpoint(sid, bid, old, output)
    with pytest.raises(LeaseLostError):
        store.publish(sid, bid, old, b, output)
    store.publish(sid, bid, new, b, output)
    assert queries.verify(store, sid, bid)["verified"]


async def test_failed_new_version_keeps_previous_queryable(store, source):
    sid, old, _ = await publish(store, source)
    root, b = source
    bid = store.enqueue(b["manuals"][0], b, root, "new-policy")
    _, token = store.claim(sid, bid)
    with pytest.raises(ValueError):
        store.publish(sid, bid, token, b, dict(cases={}))
    assert store.status(sid)["s"]["active_build_id"] == old
    assert queries.retrieve(store, "Camera", source_id=sid)["items"]
    store.fail(sid, bid, token, dict(cases={}), "fixture-error")
    assert queries.verify(store, sid)["verified"]
    assert not queries.verify(store, sid, bid)["verified"]
    assert store.claim(sid, bid) is None
    store.enqueue(b["manuals"][0], b, root, "new-policy", retry_failed=True)
    assert store.claim(sid, bid)


async def test_transaction_rollback_leaves_no_partial_projection(store, source, monkeypatch):
    root, b = source
    m = b["manuals"][0]
    sid = m["id"]
    bid = store.enqueue(m, b, root, policy_id())
    _, token = store.claim(sid, bid)
    original = store._case

    def explode(*args):
        original(*args)
        raise ValueError("after writes")

    monkeypatch.setattr(store, "_case", explode)
    with pytest.raises(ValueError):
        store.publish(sid, bid, token, b, dict(cases={"C1": await approved(source)}))
    assert not store.status(sid)["s"].get("active_build_id")
    assert not store.read("MATCH (f:DGFact) RETURN f")
    assert not store.read("MATCH (p:DGPage) RETURN p")


@pytest.mark.parametrize(
    "mutation",
    [
        "MATCH (e:DGEvidence) SET e.quote='wrong'",
        "MATCH (e:DGEvidence)-[r:CITES_IMAGE]->() DELETE r",
        "MATCH (e:DGEvidence)-[r:CITES_TEXT]->() DELETE r",
        "MATCH (m:DGMention)-[r:REFERS_TO]->() DELETE r",
        "MATCH (l:DGLine) SET l.text='wrong'",
        "MATCH (r:DGRegion) SET r.bbox=[0,0,1,1]",
        "MATCH (f:DGFact)-[r:SUBJECT]->() DELETE r",
    ],
)
async def test_readback_rejects_actual_graph_mutations(store, source, mutation):
    sid, _, _ = await publish(store, source)
    write(store, mutation)
    assert not queries.verify(store, sid)["verified"]
    with pytest.raises(ValueError):
        queries.retrieve(store, "", source_id=sid)


async def test_withdrawal_hides_facts_and_assets_and_restore(store, source):
    sid, _, _ = await publish(store, source)
    store.withdraw(sid)
    assert queries.retrieve(store, "", source_id=sid)["items"] == []
    with pytest.raises(LookupError):
        queries.asset(store, sid, "0" * 64)
    store.withdraw(sid, restore=True)
    assert queries.verify(store, sid)["verified"]


async def test_corrupt_disk_blocks_publish_and_retrieval(store, source):
    root, b = source
    sid, _, _ = await publish(store, source)
    (root / next(iter(b["assets"].values()))["storage_key"]).write_bytes(b"corrupted")
    with pytest.raises(ValueError):
        queries.retrieve(store, "", source_id=sid)


async def test_pipeline_resumes_cases_without_model_calls(store, source, tmp_path, monkeypatch):
    from app.services.document_graph import pipeline

    root, b = source
    for m in b["manuals"]:
        bid = store.enqueue(m, b, root, policy_id())
        _, token = store.claim(m["id"], bid)
        case = next(c for c in b["cases"] if c["source_id"] == m["id"])
        i = b["cases"].index(case)
        store.checkpoint(m["id"], bid, token, dict(cases={case["id"]: await approved(source, i)}))
        write(store, "MATCH (b:DGBuild {id:$id}) SET b.lease_until=0", id=bid)

    async def no_model(*args):
        raise AssertionError("should resume checkpoint")

    monkeypatch.setattr(pipeline, "extract", no_model)
    report = await run_pipeline(
        SimpleNamespace(
            vlm_base_url="https://test.invalid", vlm_api_key="fixture", vlm_model="fixture"
        ),
        store,
        root,
        tmp_path / "report.json",
        cache=tmp_path / "cache",
        cache_only=True,
    )
    assert report["status"] == "success", report
    assert all(r["trace"] == [] for r in report["manuals"].values())


async def test_read_only_api_returns_validated_image_bytes(store, source, monkeypatch):
    from app.api import document_graph as api
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    sid, _, _ = await publish(store, source)
    app = FastAPI()
    app.include_router(api.router)
    # Keep the real isolated database; only replace connection settings.
    cfg = SimpleNamespace(
        document_graph_neo4j_uri="bolt://localhost:45969",
        document_graph_neo4j_user="neo4j",
        document_graph_neo4j_password="document-pilot-test-password",
        document_graph_neo4j_database="neo4j",
        document_graph_artifacts_dir=source[0].parent,
    )
    monkeypatch.setattr(api, "get_settings", lambda: cfg)
    with TestClient(app) as client:
        response = client.get("/document-graph/retrieve", params=dict(q="Camera", source_id=sid))
        assert response.status_code == 200, response.text
        item = response.json()["items"][0]["evidence"][0]["images"][0]
        image = client.get(item["url"])
        assert image.status_code == 200
        assert image.headers["etag"] == f'"{item["sha256"]}"'
        from app.services.document_graph.source import sha256

        assert sha256(image.content) == item["sha256"]
        assert client.get(f'/document-graph/assets/{sid}/{"0"*64}').status_code == 404
        store.withdraw(sid)
        assert client.get(item["url"]).status_code == 404


async def test_obsolete_target_can_be_reselected_and_resumed(store, source):
    root, b = source
    m = b["manuals"][0]
    sid = m["id"]
    first = store.enqueue(m, b, root, policy_id())
    _, old = store.claim(sid, first)
    store.enqueue(m, b, root, "second-policy")
    with pytest.raises(LeaseLostError):
        store.checkpoint(sid, first, old, {})
    assert store.enqueue(m, b, root, policy_id()) == first
    assert store.claim(sid, first)
