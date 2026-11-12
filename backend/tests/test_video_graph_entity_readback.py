"""Readback accepts identity-equivalent labels but still detects projection corruption."""

import uuid
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest
from app.services.video_graph import pipeline
from app.services.video_graph.identity import stable_hash

pytestmark = pytest.mark.no_db


def verify_with_label(label, *, entity_type="product", entity_id="tool"):
    cid = uuid.uuid4()
    version, fingerprint = "video-graph-v2:test:policy-none-none", "source-fingerprint"
    snapshot = {
        "id": "build", "desired_id": "build", "version": version,
        "fingerprint": fingerprint, "status": "success", "withdrawn": False, "deleted": False,
    }
    nodes = {"tool": ("DJI screwdriver", "product"), "part": ("Battery", "component")}
    edges = {"relation": ("tool", "uses", "part", stable_hash({}))}
    graph = SimpleNamespace(
        nodes=[
            SimpleNamespace(id=entity_id, label=label, entity_type=entity_type, kind="entity"),
            SimpleNamespace(id="part", label="Battery", entity_type="component", kind="entity"),
        ],
        edges=[SimpleNamespace(
            id="relation", source="tool", target="part", predicate="uses",
            qualifiers={}, kind="relation",
        )],
        truncated=False,
    )
    queries = Mock()
    queries.get_graph.return_value = graph
    queries.retrieve_context.return_value = SimpleNamespace(
        context="source quote",
        evidence=[SimpleNamespace(
            relation_id="relation", content_item_id=cid, extractor_version=version,
            qualifiers={}, quote="source quote",
        )],
    )
    with (
        patch.object(pipeline, "Session"),
        patch.object(
            pipeline, "load_material", return_value=SimpleNamespace(fingerprint=fingerprint)
        ),
        patch.object(pipeline, "_active_snapshot", return_value=snapshot),
        patch.object(pipeline, "_expected_projection", return_value=(nodes, edges, set(), set())),
        patch.object(pipeline, "_stored_source_rows", return_value=(set(), set())),
        patch("app.services.video_graph.queries.GraphQueries", return_value=queries),
    ):
        result = pipeline.verify_projection(
            Mock(), cid, version, fingerprint, store=SimpleNamespace(unified=False)
        )
    return result, queries


@pytest.mark.parametrize("label", ["DJI Screwdriver", "dji   SCREWDRIVER", "ＤＪＩ screwdriver"])
def test_equivalent_identity_spelling_passes_readback_and_actual_retrieval(label):
    result, queries = verify_with_label(label)
    assert result["status"] == "verified"
    assert all(result["checks"].values())
    queries.retrieve_context.assert_called_once()


@pytest.mark.parametrize("label,entity_type,entity_id", [
    ("DJI Mini 4 Pro", "product", "tool"),
    ("DJIScrewdriver", "product", "tool"),
    ("DJI Screwdriver", "component", "tool"),
    ("DJI Screwdriver", "product", "other-tool"),
])
def test_changed_name_type_or_identity_still_fails(label, entity_type, entity_id):
    result, queries = verify_with_label(label, entity_type=entity_type, entity_id=entity_id)
    assert result["status"] == "verification_failed"
    assert not result["checks"]["entities_equal"]
    queries.retrieve_context.assert_not_called()
