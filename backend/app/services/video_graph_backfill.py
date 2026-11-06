"""Pinned, inspectable backfill plans; only selected batches can enqueue model work."""

import json
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.content import ContentItem
from app.models.video_graph import VideoGraphBuild, VideoGraphState
from app.services.video_graph import enqueue_videos, load_material, process_next
from app.services.video_graph_identity import stable_hash


def state_snapshot(session: Session, content_id: uuid.UUID, store: Any = None) -> dict[str, Any]:
    if store is not None:
        rows = store.read(
            "MATCH (v:VGVideo {id:$id}) OPTIONAL MATCH (b:VGBuild {id:v.active_build_id}) "
            "RETURN v.withdrawn AS withdrawn,b.id AS active_id,b.extractor_version AS version, "
            "b.fingerprint AS fingerprint,b.status AS status",
            id=str(content_id),
        )
        return rows[0] if rows else {}
    state = session.get(VideoGraphState, content_id)
    active = (
        session.get(VideoGraphBuild, state.active_build_id)
        if state and state.active_build_id
        else None
    )
    return {
        "withdrawn": state.withdrawn if state else False,
        "active_id": str(active.id) if active else None,
        "version": active.extractor_version if active else None,
        "fingerprint": active.fingerprint if active else None,
        "status": active.status if active else None,
    }


def create_plan(
    engine: Any,
    version: str,
    *,
    store: Any = None,
    content_ids: list[uuid.UUID] | None = None,
    include_current: bool = False,
) -> dict[str, Any]:
    items, counts = [], {"scanned": 0, "current": 0, "withdrawn": 0}
    with Session(engine) as session:
        query = select(ContentItem.id).where(ContentItem.item_type == "video_description")
        if content_ids:
            query = query.where(ContentItem.id.in_(content_ids))
        for content_id in session.scalars(query.order_by(ContentItem.created_at, ContentItem.id)):
            material = load_material(session, content_id)
            if material is None:
                continue
            counts["scanned"] += 1
            state = state_snapshot(session, content_id, store)
            if state.get("withdrawn"):
                counts["withdrawn"] += 1
                continue
            current = (
                state.get("version") == version
                and state.get("fingerprint") == material.fingerprint
                and state.get("status") == "success"
            )
            if current:
                counts["current"] += 1
                if not include_current:
                    continue
            items.append(
                {
                    "content_id": str(content_id),
                    "title": material.title,
                    "fingerprint": material.fingerprint,
                    "baseline_active_id": state.get("active_id"),
                    "baseline_version": state.get("version"),
                    "reason": "already_current"
                    if current
                    else "version_changed"
                    if state.get("version") != version
                    else "incomplete_or_changed",
                    "chunks": len(material.extraction_chunks),
                    "estimated_model_calls": 1
                    + sum(
                        4 if c.evidence_type == "transcript_segment" else 2
                        for c in material.extraction_chunks
                    )
                    if material.extraction_chunks and not current
                    else 0,
                }
            )
    # Fix already visible legacy graphs first, then previously unbuilt videos.
    items.sort(
        key=lambda row: (
            row["reason"] == "already_current",
            not bool(row["baseline_active_id"]),
            row["content_id"],
        )
    )
    plan = {
        "format_version": 1,
        "created_at": datetime.now(UTC).isoformat(),
        "target_version": version,
        "counts": {**counts, "candidates": len(items)},
        "items": items,
    }
    plan["plan_hash"] = stable_hash({"target_version": version, "items": items})
    return plan


def validate_plan(plan: dict[str, Any], version: str) -> None:
    if plan.get("format_version") != 1 or plan.get("target_version") != version:
        raise ValueError("回补清单格式或目标版本与当前抽取器不一致，请重新生成清单")
    if plan.get("plan_hash") != stable_hash(
        {"target_version": version, "items": plan.get("items")}
    ):
        raise ValueError("回补清单内容已变化，请重新生成清单")
    ids = [str(uuid.UUID(row["content_id"])) for row in plan["items"]]
    if len(ids) != len(set(ids)):
        raise ValueError("回补清单包含重复视频")


def save_plan(path: Path, plan: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(plan, ensure_ascii=False, indent=2) + "\n")
    temporary.replace(path)


def apply_plan(
    engine: Any,
    extractor: Any,
    path: Path,
    *,
    store: Any = None,
    limit: int = 20,
    batch_size: int = 5,
    retry_failed: bool = False,
):
    """Persist each result before advancing; reruns check DB truth, not a cursor alone."""
    if limit < 1 or batch_size < 1:
        raise ValueError("回补数量和批量大小必须大于 0")
    plan = json.loads(path.read_text())
    validate_plan(plan, extractor.version)
    selected = []
    expected_active, expected_fingerprints = {}, {}
    plan.setdefault("results", {})
    with Session(engine) as session:
        for item in plan["items"]:
            cid = uuid.UUID(item["content_id"])
            material = load_material(session, cid)
            state = state_snapshot(session, cid, store)
            if material is None or material.fingerprint != item["fingerprint"]:
                plan["results"][str(cid)] = {"status": "stale_source"}
                continue
            if state.get("withdrawn"):
                plan["results"][str(cid)] = {"status": "withdrawn"}
                continue
            if (
                state.get("version") == extractor.version
                and state.get("fingerprint") == material.fingerprint
                and state.get("status") == "success"
            ):
                plan["results"][str(cid)] = {
                    "status": "already_current",
                    "active_id": state["active_id"],
                }
                continue
            same_target = (
                state.get("version") == extractor.version
                and state.get("fingerprint") == item["fingerprint"]
            )
            if state.get("active_id") != item["baseline_active_id"] and not same_target:
                plan["results"][str(cid)] = {"status": "active_version_conflict"}
                continue
            previous = plan["results"].get(str(cid), {}).get("status")
            if previous in {"failed", "partial_success"} and not retry_failed:
                continue
            selected.append(cid)
            expected_active[cid] = state.get("active_id")
            expected_fingerprints[cid] = item["fingerprint"]
            if len(selected) == limit:
                break
    save_plan(path, plan)
    yield {
        "stage": "backfill_selected",
        "selected": len(selected),
        "target_version": extractor.version,
    }
    for offset in range(0, len(selected), batch_size):
        batch = selected[offset : offset + batch_size]
        if store is not None:
            from app.services.neo4j_graph.worker import enqueue_videos as enqueue_neo4j

            enqueue_neo4j(
                engine,
                store,
                extractor_version=extractor.version,
                content_ids=batch,
                retry_failed=True,
                expected_active_ids=expected_active,
                expected_fingerprints=expected_fingerprints,
            )
        else:
            with Session(engine) as session, session.begin():
                enqueue_videos(
                    session,
                    extractor_version=extractor.version,
                    content_ids=batch,
                    retry_failed=True,
                    expected_active_ids=expected_active,
                    expected_fingerprints=expected_fingerprints,
                )
        for _ in batch:
            if store is not None:
                from app.services.neo4j_graph.worker import process_next as process_neo4j

                result = process_neo4j(engine, store, extractor, content_ids=batch)
            else:
                result = process_next(engine, extractor, content_ids=batch)
            if result is None:
                break
            plan["results"][result["content_item_id"]] = result
            save_plan(path, plan)
            yield {"stage": "backfill_result", **result}
    yield {
        "stage": "backfill_summary",
        "selected": len(selected),
        "results": {
            key: sum(r["status"] == key for r in plan["results"].values())
            for key in sorted({r["status"] for r in plan["results"].values()})
        },
    }
