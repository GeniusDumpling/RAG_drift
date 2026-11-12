"""Shared protocol and real Neo4j publication tests; fixtures do not measure model quality."""

import os
import uuid
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from app.schemas.video_graph import ChunkExtraction
from app.services.document_graph.extraction import policy_id
from app.services.document_graph.storage import DocumentGraphStore
from app.services.graph_storage import GraphDatabase
from app.services.knowledge_graph import queries
from app.services.knowledge_graph.schema import Entity, Evidence, Fact, Publication, source_key
from app.services.knowledge_graph.storage import UnifiedGraphStore, publish
from app.services.video_graph.material import VideoChunk, VideoMaterial, chunk_fingerprint
from app.services.video_graph.storage import GraphStore
from test_document_graph import approved, source  # noqa: F401

from backend.tests.video_graph_fixtures import reviewed_fixture

# ruff: noqa: F811
pytestmark = pytest.mark.no_db


def evidence():
    return Evidence(quote="explicit source", locator={"step_id": 3}, review_id="reviewed")


def publication(kind="video", external="one", assertion="asserted"):
    return Publication(
        source_type=kind,
        external_id=external,
        native_build_id="native",
        source_fingerprint="fingerprint",
        extractor_version="reviewed-v1",
        title="test",
        url="url",
        entities=[
            Entity(id="a", name="Drone", entity_type="product", scope="global"),
            Entity(id="b", name="GPS", entity_type="technology", scope="global"),
        ],
        facts=[
            Fact(
                subject="a",
                object="b",
                predicate="uses",
                assertion=assertion,
                evidence=[evidence()],
            )
        ],
    )


def test_claim_identity_preserves_assertion_conditions_and_evidence():
    a = publication().facts[0]
    assert a.id != a.model_copy(update={"assertion": "negated"}).id
    assert a.id != a.model_copy(update={"qualifiers": {"firmware": "v2"}}).id
    assert a.id == a.model_copy(update={"evidence": [evidence(), evidence()]}).id


def test_entity_case_variants_merge_but_different_scopes_do_not():
    from app.services.knowledge_graph.adapters import _merge_entity

    entities = {}
    a = Entity(id="same", name="Ground Control Station", entity_type="product", scope="global")
    _merge_entity(entities, a)
    _merge_entity(entities, a.model_copy(update={"name": "Ground control station"}))
    assert len(entities) == 1
    with pytest.raises(ValueError, match="身份"):
        _merge_entity(entities, a.model_copy(update={"scope": "model:other"}))


async def test_document_publication_survives_checkpoint_key_reordering(source):
    import copy

    from app.services.document_graph.source import save_json
    from app.services.knowledge_graph.adapters import document_publication
    from app.services.video_graph.identity import stable_hash

    root, bundle = source
    second = copy.deepcopy(bundle["cases"][0])
    second["id"] = "Z"
    bundle["cases"].append(second)
    bundle["source_hash"] = stable_hash({k: v for k, v in bundle.items() if k != "source_hash"})
    save_json(root / "source.json", bundle)
    a, z = await approved(source, 0), await approved(source, 2)
    manual = bundle["manuals"][0]
    first = document_publication(
        manual, bundle, "native", dict(cases={"Z": z, "C1": a}), policy_id(), root=root
    )
    reordered = document_publication(
        manual, bundle, "native", dict(cases={"C1": a, "Z": z}), policy_id(), root=root
    )
    assert first.payload_hash == reordered.payload_hash


def test_protocol_rejects_missing_endpoints_and_malformed_values():
    with pytest.raises(ValueError, match="端点"):
        publication().model_copy(update={"entities": []}).model_validate(
            {**publication().model_dump(), "entities": []}
        )
    with pytest.raises(ValueError):
        Fact(subject="a", predicate="has_parameter", value="898 g", evidence=[evidence()])
    with pytest.raises(ValueError):
        Fact(subject="a", predicate="uses", object="b", value="x", evidence=[evidence()])


def test_writer_revalidates_nested_models_before_any_database_mutation():
    p = publication()
    invalid = p.model_copy(update={"facts": [p.facts[0].model_copy(update={"value": "invalid"})]})
    tx = Mock()
    with pytest.raises(ValueError):
        publish(tx, invalid)
    tx.run.assert_not_called()


@pytest.mark.parametrize("store_type", [GraphStore, DocumentGraphStore])
def test_unified_target_is_shared_and_never_falls_back(monkeypatch, tmp_path, store_type):
    driver = Mock()
    monkeypatch.setattr(GraphDatabase, "driver", driver)
    cfg = SimpleNamespace(
        unified_graph_enabled=True,
        unified_graph_neo4j_uri="bolt://unified:7687",
        unified_graph_neo4j_user="shared",
        unified_graph_neo4j_password="shared-password",
        unified_graph_neo4j_database="graph",
        neo4j_uri="bolt://video:7687",
        neo4j_password="video-password",
        document_graph_neo4j_uri="bolt://document:7687",
        document_graph_neo4j_password="doc-password",
        document_graph_artifacts_dir=tmp_path,
    )
    with store_type(cfg) as store:
        assert store.database == "graph"
    assert driver.call_args.args == ("bolt://unified:7687",)
    cfg.unified_graph_neo4j_uri = None
    driver.reset_mock()
    with pytest.raises(ValueError, match="UNIFIED_GRAPH_NEO4J_URI"):
        store_type(cfg)
    driver.assert_not_called()


@pytest.fixture
def stores(source):
    uri = os.getenv("KNOWLEDGE_GRAPH_TEST_NEO4J_URI")
    if not uri:
        pytest.skip("requires dedicated localhost:45970 test Neo4j")
    if uri != "bolt://localhost:45970":
        raise RuntimeError("refusing to reset non-isolated shared graph")
    cfg = SimpleNamespace(
        unified_graph_enabled=True,
        unified_graph_neo4j_uri=uri,
        unified_graph_neo4j_user="neo4j",
        unified_graph_neo4j_password="unified-graph-test-password",
        unified_graph_neo4j_database="neo4j",
        document_graph_artifacts_dir=source[0].parent,
    )
    with GraphStore(cfg) as video, DocumentGraphStore(cfg) as doc, UnifiedGraphStore(cfg) as shared:
        with shared.transaction() as tx:
            tx.run("MATCH(n) DETACH DELETE n").consume()
        video.initialize()
        doc.initialize()
        try:
            yield SimpleNamespace(video=video, doc=doc, shared=shared, cfg=cfg)
        finally:
            with shared.transaction() as tx:
                tx.run("MATCH(n) DETACH DELETE n").consume()


def video_data():
    text = "Test drone 1 uses GPS."
    chunk = VideoChunk(uuid.uuid4(), text, "transcript_segment", 10, 20)
    m = VideoMaterial(
        uuid.uuid4(),
        "youtube:test",
        "Test drone 1",
        "https://youtu.be/test",
        "",
        "manual",
        "en",
        (chunk,),
    )
    draft = ChunkExtraction.model_validate(
        dict(
            entities=[
                dict(key="p", surface_text="Test drone 1", entity_type="product"),
                dict(key="g", surface_text="GPS", entity_type="technology"),
            ],
            relations=[
                dict(
                    subject="p",
                    object="g",
                    predicate="uses",
                    quote=text,
                    assertion="asserted",
                    confidence=0.8,
                )
            ],
        )
    )
    extraction = reviewed_fixture(m, chunk, draft)
    output = dict(
        assessment=dict(relevant=True, reason="fixture"),
        chunks={str(chunk.id): extraction.model_dump()},
        chunk_fingerprints={str(chunk.id): chunk_fingerprint(chunk)},
    )
    return m, output


def publish_video(stores, material=None, output=None, version="video-graph-v2:fixture"):
    if material is None:
        material, output = video_data()
    stores.video.enqueue(material, version)
    b, token = stores.video.claim(version, [material.id])
    assert stores.video.activate(material, b["id"], token, output) == "success"
    return material, output, b["id"]


async def publish_document(stores, source):
    root, bundle = source
    m = bundle["manuals"][0]
    bid = stores.doc.enqueue(m, bundle, root, policy_id())
    _, token = stores.doc.claim(m["id"], bid)
    stores.doc.publish(m["id"], bid, token, bundle, dict(cases={"C1": await approved(source)}))
    return m["id"], bid


async def test_both_native_publishers_share_entity_and_query_evidence(stores, source):
    m, _, _ = publish_video(stores)
    sid, _ = await publish_document(stores, source)
    assert queries.verify(stores.shared, source_key("video", m.id))["verified"]
    assert queries.verify(stores.shared, source_key("document", sid))["verified"]
    entities = stores.shared.read('MATCH(e:KGEntity {name:"Test drone 1"}) RETURN e.id AS id')
    assert len(entities) == 1
    result = queries.retrieve(stores.shared, entity_id=entities[0]["id"])
    assert {s["source_type"] for f in result["items"] for s in f["sources"]} == {
        "video",
        "document",
    }
    assert {
        e["locator"]["type"] for f in result["items"] for s in f["sources"] for e in s["evidence"]
    } == {"video", "document"}


def test_shared_facts_deduplicate_and_contradictions_keep_provenance(stores):
    with stores.shared.transaction() as tx:
        publish(tx, publication())
        publish(tx, publication("document"))
        publish(tx, publication("forum", "reply", "negated"))
    result = queries.retrieve(stores.shared)
    assert len(result["items"]) == 2
    assert sorted(len(f["sources"]) for f in result["items"]) == [1, 2]
    first = queries.retrieve(stores.shared, limit=1)
    second = queries.retrieve(stores.shared, limit=1, offset=1)
    assert first["items"][0]["id"] != second["items"][0]["id"]
    assert len(queries.retrieve(stores.shared, source_type="forum")["items"]) == 1


def test_republication_hides_old_revision_and_is_idempotent(stores):
    p = publication()
    with stores.shared.transaction() as tx:
        publish(tx, p)
        publish(tx, p)
    assert len(queries.retrieve(stores.shared)["items"]) == 1
    replacement = p.model_copy(update={"facts": [], "native_build_id": "replacement"})
    with stores.shared.transaction() as tx:
        publish(tx, replacement)
    assert queries.retrieve(stores.shared)["items"] == []
    assert queries.verify(stores.shared, p.source_id)["verified"]


async def test_withdraw_restore_is_source_scoped(stores, source):
    m, output, _ = publish_video(stores)
    sid, _ = await publish_document(stores, source)
    stores.doc.withdraw(sid)
    assert queries.retrieve(stores.shared, source_type="document")["items"] == []
    assert queries.retrieve(stores.shared, source_type="video")["items"]
    stores.doc.withdraw(sid, restore=True)
    assert queries.retrieve(stores.shared, source_type="document")["items"]
    stores.video.withdraw(m.id)
    assert not queries.retrieve(stores.shared, source_type="video")["items"]
    stores.video.withdraw(m.id, restore=True)
    assert not queries.retrieve(stores.shared, source_type="video")["items"]
    publish_video(stores, m, output)
    assert queries.retrieve(stores.shared, source_type="video")["items"]


def test_shared_failure_rolls_back_video_activation(stores, monkeypatch):
    from app.services.knowledge_graph import storage

    m, output, previous = publish_video(stores)
    shared_previous = queries.sources(stores.shared)[0]["active_build_id"]
    version = "video-graph-v2:replacement"
    stores.video.enqueue(m, version)
    build, token = stores.video.claim(version, [m.id])
    original = storage.publish

    def fail(tx, p):
        original(tx, p)
        raise RuntimeError("injected shared failure")

    monkeypatch.setattr(storage, "publish", fail)
    with pytest.raises(RuntimeError, match="injected"):
        stores.video.activate(m, build["id"], token, output)
    assert stores.video.active_snapshot(m.id)["id"] == previous
    assert queries.sources(stores.shared)[0]["active_build_id"] == shared_previous
    assert queries.verify(stores.shared, source_key("video", m.id))["verified"]


async def test_shared_failure_rolls_back_document_projection(stores, source, monkeypatch):
    from app.services.knowledge_graph import storage

    root, bundle = source
    m = bundle["manuals"][0]
    bid = stores.doc.enqueue(m, bundle, root, policy_id())
    _, token = stores.doc.claim(m["id"], bid)
    original = storage.publish

    def fail(tx, p):
        original(tx, p)
        raise RuntimeError("injected shared failure")

    monkeypatch.setattr(storage, "publish", fail)
    with pytest.raises(RuntimeError):
        stores.doc.publish(m["id"], bid, token, bundle, dict(cases={"C1": await approved(source)}))
    assert not stores.doc.status(m["id"])["s"].get("active_build_id")
    assert queries.sources(stores.shared) == []
    assert stores.shared.read("MATCH(p:DGPage) RETURN count(p) AS n")[0]["n"] == 0


@pytest.mark.parametrize(
    "mutation",
    [
        "MATCH(e:KGEvidence)-[r:KG_SUPPORTS]->() DELETE r",
        "MATCH(f:KGFact)-[r:KG_OBJECT]->() DELETE r WITH f MATCH(e:KGEntity {id:'a'}) "
        "MERGE(f)-[:KG_OBJECT]->(e)",
    ],
)
def test_tampered_evidence_or_endpoints_refuse_retrieval(stores, mutation):
    with stores.shared.transaction() as tx:
        publish(tx, publication())
        tx.run(mutation).consume()
    assert not queries.verify(stores.shared, "video:one")["verified"]
    with pytest.raises(ValueError, match="校验失败"):
        queries.retrieve(stores.shared)


def test_native_binding_and_refresh_are_checked(stores):
    from app.services.knowledge_graph.adapters import video_publication

    m, output, bid = publish_video(stores)
    expected = video_publication(m, bid, output, "video-graph-v2:fixture")
    assert queries.verify(stores.shared, expected.source_id, expected)["verified"]
    with stores.shared.transaction() as tx:
        publish(tx, expected.model_copy(update={"extractor_version": "stale"}))
    assert not queries.verify(stores.shared, expected.source_id, expected)["verified"]
    stores.video.refresh_shared(m)
    assert queries.verify(stores.shared, expected.source_id, expected)["verified"]
    assert stores.video.active_snapshot(m.id)["id"] == bid


def test_conflicting_entity_id_rolls_back_source_publication(stores):
    p = publication()
    with stores.shared.transaction() as tx:
        publish(tx, p)
    bad = p.model_copy(
        update={
            "source_type": "forum",
            "external_id": "bad",
            "entities": [
                p.entities[0],
                p.entities[1].model_copy(update={"entity_type": "protocol"}),
            ],
        }
    )
    with pytest.raises(ValueError, match="冲突"):
        with stores.shared.transaction() as tx:
            publish(tx, bad)
    assert {s["id"] for s in queries.sources(stores.shared)} == {p.source_id}


async def test_http_query_and_native_image_endpoints(stores, source, monkeypatch):
    from app.api import document_graph, knowledge_graph
    from app.knowledge_graph_main import app
    from fastapi.testclient import TestClient

    publish_video(stores)
    await publish_document(stores, source)
    monkeypatch.setattr(knowledge_graph, "get_settings", lambda: stores.cfg)
    monkeypatch.setattr(document_graph, "get_settings", lambda: stores.cfg)
    with TestClient(app) as client:
        response = client.get("/knowledge-graph/retrieve")
        assert response.status_code == 200
        assert len(response.json()["items"]) == 2
        document = next(
            f for f in response.json()["items"] if f["sources"][0]["source_type"] == "document"
        )
        source_id = document["sources"][0]["external_id"]
        asset_id = document["sources"][0]["evidence"][0]["locator"]["regions"][0]["asset_id"]
        assert client.get(f"/document-graph/assets/{source_id}/{asset_id}").status_code == 200
        assert client.get("/knowledge-graph/retrieve?limit=101").status_code == 422
