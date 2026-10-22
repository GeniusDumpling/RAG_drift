from __future__ import annotations

import os
import uuid
from pathlib import Path

import pytest

from .conftest import sample_guide


class FixedEmbedding:
    model_name = "ifixit-fixed-test-model"
    dimension = 8
    fail = False

    def embed(self, text):
        if self.fail:
            raise RuntimeError("simulated embedding outage")
        return [0.5] * self.dimension


def indexer():
    from tools.ifixit_rag.database import QdrantIndexer

    return QdrantIndexer(
        url="memory://ifixit-test-" + uuid.uuid4().hex,
        collection="ifixit-tests",
        embedding=FixedEmbedding(),
    )


def test_import_idempotent_raw_source_and_keyword_retrieval(db_session, make_bundle):
    from sqlalchemy import func, select, text

    from tools.ifixit_rag.database import ContentChunk, ContentItem, RawPage, import_bundle

    bundle = make_bundle()
    first = import_bundle(db_session, bundle)
    assert first["status"] == "success" and first["created"] == 1
    second = import_bundle(db_session, bundle)
    assert second["unchanged"] == 1 and second["chunks_created"] == 0
    assert db_session.scalar(select(func.count()).select_from(ContentItem)) == 1
    assert db_session.scalar(select(func.count()).select_from(RawPage)) == 1
    assert db_session.scalar(select(func.count()).select_from(ContentChunk)) == len(
        bundle["chunks"]
    )
    item = db_session.scalar(select(ContentItem))
    assert item.item_type == "repair_guide" and item.published_at.year == 2017
    assert item.metadata_json["steps"][0]["lines"][1]["semantic"] == "warning"
    assert db_session.scalar(text("SELECT search_tsv IS NOT NULL FROM content_items"))
    assert db_session.scalar(select(ContentChunk).where(ContentChunk.embed_text.ilike("%2.5 mm%")))


def test_real_qdrant_upsert_dimension_guard_and_removal(db_session, make_bundle, budget):
    url = os.environ.get("IFIXIT_TEST_QDRANT_URL")
    if not url:
        pytest.skip("set IFIXIT_TEST_QDRANT_URL for an isolated Qdrant collection test")
    from qdrant_client import QdrantClient

    from tools.ifixit_rag.database import QdrantIndexer, import_bundle, index_bundle

    collection = "ifixit_test_" + uuid.uuid4().hex
    client = QdrantClient(url=url)
    target = QdrantIndexer(url=url, collection=collection, embedding=FixedEmbedding())
    try:
        bundle = make_bundle()
        import_bundle(db_session, bundle)
        assert index_bundle(db_session, bundle, target, budget)["failed"] == 0
        assert client.count(collection_name=collection).count == len(bundle["chunks"])
        target.embedding.dimension = 4
        with pytest.raises(ValueError, match="dimension"):
            index_bundle(db_session, bundle, target, budget)
        assert client.count(collection_name=collection).count == len(bundle["chunks"])
        target.embedding.dimension = 8
        empty = make_bundle(sample_guide(steps=[], introduction_raw=""))
        import_bundle(db_session, empty)
        assert index_bundle(db_session, empty, target, budget)["failed"] == 0
        assert client.count(collection_name=collection).count == 0
    finally:
        if client.collection_exists(collection):
            client.delete_collection(collection)
        client.close()


def test_index_retries_outage_and_does_not_reembed_unchanged(db_session, make_bundle, budget):
    from sqlalchemy import select

    from tools.ifixit_rag.database import ContentChunk, import_bundle, index_bundle

    bundle = make_bundle()
    import_bundle(db_session, bundle)
    target = indexer()
    target.embedding.fail = True
    failed = index_bundle(db_session, bundle, target, budget)
    assert failed["failed"] == len(bundle["chunks"])
    assert all(c.embed_status == "failed" for c in db_session.scalars(select(ContentChunk)))
    target.embedding.fail = False
    retry = index_bundle(db_session, bundle, target, budget)
    assert retry["indexed"] == len(bundle["chunks"])
    unchanged = index_bundle(db_session, bundle, target, budget)
    assert unchanged["unchanged"] == len(bundle["chunks"])


def test_reorder_remove_and_cleanup_keeps_logical_ids(db_session, make_bundle, budget):
    from app.services.retrieval import _MEMORY_COLLECTIONS
    from sqlalchemy import select

    from tools.ifixit_rag.database import ContentChunk, import_bundle, index_bundle

    payload = sample_guide()
    payload["steps"].append({**payload["steps"][0], "stepid": 22, "orderby": 2})
    bundle = make_bundle(payload)
    import_bundle(db_session, bundle)
    target = indexer()
    index_bundle(db_session, bundle, target, budget)
    before = {
        c.chunk_metadata_json["logical_chunk_key"]: c.id
        for c in db_session.scalars(select(ContentChunk))
    }
    payload["steps"] = [{**payload["steps"][1], "orderby": 1}]
    updated_bundle = make_bundle(payload)
    result = import_bundle(db_session, updated_bundle)
    assert result["status"] == "success"
    assert result["chunks_obsolete"] == 1
    active = list(
        db_session.scalars(select(ContentChunk).where(ContentChunk.embed_status != "obsolete"))
    )
    assert all(before[c.chunk_metadata_json["logical_chunk_key"]] == c.id for c in active)
    result = index_bundle(db_session, updated_bundle, target, budget)
    assert result["failed"] == 0
    points = _MEMORY_COLLECTIONS[(target.url, target.collection)].points
    assert set(points) == {str(c.id) for c in active}
    # Re-import of the same bundle must not reset the successful vector metadata.
    import_bundle(db_session, updated_bundle)
    assert index_bundle(db_session, updated_bundle, target, budget)["indexed"] == 0
    assert all(c.token_count is not None for c in active)


def test_model_mismatch_and_missing_tokenizer(db_session, make_bundle, budget):
    from tools.ifixit_rag.chunking import Budget
    from tools.ifixit_rag.database import import_bundle, index_bundle

    bundle = make_bundle()
    import_bundle(db_session, bundle)
    target = indexer()
    with pytest.raises(ValueError, match="tokenizer"):
        index_bundle(db_session, bundle, target, Budget())
    assert index_bundle(db_session, bundle, target, budget)["failed"] == 0
    target.embedding.model_name = "another-model-same-dimension"
    with pytest.raises(ValueError, match="another embedding model"):
        index_bundle(db_session, bundle, target, budget)


def test_changed_content_outbox_survives_failed_deletion(
    db_session, make_bundle, budget, monkeypatch
):
    from sqlalchemy import select

    from tools.ifixit_rag.database import ContentChunk, import_bundle, index_bundle

    bundle = make_bundle()
    import_bundle(db_session, bundle)
    target = indexer()
    index_bundle(db_session, bundle, target, budget)
    payload = sample_guide(introduction_raw="A changed battery introduction.")
    updated = make_bundle(payload)
    import_bundle(db_session, updated)
    delete = target.delete_chunk

    def fail_delete(**kwargs):
        raise RuntimeError("simulated vector delete outage")

    monkeypatch.setattr(target, "delete_chunk", fail_delete)
    failed = index_bundle(db_session, updated, target, budget)
    assert failed["failed"]
    assert any(
        c.chunk_metadata_json["pending_vector_deletions"]
        for c in db_session.scalars(select(ContentChunk))
    )
    monkeypatch.setattr(target, "delete_chunk", delete)
    assert index_bundle(db_session, updated, target, budget)["failed"] == 0


def test_input_shrink_does_not_delete_guides(db_session, make_bundle):
    from sqlalchemy import func, select

    from tools.ifixit_rag.database import ContentItem, import_bundle

    import_bundle(db_session, make_bundle(sample_guide(1), sample_guide(2)))
    assert import_bundle(db_session, make_bundle(sample_guide(1)))["failed"] == 0
    assert db_session.scalar(select(func.count()).select_from(ContentItem)) == 2


def test_cross_store_cleanup_then_migration(db_session, make_bundle, budget):
    from app.services.retrieval import _MEMORY_COLLECTIONS

    from tools.ifixit_rag.database import cleanup_bundle, import_bundle, index_bundle

    bundle = make_bundle()
    import_bundle(db_session, bundle)
    old = indexer()
    new = indexer()
    assert index_bundle(db_session, bundle, old, budget)["failed"] == 0
    assert index_bundle(db_session, bundle, new, budget)["failed"] == len(bundle["chunks"])
    cleanup = cleanup_bundle(db_session, bundle, old.url)
    assert cleanup["cleaned"] == len(bundle["chunks"])
    assert not _MEMORY_COLLECTIONS[(old.url, old.collection)].points
    assert index_bundle(db_session, bundle, new, budget)["failed"] == 0
    assert len(_MEMORY_COLLECTIONS[(new.url, new.collection)].points) == len(bundle["chunks"])


def test_one_guide_failure_rolls_back_and_continues(db_session, make_bundle, monkeypatch):
    from tools.ifixit_rag import database

    bundle = make_bundle(sample_guide(1), sample_guide(2))
    original = database._import_guide

    def fail_one(session, source, run, guide, *args):
        if guide["guide_id"] == 1:
            raise RuntimeError("simulated invalid guide")
        return original(session, source, run, guide, *args)

    monkeypatch.setattr(database, "_import_guide", fail_one)
    result = database.import_bundle(db_session, bundle)
    assert result["status"] == "partial" and result["created"] == 1 and result["failed"] == 1
    monkeypatch.setattr(database, "_import_guide", original)
    retry = database.import_bundle(db_session, bundle)
    assert retry["created"] == 1 and retry["unchanged"] == 1 and retry["failed"] == 0


def test_accepted_upsert_timeout_keeps_point_for_future_cleanup(
    db_session,
    make_bundle,
    budget,
    monkeypatch,
):
    from app.services.retrieval import _MEMORY_COLLECTIONS

    from tools.ifixit_rag.database import import_bundle, index_bundle

    original_bundle = make_bundle()
    import_bundle(db_session, original_bundle)
    target = indexer()
    upsert = target.upsert_chunk

    def accepted_then_timeout(**kwargs):
        upsert(**kwargs)
        raise RuntimeError("server accepted write but reply timed out")

    monkeypatch.setattr(target, "upsert_chunk", accepted_then_timeout)
    assert index_bundle(db_session, original_bundle, target, budget)["failed"]
    empty = make_bundle(sample_guide(steps=[], introduction_raw=""))
    import_bundle(db_session, empty)
    monkeypatch.setattr(target, "upsert_chunk", upsert)
    assert index_bundle(db_session, empty, target, budget)["failed"] == 0
    assert not _MEMORY_COLLECTIONS[(target.url, target.collection)].points


def test_full_prepared_snapshot_import_twice(db_session):
    from sqlalchemy import func, select

    from tools.ifixit_rag.bundle import load_bundle
    from tools.ifixit_rag.database import ContentChunk, ContentItem, import_bundle

    path = os.environ.get("IFIXIT_TEST_BUNDLE")
    if not path:
        pytest.skip("set IFIXIT_TEST_BUNDLE for a full local-snapshot import test")
    bundle = load_bundle(Path(path))
    for raw in bundle["raw_guides"]:
        normalized = bundle["steps_by_doc"][raw["document_id"]]
        original = raw["payload"]["steps"]
        assert [s["source_step_id"] for s in normalized] == [s["stepid"] for s in original]
        assert [s["source_array_index"] for s in normalized] == list(range(len(original)))
    first = import_bundle(db_session, bundle)
    assert first["failed"] == 0, first["errors"][:2]
    assert first["created"] == len(bundle["guides"])
    second = import_bundle(db_session, bundle)
    assert second["failed"] == 0 and second["unchanged"] == len(bundle["guides"])
    assert second["chunks_created"] == second["chunks_updated"] == 0
    assert db_session.scalar(select(func.count()).select_from(ContentItem)) == len(bundle["guides"])
    assert db_session.scalar(select(func.count()).select_from(ContentChunk)) == len(
        bundle["chunks"]
    )
