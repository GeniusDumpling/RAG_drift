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


def quality_output(m, *, attributes=None, modality="actual", scope_quote=None):
    from app.services.video_graph_quality import review_extraction

    data = extraction()
    data.entities[0].surface_text = "drone"
    data.entities[0].entity_type = "product"
    data.entities[0].attributes = attributes or {}
    data.relations[0].quote = m.chunks[0].text
    response = {
        "entities": [
            {
                "key": "s",
                "accepted": True,
                "canonical_name": "drone",
                "identity_scope": "video",
                "identity_quote": "drone",
                "identity_source": "current_chunk",
                "reason": "local device",
                "accepted_attributes": list(attributes or {}),
            },
            {
                "key": "o",
                "accepted": True,
                "canonical_name": "GPS",
                "identity_scope": "global",
                "identity_quote": "GPS",
                "identity_source": "current_chunk",
                "reason": "named technology",
            },
        ],
        "relations": [
            {"index": 0, "supported": True, "quote": m.chunks[0].text, "reason": "explicit use",
             "basis": "use", "modality": modality, "scope_quote": scope_quote}
        ],
    }
    data = review_extraction(lambda *_: response, m, m.chunks[0], "", data, "")
    return dict(
        assessment=dict(relevant=True, reason="fixture"),
        chunks={str(m.chunks[0].id): data.model_dump()},
        chunk_fingerprints={str(m.chunks[0].id): chunk_fingerprint(m.chunks[0])},
    )


def test_v2_generic_scope_persistence_and_contextual_retrieval(store):
    from app.services.video_graph_identity import extraction_version

    version = extraction_version("video-graph-v2", "fixture")
    videos = [material("drone uses GPS.") for _ in range(2)]
    for m in videos:
        store.enqueue(m, version)
        build, token = store.claim(version, [m.id])
        assert store.activate(m, build["id"], token, quality_output(m)) == "success"
    rows = store.read(
        "MATCH (e:VGEntity {canonical_name:'drone'}) RETURN e.id AS id,e.scope_key AS scope"
    )
    assert len(rows) == 2 and len({r["id"] for r in rows}) == 2
    q = GraphQueries(store)
    assert not q.retrieve_context(None, GraphRetrieveRequest(query="drone")).evidence
    assert q.retrieve_context(
        None, GraphRetrieveRequest(query="drone", content_item_id=videos[0].id)
    ).evidence
    assert len(q.retrieve_context(None, GraphRetrieveRequest(query="GPS")).evidence) == 2


def test_v2_retrieval_preserves_conditional_scope(store):
    from app.services.video_graph_identity import extraction_version

    m = material("After setup, drone uses GPS.")
    version = extraction_version("video-graph-v2", "fixture")
    store.enqueue(m, version)
    build, token = store.claim(version, [m.id])
    result = quality_output(m, modality="conditional", scope_quote=m.chunks[0].text)
    assert store.activate(m, build["id"], token, result) == "success"
    q = GraphQueries(store)
    context = q.retrieve_context(None, GraphRetrieveRequest(query="GPS"))
    assert context.evidence[0].qualifiers["scope"] == m.chunks[0].text
    assert "conditional" in context.context and "适用条件" in context.context
    evidence = q.relation_evidence(None, context.evidence[0].relation_id)
    assert evidence["items"][0].qualifiers == context.evidence[0].qualifiers


def test_v2_refuses_unreviewed_cache_and_keeps_old_projection(store):
    from app.services.video_graph_identity import extraction_version

    m = material()
    old = publish(store, m)
    version = extraction_version("video-graph-v2", "fixture")
    store.enqueue(m, version)
    build, token = store.claim(version, [m.id])
    assert "chunks" not in decode(build["output_json"])
    with pytest.raises(ValueError, match="审核凭据"):
        store.activate(m, build["id"], token, output(m))
    assert (
        store.read("MATCH (v:VGVideo {id:$id}) RETURN v.active_build_id AS id", id=str(m.id))[0][
            "id"
        ]
        == old
    )


def test_v2_context_change_does_not_reuse_old_review(store):
    from dataclasses import replace

    from app.services.video_graph_identity import extraction_version

    m = material("drone uses GPS.")
    version = extraction_version("video-graph-v2", "fixture")
    store.enqueue(m, version)
    build, token = store.claim(version, [m.id])
    assert store.activate(m, build["id"], token, quality_output(m)) == "success"
    store.enqueue(replace(m, title="A different model"), version)
    pending, _ = store.claim(version, [m.id])
    assert not decode(pending["output_json"]).get("chunks")


def test_backfill_plan_and_resume_across_real_databases(store, tmp_path):
    from app.services.video_graph import load_material
    from app.services.video_graph_backfill import apply_plan, create_plan, save_plan
    from app.services.video_graph_identity import extraction_version
    from app.services.video_graph_quality import review_extraction
    from sqlalchemy.orm import Session

    from backend.tests.test_video_graph import TestVideoGraphPersistence

    if not os.environ.get("VIDEO_GRAPH_TEST_DATABASE_URL"):
        pytest.skip("isolated PostgreSQL URL required for cross-database workflow")
    source = TestVideoGraphPersistence()
    source.setUp()
    try:
        content_id = source.seed_video()
        with Session(source.engine) as session:
            m = load_material(session, content_id)
        old = publish(store, m)

        class QualityExtractor:
            version = extraction_version("video-graph-v2", "fixture")

            def assess(self, material):
                from app.schemas.video_graph import VideoAssessment

                return VideoAssessment(relevant=True, reason="fixture")

            def extract(self, material, chunk, context):
                verdict = {
                    "entities": [
                        dict(
                            key=key,
                            accepted=True,
                            canonical_name=name,
                            identity_scope="global",
                            identity_quote=name,
                            identity_source="current_chunk",
                            reason="explicit",
                        )
                        for key, name in [("s", "PX4"), ("o", "GPS")]
                    ],
                    "relations": [
                        dict(index=0, supported=True, quote=chunk.text, reason="explicit",
                             basis="use", modality="actual")
                    ],
                }
                return review_extraction(
                    lambda *_: verdict, material, chunk, context, extraction(), ""
                )

        extractor = QualityExtractor()
        before = store.read("MATCH (b:VGBuild) RETURN count(b) AS n")[0]["n"]
        plan = create_plan(source.engine, extractor.version, store=store, content_ids=[content_id])
        assert store.read("MATCH (b:VGBuild) RETURN count(b) AS n")[0]["n"] == before
        assert plan["items"][0]["baseline_active_id"] == old
        path = tmp_path / "real-backfill.json"
        save_plan(path, plan)
        events = list(
            apply_plan(source.engine, extractor, path, store=store, limit=1, batch_size=1)
        )
        assert events[-1]["results"] == {"success": 1}
        current = store.read(
            "MATCH (v:VGVideo {id:$id}) MATCH (b:VGBuild {id:v.active_build_id}) "
            "RETURN b.id AS id,b.extractor_version AS version",
            id=str(content_id),
        )[0]
        assert current["id"] != old and current["version"] == extractor.version
        assert store.read("MATCH (b:VGBuild {id:$id}) RETURN b.status AS status", id=old)
        resumed = list(apply_plan(source.engine, extractor, path, store=store, limit=1))
        assert resumed[0]["selected"] == 0
        assert resumed[-1]["results"] == {"already_current": 1}
    finally:
        source.tearDown()


def test_backfill_active_version_guard_is_atomic(store):
    m = material()
    old = publish(store, m)
    with pytest.raises(ValueError, match="有效版本已变化"):
        store.enqueue(m, "new-version", expected_active_id="wrong-baseline")
    assert store.read("MATCH (b:VGBuild) RETURN count(b) AS n")[0]["n"] == 1
    assert (
        store.read("MATCH (v:VGVideo {id:$id}) RETURN v.active_build_id AS id", id=str(m.id))[0][
            "id"
        ]
        == old
    )


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
