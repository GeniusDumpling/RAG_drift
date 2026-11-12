"""Bounded source-to-graph runs with durable plans and independent publication readback."""

import copy
import json
import uuid
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy.orm import Session

from app.schemas.video_graph import ChunkExtraction, GraphRetrieveRequest, VideoAssessment
from app.services.video_graph.backfill import (
    apply_plan,
    create_plan,
    save_plan,
    state_snapshot,
    validate_plan,
)
from app.services.video_graph.identity import entity_identity, stable_hash
from app.services.video_graph.material import ENTITY_NAMESPACE, digest, load_material, locate_quote
from app.services.video_graph.semantics import normalized_name
from app.services.video_graph.validation import update_coverage, validate_extraction


def _active_snapshot(session, content_id, store):
    return state_snapshot(session, content_id, store)


def _evidence_key(row):
    return (
        str(row["relation_id"]),
        str(row["content_chunk_id"]),
        row["quote"],
        row["char_start"],
        row["char_end"],
        row["assertion"],
        row.get("start_seconds"),
        row.get("end_seconds"),
        row.get("caption_source"),
    )


def _expected_projection(material, snapshot):
    output = copy.deepcopy(snapshot["output"])
    assessment = VideoAssessment.model_validate(output.get("assessment"))
    coverage = update_coverage(output, material)
    if not coverage["complete"] or coverage["skipped_chunks"]:
        raise ValueError("当前有效构建仍有未通过或未处理的切片")
    published = {str(c.id) for c in material.extraction_chunks} if assessment.relevant else set()
    if (
        output.get("projection_activated") is not True
        or set(output.get("published_chunk_ids", [])) != published
        or output.get("published_coverage") != coverage
        or (coverage["applicable"] and not coverage["passed_chunks"])
    ):
        raise ValueError("有效构建缺少一致的发布覆盖率与切片记录")
    nodes, edges, mentions, evidence = {}, {}, set(), set()
    if not coverage["applicable"]:
        return nodes, edges, mentions, evidence
    for chunk in material.extraction_chunks:
        extraction = validate_extraction(
            chunk,
            ChunkExtraction.model_validate(output["chunks"][str(chunk.id)]),
            material=material,
            require_review=True,
        )
        keys = {}
        for entity in extraction.entities:
            identity = entity_identity(entity, material)
            eid, name = str(identity.id), identity.name
            keys[entity.key] = eid
            nodes[eid] = (name, entity.entity_type)
            start, end = locate_quote(chunk.text, entity.surface_text)
            mentions.add((str(chunk.id), eid, start, end))
        for relation in extraction.relations:
            subject, obj = keys[relation.subject], keys[relation.object]
            if subject == obj:
                continue
            rid = str(
                uuid.uuid5(
                    ENTITY_NAMESPACE,
                    digest([subject, relation.predicate, obj, relation.qualifiers]),
                )
            )
            edges[rid] = (subject, relation.predicate, obj, stable_hash(relation.qualifiers))
            start, end = locate_quote(chunk.text, relation.quote)
            evidence.add(
                _evidence_key(
                    dict(
                        relation_id=rid,
                        content_chunk_id=str(chunk.id),
                        quote=chunk.text[start:end],
                        char_start=start,
                        char_end=end,
                        assertion=relation.assertion,
                        start_seconds=chunk.start_seconds,
                        end_seconds=chunk.end_seconds,
                        caption_source=material.caption_source,
                    )
                )
            )
    return nodes, edges, mentions, evidence


def _stored_source_rows(session, snapshot, content_id, store):
    mentions = store.read(
        "MATCH (b:VGBuild {id:$id})-[:HAS_MENTION]->(m:VGMention)-[:REFERS_TO]->(e:VGEntity) "
        "RETURN m.content_chunk_id AS chunk,e.id AS entity,"
        "m.start_char AS start,m.end_char AS end",
        id=snapshot["id"],
    )
    evidence = store.read(
        "MATCH (b:VGBuild {id:$id})-[:HAS_EVIDENCE]->(ev:VGEvidence)"
        "-[:SUPPORTS]->(r:VGRelation) "
        "RETURN r.id AS relation_id,ev",
        id=snapshot["id"],
    )
    return (
        {(m["chunk"], m["entity"], m["start"], m["end"]) for m in mentions},
        {_evidence_key({**r["ev"], "relation_id": r["relation_id"]}) for r in evidence},
    )


def _entity_projection_equal(actual, expected):
    # Shared identities use normalized_name in their UUID. The stored display
    # spelling belongs to the first publisher and can differ in case/spacing.
    return actual.keys() == expected.keys() and all(
        kind == expected[eid][1] and normalized_name(name) == normalized_name(expected[eid][0])
        for eid, (name, kind) in actual.items()
    )


def verify_projection(engine, content_id, version, fingerprint, *, store):
    """Read through the same graph/retrieval services as the API; never mutate a projection."""
    from app.services.video_graph.queries import GraphQueries

    result = dict(content_item_id=str(content_id), verified=False, checks={})
    with Session(engine) as session:
        material = load_material(session, content_id)
        snapshot = _active_snapshot(session, content_id, store)
        checks = result["checks"]
        checks["source_current"] = material is not None and material.fingerprint == fingerprint
        checks["active_current"] = bool(
            snapshot
            and not snapshot["withdrawn"]
            and not snapshot["deleted"]
            and snapshot["version"] == version
            and snapshot["fingerprint"] == fingerprint
            and snapshot["status"] == "success"
            and snapshot["desired_id"] == snapshot["id"]
        )
        result["active_build_id"] = snapshot.get("id")
        if not all(checks.values()):
            result["status"] = "not_current"
            return result
        try:
            expected_nodes, expected_edges, expected_mentions, expected_evidence = (
                _expected_projection(
                    material,
                    snapshot,
                )
            )
        except (ValueError, KeyError, TypeError) as exc:
            checks["saved_output_valid"] = False
            result.update(status="invalid_saved_output", error_type=type(exc).__name__)
            return result
        checks["saved_output_valid"] = True
        queries = GraphQueries(store)
        graph = queries.get_graph(
            session,
            content_id=content_id,
            max_nodes=None,
            max_edges=None,
            min_component_size=0,
        )
        nodes = {n.id: (n.label, n.entity_type) for n in graph.nodes if n.kind == "entity"}
        edges = {
            e.id: (e.source, e.predicate, e.target, stable_hash(e.qualifiers))
            for e in graph.edges
            if e.kind == "relation"
        }
        mentions, evidence = _stored_source_rows(session, snapshot, content_id, store)
        checks.update(
            graph_complete=not graph.truncated,
            entities_equal=_entity_projection_equal(nodes, expected_nodes),
            relations_equal=edges == expected_edges,
            mentions_equal=mentions == expected_mentions,
            evidence_equal=evidence == expected_evidence,
        )
        if getattr(store, "unified", False) is True:
            from app.services.knowledge_graph.adapters import video_publication
            from app.services.knowledge_graph.queries import verify
            from app.services.knowledge_graph.schema import source_key

            checks["unified_projection"] = verify(
                store,
                source_key("video", content_id),
                video_publication(material, snapshot["id"], snapshot["output"], version),
            )["verified"]
        result["counts"] = dict(
            entities=len(nodes),
            relations=len(edges),
            mentions=len(mentions),
            evidence=len(evidence),
        )
        result["expected_counts"] = dict(
            entities=len(expected_nodes),
            relations=len(expected_edges),
            mentions=len(expected_mentions),
            evidence=len(expected_evidence),
        )
        if expected_edges and checks["entities_equal"] and checks["relations_equal"]:
            source_id = next(iter(expected_edges.values()))[0]
            name = expected_nodes[source_id][0]
            context = queries.retrieve_context(
                session,
                GraphRetrieveRequest(
                    query=name,
                    content_item_id=content_id,
                    top_k=1,
                    max_chars=30000,
                ),
            )
            checks["retrievable"] = bool(
                context.context
                and context.evidence
                and all(
                    str(e.relation_id) in expected_edges
                    and e.content_item_id == content_id
                    and e.extractor_version == version
                    and stable_hash(e.qualifiers) == expected_edges[str(e.relation_id)][3]
                    and e.quote in context.context
                    for e in context.evidence
                )
            )
            result["retrieval"] = dict(query=name, evidence=len(context.evidence))
        else:
            # An irrelevant video or an entity-only summary may legitimately have no edges.
            checks["retrievable"] = not expected_edges

    # Source DB and Neo4j cannot share a read transaction. Detect a concurrent publication/update.
    with Session(engine) as session:
        latest = load_material(session, content_id)
        active = _active_snapshot(session, content_id, store)
        checks["unchanged_during_verification"] = bool(
            latest
            and latest.fingerprint == fingerprint
            and active.get("id") == snapshot["id"]
            and active.get("desired_id") == snapshot["id"]
            and not active.get("withdrawn")
            and not active.get("deleted")
            and active.get("version") == version
            and active.get("fingerprint") == fingerprint
            and active.get("status") == "success"
        )
    result["verified"] = all(checks.values())
    result["status"] = "verified" if result["verified"] else "verification_failed"
    return result


def run_pipeline(
    engine,
    extractor,
    manifest_path,
    report_path,
    *,
    store,
    content_ids=None,
    limit=20,
    batch_size=5,
    retry_failed=False,
    dry_run=False,
):
    """Plan a bounded batch, reuse the durable worker, then verify every selected video."""
    manifest_path, report_path = Path(manifest_path), Path(report_path)
    if limit < 1 or batch_size < 1 or manifest_path.resolve() == report_path.resolve():
        raise ValueError("数量须大于0，且清单和报告必须使用不同路径")
    version = extractor.version
    requested = list(dict.fromkeys(content_ids or []))
    if requested:
        with Session(engine) as session:
            for cid in requested:
                if load_material(session, cid) is None:
                    raise ValueError(f"找不到视频：{cid}")
                if state_snapshot(session, cid, store).get("withdrawn"):
                    raise ValueError(f"视频已撤回，需显式restore后再构建：{cid}")
    if manifest_path.exists():
        plan = json.loads(manifest_path.read_text())
        validate_plan(plan, version)
        if requested and set(map(str, requested)) != set(plan.get("requested_content_ids", [])):
            raise ValueError("既有清单已固定视频范围，请复用原范围或使用新清单路径")
        if len(plan["items"]) > limit:
            raise ValueError("既有清单的视频数超过limit；请提高limit或使用新的有界清单")
    else:
        if report_path.exists():
            raise ValueError("报告已存在而清单缺失，请使用新的清单和报告路径")
        plan = create_plan(
            engine,
            version,
            store=store,
            content_ids=requested or None,
            include_current=True,
        )
        available = len(plan["items"])
        plan["items"] = plan["items"][:limit]
        plan["requested_content_ids"] = list(map(str, requested))
        plan["counts"].update(selected=len(plan["items"]), deferred=available - len(plan["items"]))
        plan["plan_hash"] = stable_hash({"target_version": version, "items": plan["items"]})
    previous = None
    if report_path.exists():
        previous = json.loads(report_path.read_text())
        if (
            previous.get("plan_hash") != plan["plan_hash"]
            or previous.get("extractor_version") != version
        ):
            raise ValueError("报告属于其他清单或抽取版本，请使用新路径")
    event = dict(
        stage="pipeline_plan",
        target_version=version,
        counts=plan["counts"],
        selected=len(plan["items"]),
        manifest=str(manifest_path),
        report=str(report_path),
    )
    if dry_run:
        yield {**event, "mode": "read_only"}
        return
    save_plan(manifest_path, plan)
    report = dict(
        pipeline_version=1,
        extractor_version=version,
        backend="neo4j",
        plan_hash=plan["plan_hash"],
        manifest=str(manifest_path),
        started_at=datetime.now(UTC).isoformat(),
        status="running",
        results={},
    )
    usage = getattr(extractor, "token_usage", None)
    usage_before = usage.snapshot() if usage is not None else {}
    prior_usage = (
        previous.get("cumulative_token_usage", previous.get("token_usage", {})) if previous else {}
    )

    def save_report():
        if usage is not None:
            from app.services.video_graph.usage import merge_usage, usage_delta

            report["token_usage"] = usage_delta(usage_before, usage.snapshot())
            report["cumulative_token_usage"] = merge_usage(prior_usage, report["token_usage"])
            report["usage_history_complete"] = (
                previous.get("usage_history_complete", "token_usage" in previous)
                if previous
                else True
            )
        save_plan(report_path, report)

    save_report()
    yield event
    try:
        if plan["items"]:
            for event in apply_plan(
                engine,
                extractor,
                manifest_path,
                store=store,
                limit=len(plan["items"]),
                batch_size=batch_size,
                retry_failed=retry_failed,
            ):
                if "content_item_id" in event:
                    report["results"].setdefault(event["content_item_id"], {})["construction"] = (
                        event
                    )
                    save_report()
                yield event
        plan = json.loads(manifest_path.read_text())
        for item in plan["items"]:
            cid = item["content_id"]
            result = report["results"].setdefault(cid, {})
            result["construction"] = plan.get("results", {}).get(cid, {"status": "pending"})
            verification = verify_projection(
                engine,
                uuid.UUID(cid),
                version,
                item["fingerprint"],
                store=store,
            )
            result["verification"] = verification
            save_report()
            yield {"stage": "pipeline_verification", **verification}
        counts = Counter(r["verification"]["status"] for r in report["results"].values())
        report["counts"] = dict(selected=len(plan["items"]), **counts)
        report["status"] = (
            "success" if counts.get("verified", 0) == len(plan["items"]) else "incomplete"
        )
        report["completed_at"] = datetime.now(UTC).isoformat()
        save_report()
        yield dict(
            stage="pipeline_summary",
            status=report["status"],
            counts=report["counts"],
            report=str(report_path),
            token_usage=report.get("token_usage"),
        )
    except Exception as exc:
        report.update(
            status="failed",
            error_type=type(exc).__name__,
            completed_at=datetime.now(UTC).isoformat(),
        )
        save_report()
        raise
