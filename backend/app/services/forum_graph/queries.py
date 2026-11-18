"""Independent native/shared readback with current PostgreSQL evidence validation."""

import json

from app.services.knowledge_graph import queries as shared
from app.services.knowledge_graph.schema import digest, normalized

from .extraction import validate_assessment
from .projection import compile_output
from .source import Material, load_material, validate_archives
from .storage import ForumGraphStore
from .windows import aggregate, make_windows


def ordered(rows):
    return dict(
        nodes={k: sorted(v, key=lambda r: r["id"]) for k, v in rows["nodes"].items()},
        edges=sorted(rows["edges"], key=lambda r: r["id"]),
    )


def verify(store, session, source_id, *, build_id=None):
    state = ForumGraphStore.status(store, source_id)
    if state is None or state["s"].get("withdrawn"):
        return dict(verified=False, source_id=source_id, checks={"active": False})
    desired = state["b"]
    if desired is None:
        return dict(verified=False, source_id=source_id, checks={"build": False})
    # A failed policy upgrade must not hide a valid active build whose source
    # is unchanged. Explicit build verification still checks the desired target.
    bid = build_id or state["s"].get("active_build_id") or desired["id"]
    record = store.read("MATCH(b:FGBuild {id:$bid}) RETURN b", bid=bid)
    if not record:
        return dict(verified=False, source_id=source_id, checks={"build": False})
    build = record[0]["b"]
    if build.get("source_id") != source_id or build["status"] not in {"success", "skipped"}:
        return dict(verified=False, source_id=source_id, build_id=bid, checks={"terminal": False})
    material = load_material(session, state["s"]["content_item_id"])
    if material is None:
        return dict(verified=False, source_id=source_id, checks={"source_current": False})
    output = json.loads(build["output_json"])
    checks = dict(
        source_current=material.fingerprint == build["fingerprint"],
        target_current=(state["s"].get("desired_build_id") == bid)
        if build_id or build["status"] == "skipped"
        else state["s"].get("active_build_id") == bid,
        version_bound=build["policy"] == output.get("policy"),
    )
    if not all(checks.values()):
        return dict(verified=False, source_id=source_id, build_id=bid, checks=checks)
    windows = make_windows(
        material, max_chars=output["window_chars"], context_chars=output["context_chars"]
    )
    assessments = output.get("assessments", {})
    summary = aggregate(windows, {k: v["value"] for k, v in assessments.items()})
    checks["assessment_current"] = all(
        assessments[w.id]["fingerprint"] == w.fingerprint(material)
        and assessments[w.id]["policy"] == build["policy"]
        for w in windows
    )
    for window in windows:
        validate_assessment(material, window, build["policy"], assessments[window.id])
    if build["status"] == "skipped":
        checks.update(
            skipped=summary["decision"] == "skip",
            no_active_native=state["s"].get("active_build_id") is None,
        )
        shared_state = store.read("MATCH(s:KGSource {id:$id}) RETURN s", id=source_id)
        checks["no_active_shared"] = not shared_state or bool(shared_state[0]["s"]["withdrawn"])
        return dict(
            verified=all(checks.values()),
            source_id=source_id,
            build_id=bid,
            status="skipped",
            checks=checks,
        )
    checks["active"] = (
        state["s"].get("active_build_id") == bid
        and not state["s"].get("stale")
        and build["status"] == "success"
    )
    if not all(checks.values()):
        return dict(verified=False, source_id=source_id, build_id=bid, checks=checks)
    frozen = Material.model_validate(output["material"])
    validate_archives(session, frozen)
    checks["frozen_source"] = frozen.fingerprint == material.fingerprint
    rows, publication = compile_output(frozen, windows, output, build["policy"], bid)
    actual = ForumGraphStore.native_rows(store, bid)
    checks.update(
        native_equal=ordered(rows) == ordered(actual),
        native_hash=digest(rows) == output.get("native_hash"),
        publication_hash=publication.payload_hash == output.get("publication_hash"),
    )
    checks["shared_equal"] = shared.verify(store, source_id, publication)["verified"]
    current = load_material(session, material.content_item_id)
    state_after = ForumGraphStore.status(store, source_id)
    checks["unchanged"] = (
        current is not None
        and current.fingerprint == material.fingerprint
        and state_after["s"].get("active_build_id") == bid
        and not state_after["s"].get("withdrawn")
        and not state_after["s"].get("stale")
    )
    return dict(
        verified=all(checks.values()),
        source_id=source_id,
        build_id=bid,
        checks=checks,
        counts={label: len(values) for label, values in rows["nodes"].items()},
        facts=len(publication.facts),
        evidence=sum(len(f.evidence) for f in publication.facts),
    )


def retrieve(store, session, query="", *, source_id=None, limit=20, offset=0):
    response = shared.retrieve(
        store,
        query,
        source_type="forum",
        source_id=source_id,
        limit=limit,
        offset=offset,
        validate_forum=False,
    )
    selected = {
        s["id"]: s["native_build_id"] for item in response["items"] for s in item["sources"]
    }
    if source_id or not normalized(query):
        selected.update(
            {
                s["id"]: s["native_build_id"]
                for s in shared.sources(store, "forum")
                if s["active_build_id"]
                and not s["withdrawn"]
                and (source_id is None or s["id"] == source_id)
            }
        )
    for sid, bid in selected.items():
        check = verify(store, session, sid)
        if not check["verified"] or check.get("build_id") != bid:
            raise ValueError("论坛图谱或当前原文校验失败，拒绝返回过期证据")
    return response


def status_summary(store, source_id):
    row = ForumGraphStore.status(store, source_id)
    if not row:
        return dict(status="absent", source_id=source_id)
    source, build = row["s"], row.get("b")
    output = json.loads(build["output_json"]) if build else {}
    return dict(
        source_id=source_id,
        content_item_id=source["content_item_id"],
        withdrawn=source.get("withdrawn", False),
        stale=source.get("stale", True),
        active_build_id=source.get("active_build_id"),
        desired_build_id=source.get("desired_build_id"),
        status=build["status"] if build else "absent",
        attempts=build.get("attempts", 0) if build else 0,
        error_type=build.get("error_type") if build else None,
        assessment=output.get("assessment"),
        usage=output.get("usage", {}),
    )


def review_bundle(store, session, source_id):
    check = verify(store, session, source_id)
    if not check["verified"]:
        raise ValueError("不能导出尚未通过完整回读验证的主题")
    build = store.read("MATCH(b:FGBuild {id:$bid}) RETURN b", bid=check["build_id"])[0]["b"]
    output = json.loads(build["output_json"])
    result = dict(verification=check, status=status_summary(store, source_id))
    if check.get("status") == "skipped":
        result["assessments"] = output["assessments"]
        return result
    source = Material.model_validate(output["material"])
    windows = make_windows(
        source, max_chars=output["window_chars"], context_chars=output["context_chars"]
    )
    _, publication = compile_output(source, windows, output, build["policy"], build["id"])
    result.update(
        native=ForumGraphStore.native_rows(store, build["id"]),
        publication=publication.model_dump(),
        assessments=output["assessments"],
        reviewed_windows=output["windows"],
    )
    return result
