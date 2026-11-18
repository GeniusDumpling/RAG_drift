"""Fixed bounded plans; assessment first, checkpointed windows, atomic topic publication."""

import json
from pathlib import Path

from sqlalchemy.orm import Session

from app.services.knowledge_graph.schema import digest
from app.services.video_graph.material import LeaseLostError
from app.services.video_graph.usage import merge_usage

from .client import ForumClient
from .extraction import (
    assess,
    assessment_record,
    extract_window,
    policy_id,
    validate_assessment,
    validate_result,
)
from .queries import verify
from .source import BASES, SourceNotReady, inventory, load_material, save_json
from .storage import now_ms
from .windows import aggregate, make_windows


def target_hash(settings):
    prefix = "unified_graph_neo4j" if settings.unified_graph_enabled else "forum_graph_neo4j"
    return digest([getattr(settings, prefix + "_uri"), getattr(settings, prefix + "_database")])


def reconcile(store, engine, *, content_ids=None, forums=None):
    from urllib.parse import urlsplit
    from uuid import UUID

    from app.models.content import ContentItem

    selected = set(map(str, content_ids)) if content_ids else None
    domains = {urlsplit(base).netloc: name for name, base in BASES.items()}
    withdrawn = []
    with Session(engine) as session:
        for row in store.known():
            if row["withdrawn"] or (
                selected is not None and row["content_item_id"] not in selected
            ):
                continue
            forum = row.get("forum") or domains.get(row["id"].split(":")[1])
            if forums and forum not in forums:
                continue
            if session.get(ContentItem, UUID(row["content_item_id"])) is None:
                store.withdraw(row["id"])
                withdrawn.append(row["id"])
    return withdrawn


def observe_updates(store, engine, *, forums=None):
    known = {r["id"]: r for r in store.known() if not r["withdrawn"]}
    with Session(engine) as session:
        for cid in inventory(session, forums=forums):
            try:
                material = load_material(session, cid)
            except SourceNotReady:
                continue
            if material and material.source_id in known:
                store.observe_update(material)


def plan(settings, store, engine, *, limit=20, content_ids=None, forums=None, retry_failed=False):
    if limit < 1 or (content_ids and len(set(content_ids)) > limit):
        raise ValueError("计划限额必须覆盖所有显式来源")
    if settings.forum_graph_max_wait_seconds < settings.forum_graph_debounce_seconds:
        raise ValueError("最大合并等待时间不能小于静默期")
    policy = policy_id(settings)
    known = {r["id"]: r for r in store.known()} if store else {}
    candidates, waiting = [], []
    with Session(engine) as session:
        for content_id in inventory(session, forums=forums, content_ids=content_ids):
            try:
                material = load_material(session, content_id)
            except SourceNotReady as exc:
                if content_ids:
                    raise
                waiting.append(dict(content_item_id=str(content_id), reason=str(exc)))
                continue
            state = known.get(material.source_id)
            if state and state["withdrawn"]:
                if content_ids:
                    raise ValueError("显式来源图谱已撤回")
                continue
            if (
                not content_ids
                and state
                and state.get("observed_fingerprint") == material.fingerprint
                and state.get("update_first_seen") is not None
                and now_ms() - state["update_first_seen"]
                < settings.forum_graph_max_wait_seconds * 1000
                and now_ms() - state["update_last_seen"]
                < settings.forum_graph_debounce_seconds * 1000
            ):
                waiting.append(dict(content_item_id=str(content_id), reason="coalescing_updates"))
                continue
            bid = digest([material.source_id, material.fingerprint, policy])
            current = state and state.get("desired_build_id") == bid
            if not content_ids and current:
                if state.get("status") in {"success", "skipped"}:
                    continue
                if state.get("status") == "running" and (state.get("lease_until") or 0) >= now_ms():
                    continue
                if state.get("status") in {"failed", "deferred"} and not retry_failed:
                    waiting.append(
                        dict(content_item_id=str(content_id), reason="retry_failed_required")
                    )
                    continue
            candidates.append(
                dict(
                    content_item_id=str(content_id),
                    source_id=material.source_id,
                    fingerprint=material.fingerprint,
                    build_id=bid,
                    unchanged=bool(current),
                    expected_desired_id=state.get("desired_build_id") if state else None,
                )
            )
    # Stable plans prefer changed/unseen material; selecting another plan moves
    # through the historical backlog instead of repeatedly taking current topics.
    candidates.sort(key=lambda r: r["unchanged"])
    # Reserve part of a continuously changing feed for old backlog topics.
    budget = min(limit // 5, len(candidates))
    selected = candidates[: max(0, limit - budget)]
    if budget:
        selected.extend(r for r in candidates[-budget:] if r not in selected)
    selected.extend(r for r in candidates if r not in selected)
    result = dict(
        schema_version=1,
        policy=policy,
        target_hash=target_hash(settings),
        forums=sorted(forums or []),
        window_chars=settings.forum_graph_window_chars,
        context_chars=settings.forum_graph_context_chars,
        entries=selected[:limit],
        waiting=waiting,
    )
    result["plan_hash"] = digest(result)
    return result


async def run_topic(
    settings,
    store,
    engine,
    entry,
    *,
    policy,
    cache,
    retry_failed=False,
    cache_only=False,
    client_factory=ForumClient,
):
    with Session(engine) as session:
        material = load_material(session, entry["content_item_id"])
    if material is None or material.fingerprint != entry["fingerprint"]:
        raise SourceNotReady("计划来源已变化，请使用新的计划文件")
    bid = store.enqueue(
        material,
        policy,
        retry_failed=retry_failed,
        expected_desired_id=entry.get("expected_desired_id", ...),
    )
    if bid != entry["build_id"]:
        raise ValueError("计划目标与实际目标不一致")
    claimed = store.claim(material.source_id, bid)
    if claimed is None:
        state = store.status(material.source_id)
        status = state["b"]["status"]
        if status in {"success", "skipped"}:
            with Session(engine) as session:
                check = verify(store, session, material.source_id, build_id=bid)
            return dict(
                source_id=material.source_id,
                build_id=bid,
                status="already_current" if check["verified"] else "verification_failed",
                verification=check,
                usage={},
            )
        return dict(source_id=material.source_id, build_id=bid, status=status, usage={})
    build, token = claimed
    output = json.loads(build["output_json"])
    output.update(
        policy=policy,
        window_chars=settings.forum_graph_window_chars,
        context_chars=settings.forum_graph_context_chars,
    )
    previous_usage = output.get("usage", {})

    def checkpoint(value):
        value["usage"] = merge_usage(previous_usage, client.usage.snapshot())
        store.checkpoint(material.source_id, bid, token, value)

    client = client_factory(
        settings,
        cache,
        cache_only=cache_only,
        output=output,
        checkpoint=checkpoint,
    )
    windows = make_windows(
        material,
        max_chars=settings.forum_graph_window_chars,
        context_chars=settings.forum_graph_context_chars,
    )
    cached_assessments = output.get("assessments", {})
    cached_windows = {**output.get("window_cache", {}), **output.get("windows", {})}
    output["assessments"], output["windows"] = {}, {}
    output["window_cache"] = cached_windows
    try:
        # All current windows are assessed before any extraction is allowed.
        for window in windows:
            old = cached_assessments.get(window.id)
            value = None
            if (
                old
                and old["fingerprint"] == window.fingerprint(material)
                and old["policy"] == policy
            ):
                try:
                    value = validate_assessment(material, window, policy, old)
                    client.usage.replay("assessment")
                except (ValueError, KeyError):
                    pass
            if value is None:
                value = await assess(material, window, client)
            output["assessments"][window.id] = assessment_record(material, window, policy, value)
            client.heartbeat()
        summary = aggregate(windows, {k: r["value"] for k, r in output["assessments"].items()})
        output["assessment"] = summary
        if summary["decision"] != "extract":
            status = "skipped" if summary["decision"] == "skip" else "deferred"
            with Session(engine) as session, session.begin():
                current = load_material(session, material.content_item_id, lock=True)
                if current is None or current.fingerprint != material.fingerprint:
                    raise SourceNotReady("终态提交前来源已变化")
                store.finish(material.source_id, bid, token, output, status, material=material)
            check = None
            if status == "skipped":
                with Session(engine) as session:
                    check = verify(store, session, material.source_id, build_id=bid)
            return dict(
                source_id=material.source_id,
                build_id=bid,
                status=status,
                assessment=summary,
                verification=check,
                usage=client.usage.snapshot(),
            )
        for window in windows:
            if window.id not in summary["extract_windows"]:
                continue
            old = cached_windows.get(window.id)
            if old:
                try:
                    result = validate_result(material, window, policy, old, output["windows"])
                    client.usage.replay("reviewed_window")
                except (ValueError, KeyError):
                    result = None
            else:
                result = None
            if result is None:
                result = await extract_window(material, window, client, policy, output["windows"])
            output["windows"][window.id] = result
            client.heartbeat()
        output.pop("window_cache", None)
        # No provider call holds a SQL source-row lock. Neo4j activation happens
        # inside this short read lock; queries still revalidate afterwards.
        with Session(engine) as session, session.begin():
            current = load_material(session, material.content_item_id, lock=True)
            if current is None or current.fingerprint != material.fingerprint:
                raise SourceNotReady("模型运行期间来源已变化")
            store.publish(current, windows, policy, bid, token, output)
        with Session(engine) as session:
            check = verify(store, session, material.source_id, build_id=bid)
        return dict(
            source_id=material.source_id,
            build_id=bid,
            status="success" if check["verified"] else "verification_failed",
            assessment=summary,
            verification=check,
            usage=client.usage.snapshot(),
        )
    except LeaseLostError:
        return dict(
            source_id=material.source_id,
            build_id=bid,
            status="lease_lost",
            usage=client.usage.snapshot(),
        )
    except Exception as exc:
        try:
            output["usage"] = merge_usage(previous_usage, client.usage.snapshot())
            store.finish(material.source_id, bid, token, output, "failed", error=type(exc).__name__)
        except LeaseLostError:
            pass
        return dict(
            source_id=material.source_id,
            build_id=bid,
            status="failed",
            error_type=type(exc).__name__,
            usage=client.usage.snapshot(),
        )


async def run_pipeline(
    settings,
    store,
    engine,
    *,
    manifest,
    report,
    cache,
    limit=20,
    content_ids=None,
    forums=None,
    retry_failed=False,
    cache_only=False,
    dry_run=False,
    client_factory=ForumClient,
):
    manifest, report, cache = Path(manifest), Path(report), Path(cache)
    if manifest.resolve() == report.resolve():
        raise ValueError("计划与报告不能使用同一路径")
    policy = policy_id(settings)
    if manifest.exists():
        selection = json.loads(manifest.read_text())
        if selection.get("plan_hash") != digest(
            {k: v for k, v in selection.items() if k != "plan_hash"}
        ):
            raise ValueError("计划内容校验失败")
        if selection.get("target_hash") != target_hash(settings):
            raise ValueError("不能改变固定计划的图谱目标")
        if forums and selection.get("forums") != sorted(forums):
            raise ValueError("不能改变固定计划的论坛范围")
        ids = [e["content_item_id"] for e in selection["entries"]]
        if len(ids) != len(set(ids)):
            raise ValueError("计划来源重复")
        if selection["policy"] != policy or len(selection["entries"]) > limit:
            raise ValueError("计划版本/限额已变化，请使用新文件名")
        if content_ids and set(map(str, content_ids)) != {
            e["content_item_id"] for e in selection["entries"]
        }:
            raise ValueError("不能改变固定计划的来源范围")
        with Session(engine) as session:
            for entry in selection["entries"]:
                material = load_material(session, entry["content_item_id"])
                if material is None or material.fingerprint != entry["fingerprint"]:
                    raise SourceNotReady("固定计划来源已过期，请建立新计划")
    else:
        selection = plan(
            settings,
            store,
            engine,
            limit=limit,
            content_ids=content_ids,
            forums=forums,
            retry_failed=retry_failed,
        )
    if dry_run:
        return dict(status="read_only", plan=selection)
    if not manifest.exists() and not content_ids and settings.forum_graph_debounce_seconds:
        if report.exists():
            raise ValueError("已有报告缺少对应计划，拒绝覆盖")
        observe_updates(store, engine, forums=forums)
        selection = plan(
            settings, store, engine, limit=limit, forums=forums, retry_failed=retry_failed
        )
    withdrawn = reconcile(store, engine, content_ids=content_ids, forums=forums)
    save_json(manifest, selection)
    previous = json.loads(report.read_text()) if report.exists() else {}
    if previous and previous.get("plan_hash") != selection["plan_hash"]:
        raise ValueError("报告属于其他固定计划，请使用新路径")
    result = dict(
        status="running",
        policy=policy,
        plan=str(manifest),
        plan_hash=selection["plan_hash"],
        results=[],
        reconciled_withdrawals=withdrawn,
    )
    save_json(report, result)
    for entry in selection["entries"]:
        try:
            outcome = await run_topic(
                settings,
                store,
                engine,
                entry,
                policy=policy,
                cache=cache,
                retry_failed=retry_failed,
                cache_only=cache_only,
                client_factory=client_factory,
            )
        except Exception as exc:
            outcome = dict(
                source_id=entry["source_id"], status="failed", error_type=type(exc).__name__
            )
        result["results"].append(outcome)
        save_json(report, result)
    result["status"] = (
        "success"
        if all(
            r["status"] in {"success", "already_current", "skipped"}
            and r.get("verification", {}).get("verified")
            for r in result["results"]
        )
        else "incomplete"
    )
    usage = {}
    for row in result["results"]:
        usage = merge_usage(usage, row.get("usage", {}))
    result["usage"] = usage
    result["cumulative_usage"] = merge_usage(previous.get("cumulative_usage", {}), usage)
    save_json(report, result)
    return result
