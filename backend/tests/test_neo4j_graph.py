"""Real Neo4j integration tests require an explicitly selected EMPTY test graph."""

import importlib.util
import os
import uuid
from contextlib import nullcontext
from pathlib import Path
from unittest.mock import patch

import pytest
from app.core.config import Settings
from app.schemas.video_graph import ChunkExtraction, GraphRetrieveRequest
from app.services.neo4j_graph.queries import GraphQueries
from app.services.neo4j_graph.store import GraphStore, decode, now_ms
from app.services.video_graph import LeaseLostError, VideoChunk, VideoMaterial, chunk_fingerprint

pytestmark = pytest.mark.no_db


@pytest.fixture
def store():
    uri = os.environ.get("NEO4J_TEST_URI")
    if not uri:
        pytest.skip("NEO4J_TEST_URI must explicitly select an empty test graph")
    settings = Settings(
        NEO4J_URI=uri,
        NEO4J_PASSWORD=os.environ.get("NEO4J_TEST_PASSWORD"),
        NEO4J_DATABASE=os.environ.get("NEO4J_TEST_DATABASE", "neo4j"),
    )
    with GraphStore(settings) as graph:
        count = graph.read(
            'MATCH (n) WHERE any(l IN labels(n) WHERE l STARTS WITH "VG") RETURN count(n) AS n'
        )[0]["n"]
        if count:
            pytest.fail("Refusing tests: selected Neo4j graph already contains VG nodes")
        graph.initialize()
        try:
            yield graph
        finally:
            # The fixture required an empty graph; only this suite owns these prefixed nodes.
            with graph.transaction() as tx:
                tx.run(
                    'MATCH (n) WHERE any(l IN labels(n) WHERE l STARTS WITH "VG") DETACH DELETE n'
                ).consume()


def material(text="PX4 uses GPS.", *, id=None, chunk_id=None):
    return VideoMaterial(
        id or uuid.uuid4(),
        "youtube:test",
        "Test video",
        "https://www.youtube.com/watch?v=test",
        "summary",
        "manual",
        "en",
        (VideoChunk(chunk_id or uuid.uuid4(), text, "transcript_segment", 0, 10),),
    )


def extraction():
    return ChunkExtraction.model_validate(
        dict(
            entities=[
                dict(key="s", surface_text="PX4", entity_type="software"),
                dict(key="o", surface_text="GPS", entity_type="technology"),
            ],
            relations=[
                dict(
                    subject="s",
                    object="o",
                    predicate="uses",
                    quote="PX4 uses GPS.",
                    assertion="asserted",
                    confidence=0.9,
                    qualifiers={},
                )
            ],
        )
    )


def output(m):
    return dict(
        assessment=dict(relevant=True, reason="test"),
        chunks={str(m.chunks[0].id): extraction().model_dump()},
        chunk_fingerprints={str(m.chunks[0].id): chunk_fingerprint(m.chunks[0])},
    )


def publish(store, m):
    store.enqueue(m, "test-v1")
    build, token = store.claim("test-v1", [m.id])
    assert store.activate(m, build["id"], token, output(m)) == "success"
    return build["id"]


def test_active_graph_evidence_and_retrieval(store):
    m = material()
    publish(store, m)
    q = GraphQueries(store)
    graph = q.get_graph(None)
    assert len(graph.nodes) == 2 and len(graph.edges) == 1
    assert q.list_entities(None, q="px4")["total"] == 1
    evidence = q.relation_evidence(None, uuid.UUID(graph.edges[0].id))
    assert evidence["total"] == 1 and evidence["items"][0].quote == "PX4 uses GPS."
    result = q.retrieve_context(None, GraphRetrieveRequest(query="PX4 uses what?"))
    assert len(result.evidence) == 1 and "[1]" in result.context
    assert q.get_graph(None, max_nodes=2, max_edges=1).edges[0].evidence_count == 1
    other = material()
    publish(store, other)
    assert q.get_graph(None).edges[0].evidence_count == 2
    store.withdraw(m.id)
    assert q.get_graph(None).edges[0].evidence_count == 1
    assert not q.get_graph(None, content_id=m.id).nodes
    store.withdraw(m.id, restore=True)
    assert store.enqueue(m, "test-v1") == "queued"
    b, t = store.claim("test-v1", [m.id])
    assert decode(b["output_json"])["chunks"]
    store.activate(m, b["id"], t, decode(b["output_json"]))
    assert q.get_graph(None).edges[0].evidence_count == 2


def test_partial_versions_and_stale_worker(store):
    m = material()
    old = publish(store, m)
    new = VideoMaterial(
        m.id,
        m.source_key,
        m.title,
        m.url,
        "changed",
        m.caption_source,
        m.caption_language,
        (*m.chunks, VideoChunk(uuid.uuid4(), "invalid", "transcript_segment", 10, 20)),
    )
    store.enqueue(new, "test-v1")
    b, t = store.claim("test-v1")
    data = decode(b["output_json"])
    assert str(m.chunks[0].id) in data["chunks"]
    data.update(
        assessment=dict(relevant=True, reason="test"),
        chunk_errors={str(new.chunks[1].id): dict(code="invalid")},
    )
    assert store.activate(new, b["id"], t, data) == "partial_success"
    assert (
        store.read("MATCH (v:VGVideo {id:$id}) RETURN v.active_build_id AS id", id=str(m.id))[0][
            "id"
        ]
        == old
    )
    assert store.enqueue(new, "test-v1") == "unchanged"
    store.enqueue(new, "test-v1", retry_partial=True)
    b, t = store.claim("test-v1")
    with store.transaction() as tx:
        tx.run(
            "MATCH (b:VGBuild {id:$id}) SET b.lease_until=$past", id=b["id"], past=now_ms() - 1
        ).consume()
    newer, token = store.claim("test-v1")
    with pytest.raises(LeaseLostError):
        store.checkpoint(b["id"], m.id, t, data)
    data["chunks"][str(new.chunks[1].id)] = {"entities": [], "relations": []}
    data["chunk_errors"] = {}
    assert store.activate(new, newer["id"], token, data) == "success"
    assert store.claim("test-v1") is None


def test_first_partial_all_invalid_and_source_change(store):
    m = material()
    m = VideoMaterial(
        m.id,
        m.source_key,
        m.title,
        m.url,
        m.summary,
        m.caption_source,
        m.caption_language,
        (*m.chunks, VideoChunk(uuid.uuid4(), "bad", "transcript_segment", 10, 20)),
    )
    store.enqueue(m, "test-v1")
    b, t = store.claim("test-v1")
    data = output(m)
    data["chunk_errors"] = {str(m.chunks[1].id): {"code": "invalid"}}
    assert store.activate(m, b["id"], t, data) == "partial_success"
    assert data["projection_activated"]
    store.enqueue(m, "test-v1", retry_partial=True)
    b, t = store.claim("test-v1")
    changed = material(id=m.id)
    assert store.activate(changed, b["id"], t, data) == "obsolete"
    store.enqueue(changed, "test-v1")
    b, t = store.claim("test-v1")
    data = dict(
        assessment=dict(relevant=True),
        chunks={},
        chunk_errors={str(changed.chunks[0].id): {"code": "invalid"}},
    )
    assert store.activate(changed, b["id"], t, data) == "failed"
    assert len(GraphQueries(store).get_graph(None).edges) == 1


def test_worker_checkpoint_resume_and_network_failure(store):
    from app.schemas.video_graph import VideoAssessment
    from app.services.neo4j_graph.worker import process_next

    m = material()

    class SourceSession:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def begin(self):
            return nullcontext()

    class Extractor:
        version = "test-v1"
        broken = True

        def assess(self, material):
            return VideoAssessment(relevant=True, reason="test")

        def extract(self, *args):
            if self.broken:
                raise RuntimeError("network")
            return extraction()

    extractor = Extractor()
    store.enqueue(m, extractor.version)
    with (
        patch("app.services.neo4j_graph.worker.Session", return_value=SourceSession()),
        patch("app.services.neo4j_graph.worker.load_material", return_value=m),
    ):
        assert process_next(None, store, extractor)["status"] == "failed"
        store.enqueue(m, extractor.version, retry_failed=True)
        extractor.broken = False
        assert process_next(None, store, extractor)["status"] == "success"
        assert process_next(None, store, extractor) is None


def migration_module():
    path = Path(__file__).resolve().parents[2] / "tools/video-knowledge-graph/migrate_neo4j.py"
    spec = importlib.util.spec_from_file_location("migration_neo4j", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_migration_preserves_properties_and_refuses_nonempty_graph(store):
    module = migration_module()
    vid, bid, eid, mid = [str(uuid.uuid4()) for _ in range(4)]
    snapshot = {
        "format": 1,
        "snapshot_id": "test",
        "tables": dict(
            states=[
                dict(
                    content_item_id=vid,
                    source_key="youtube:test",
                    withdrawn=False,
                    active_build_id=bid,
                    desired_build_id=bid,
                )
            ],
            videos=[dict(id=vid, title="test", canonical_url="https://example.com")],
            builds=[
                dict(
                    id=bid,
                    content_item_id=vid,
                    fingerprint="test",
                    extractor_version="test-v1",
                    status="success",
                    attempts=1,
                    output_json={"coverage": {"complete": True}},
                    created_at=1,
                )
            ],
            entities=[dict(id=eid, name="PX4", canonical_name="PX4", entity_type="software")],
            mentions=[
                dict(
                    id=mid,
                    entity_id=eid,
                    content_item_id=vid,
                    metadata_json={"video_graph_build_id": bid},
                )
            ],
            aliases=[],
            relations=[],
            evidence=[],
        ),
    }
    assert module.import_snapshot(store, snapshot)["verified"]
    assert module.import_snapshot(store, snapshot)["verified"]
    changed = dict(snapshot, snapshot_id="different")
    with pytest.raises(ValueError, match="不同快照"):
        module.import_snapshot(store, changed)
    with store.transaction() as tx:
        tx.run('MATCH (e:VGEntity) SET e.name="corrupted"').consume()
    with pytest.raises(ValueError, match="校验不一致"):
        module.verify(store, snapshot)


def test_api_neo4j_unavailable_is_503_without_sql_fallback():
    from app.api.deps import get_session
    from app.main import app
    from fastapi.testclient import TestClient
    from neo4j.exceptions import ServiceUnavailable

    class UnusedSession:
        async def run_sync(self, callback):
            raise AssertionError("Neo4j errors must not fall back to stale PostgreSQL data")

    async def source_dependency():
        yield UnusedSession()

    app.dependency_overrides[get_session] = source_dependency
    settings = Settings(VIDEO_GRAPH_BACKEND="neo4j", NEO4J_PASSWORD="test-only")
    try:
        with (
            patch("app.api.video_graph.get_settings", return_value=settings),
            patch(
                "app.services.neo4j_graph.store.GraphStore.ensure_ready",
                side_effect=ServiceUnavailable("test outage"),
            ),
            TestClient(app) as client,
        ):
            response = client.get("/video-graph/graph")
            assert response.status_code == 503
            assert response.json()["detail"] == "Neo4j 图谱不可用，请检查连接及迁移状态"
    finally:
        app.dependency_overrides.pop(get_session, None)


def test_concurrent_claims_have_one_owner(store):
    from concurrent.futures import ThreadPoolExecutor

    m = material()
    store.enqueue(m, "test-v1")
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(lambda _: store.claim("test-v1"), range(4)))
    claimed = [row for row in results if row is not None]
    assert len(claimed) == 1
    build, token = claimed[0]
    assert store.activate(m, build["id"], token, output(m)) == "success"
