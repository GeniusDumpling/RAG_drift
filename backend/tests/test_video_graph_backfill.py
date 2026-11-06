"""Bounded backfills, optimistic source checks and resumability independent of model output."""

import json
from unittest.mock import patch

import pytest
from app.services import video_graph_backfill as backfill
from app.services.video_graph_identity import stable_hash

from backend.tests.test_video_graph_quality import material

pytestmark = pytest.mark.no_db


class SourceSession:
    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass


def plan_for(materials):
    items = [
        dict(
            content_id=str(m.id),
            fingerprint=m.fingerprint,
            baseline_active_id="legacy",
            baseline_version="video-graph-v1:old",
            title=m.title,
            chunks=1,
        )
        for m in materials
    ]
    return {
        "format_version": 1,
        "target_version": "test-v2",
        "items": items,
        "plan_hash": stable_hash({"target_version": "test-v2", "items": items}),
    }


class Extractor:
    version = "test-v2"


def test_plan_mutation_and_version_drift_refused():
    plan = plan_for([material()])
    backfill.validate_plan(plan, "test-v2")
    with pytest.raises(ValueError, match="目标版本"):
        backfill.validate_plan(plan, "different")
    plan["items"][0]["content_id"] = str(material().id)
    with pytest.raises(ValueError, match="内容已变化"):
        backfill.validate_plan(plan, "test-v2")


def test_only_selected_batch_is_enqueued_and_resume_uses_database_truth(tmp_path):
    materials = [material() for _ in range(5)]
    path = tmp_path / "plan.json"
    backfill.save_plan(path, plan_for(materials))
    by_id = {m.id: m for m in materials}
    states = {m.id: dict(active_id="legacy", version="video-graph-v1:old") for m in materials}
    enqueued = []

    def enqueue(*args, content_ids, **kwargs):
        enqueued.append(list(content_ids))

    def process(*args, content_ids):
        cid = next((c for c in content_ids if states[c]["version"] != "test-v2"), None)
        if cid is None:
            return None
        states[cid] = dict(
            active_id=f"new-{cid}",
            version="test-v2",
            fingerprint=by_id[cid].fingerprint,
            status="success",
        )
        return dict(content_item_id=str(cid), status="success", projection_activated=True)

    with (
        patch.object(backfill, "Session", return_value=SourceSession()),
        patch.object(backfill, "load_material", side_effect=lambda _, cid: by_id[cid]),
        patch.object(backfill, "state_snapshot", side_effect=lambda _, cid, store: states[cid]),
        patch("app.services.neo4j_graph.worker.enqueue_videos", side_effect=enqueue),
        patch("app.services.neo4j_graph.worker.process_next", side_effect=process),
    ):
        first = list(
            backfill.apply_plan(None, Extractor(), path, store=object(), limit=2, batch_size=1)
        )
        assert first[0]["selected"] == 2
        assert enqueued == [[materials[0].id], [materials[1].id]]
        second = list(
            backfill.apply_plan(None, Extractor(), path, store=object(), limit=2, batch_size=2)
        )
        assert second[0]["selected"] == 2
        assert enqueued[-1] == [materials[2].id, materials[3].id]
    saved = json.loads(path.read_text())
    assert saved["results"][str(materials[0].id)]["status"] == "already_current"
    assert str(materials[4].id) not in saved["results"]


def test_stale_source_withdrawal_and_competing_active_version_do_not_enqueue(tmp_path):
    materials = [material() for _ in range(3)]
    plan = plan_for(materials)
    plan["items"][0]["fingerprint"] = "changed-before-run"
    plan["plan_hash"] = stable_hash({"target_version": "test-v2", "items": plan["items"]})
    path = tmp_path / "plan.json"
    backfill.save_plan(path, plan)
    states = {
        materials[0].id: {},
        materials[1].id: {"withdrawn": True},
        materials[2].id: {"active_id": "newer-unrelated"},
    }
    with (
        patch.object(backfill, "Session", return_value=SourceSession()),
        patch.object(
            backfill,
            "load_material",
            side_effect=lambda _, cid: next(m for m in materials if m.id == cid),
        ),
        patch.object(backfill, "state_snapshot", side_effect=lambda _, cid, store: states[cid]),
        patch("app.services.neo4j_graph.worker.enqueue_videos") as enqueue,
    ):
        events = list(backfill.apply_plan(None, Extractor(), path, store=object()))
    assert events[0]["selected"] == 0
    enqueue.assert_not_called()
    assert {r["status"] for r in json.loads(path.read_text())["results"].values()} == {
        "stale_source",
        "withdrawn",
        "active_version_conflict",
    }
