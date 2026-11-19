"""Canvas contract tests; the existing forum verifier covers native/source integrity."""

from types import SimpleNamespace

import pytest
from app.services.forum_graph import display
from app.services.knowledge_graph.schema import Entity, Evidence, Fact, Publication

pytestmark = pytest.mark.no_db


@pytest.fixture
def view(monkeypatch):
    proof = Evidence(
        quote="Try calibration.",
        review_id="reviewed",
        locator=dict(
            url="https://discuss.px4.io/t/7/2",
            post_id=102,
            post_number=2,
            author="helper",
            source_role="claim",
            act="recommendation",
        ),
    )
    p = Publication(
        source_type="forum",
        external_id="discuss.px4.io:7",
        native_build_id="build",
        source_fingerprint="fingerprint",
        extractor_version="fixture",
        title="GPS drift",
        url="https://discuss.px4.io/t/7",
        metadata={"content_item_id": "00000000-0000-0000-0000-000000000007"},
        entities=[Entity(id="device", name="Pixhawk", entity_type="product", scope="case:7")],
        facts=[
            Fact(
                subject="device",
                predicate="instruction",
                kind="instruction",
                value="Try calibration",
                qualifiers={"modality": "recommendation"},
                evidence=[proof],
            )
        ],
    )
    store = SimpleNamespace(
        known=lambda: [{"status": "success"}],
        read=lambda *a, **k: [{"sid": p.source_id, "payload": p.model_dump_json()}],
    )
    monkeypatch.setattr(display, "verify", lambda *a: {"verified": True, "build_id": "build"})
    return display.ForumDisplay(store, None), p, store


def test_value_facts_are_visible_without_inventing_entities(view):
    service, p, _ = view
    graph = service.graph()
    assert len(graph.nodes) == 2 and len(graph.edges) == 1
    value = next(n for n in graph.nodes if n.kind == "fact")
    assert value.label == "Try calibration" and value.entity_type == "instruction"
    assert service.overview()["entities"] == 1
    assert service.overview()["active_topics"] == 1
    assert service.evidence(p.facts[0].id)["items"][0]["post_number"] == 2
    assert service.list_entities("pixhawk")["total"] == 1
    assert len(service.graph(entity_id="device").edges) == 1
    assert len(service.graph(entity_type="instruction").edges) == 1
    assert not service.graph(q="unrelated").nodes


def test_stale_or_replaced_native_build_is_excluded(view, monkeypatch):
    _, _, store = view
    monkeypatch.setattr(display, "verify", lambda *a: {"verified": True, "build_id": "other"})
    service = display.ForumDisplay(store, None)
    assert not service.graph().nodes
    assert service.overview()["unavailable_topics"] == 1


def test_empty_graph_and_evidence_pagination(view):
    service, p, store = view
    assert service.evidence(p.facts[0].id, offset=1)["items"] == []
    assert service.evidence("unknown")["total"] == 0
    assert not service.graph(min_component_size=5).nodes
    store.read = lambda *a, **k: []
    assert not display.ForumDisplay(store, None).graph().edges
