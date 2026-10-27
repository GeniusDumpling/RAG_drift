"""Corpus-wide source reconciliation, per-model bundles and exact index verification."""

import fcntl
import json
import os
import shutil
from collections import Counter, defaultdict
from contextlib import closing
from copy import deepcopy
from pathlib import Path

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from qdrant_client import QdrantClient, models

from .bulk import bundle_path
from .common import (
    now,
    read_json,
    read_jsonl,
    seal_bundle,
    uid,
    validate_bundle,
    write_json,
    write_jsonl,
)


def scoped_differences(claims: list[dict], scopes: dict[str, list[str]]) -> tuple[list, dict]:
    """Only compare unambiguous single-model/single-configuration source claims."""
    groups = defaultdict(list)
    stats = Counter()
    per_source = defaultdict(set)
    for c in claims:
        per_source[(c["source"]["document_id"], c["parameter_key"])].add(
            json.dumps(c["value"], sort_keys=True)
        )
    for c in claims:
        scope = scopes[c["source"]["document_id"]]
        if len(scope) != 1:
            stats["shared_model_scope_not_compared"] += 1
            continue
        if (
            not c.get("comparison_eligible", True)
            or len(per_source[(c["source"]["document_id"], c["parameter_key"])]) > 1
        ):
            stats["ambiguous_or_qualified_values_not_compared"] += 1
            continue
        groups[(scope[0], c["parameter_key"])].append(c)
    differences = []
    for (slug, key), candidates in groups.items():
        candidates.sort(key=lambda c: c["id"])
        for i, left in enumerate(candidates):
            for right in candidates[i + 1 :]:
                if left["source"]["document_id"] == right["source"]["document_id"]:
                    continue
                for dimension in ["value", "test_speed_kmh"]:
                    a = left["value"] if dimension == "value" else left["conditions"].get(dimension)
                    b = (
                        right["value"]
                        if dimension == "value"
                        else right["conditions"].get(dimension)
                    )
                    if a is None or b is None or a == b:
                        continue
                    differences.append(
                        {
                            "id": uid("parameter-difference", left["id"], right["id"], dimension),
                            "model_slug": slug,
                            "parameter_key": key,
                            "dimension": dimension,
                            "status": "unresolved",
                            "claims": [left, right],
                            "note": "同一机型的来源记载存在差异；"
                            "保留各自版本与条件，尚未核定统一值。",
                        }
                    )
    stats["differences"] = len(differences)
    return differences, dict(stats)


def reconcile_sources(output: Path, jobs: list[dict]) -> dict:
    from .parameters import comparison_eligible, parameter_values
    from .storage import configuration

    scopes, paths, claims, metadata = {}, [], [], {}
    for job in jobs:
        bundle = bundle_path(output, job)
        validate_bundle(bundle)
        paths.append(bundle)
        for d in read_jsonl(bundle / "documents.jsonl"):
            scopes[d["id"]] = d["model_slugs"]
        for e in read_jsonl(bundle / "elements.jsonl"):
            claim = e["metadata_json"].get("claim")
            if claim:
                claim = deepcopy(claim)
                values = parameter_values(
                    claim["parameter_key"], e["metadata_json"]["parameter_value"]
                )
                claim.update(
                    {
                        "value_variants": values,
                        "comparison_eligible": bool(
                            comparison_eligible(
                                claim["parameter_key"], e["metadata_json"]["parameter_value"]
                            )
                        ),
                    }
                )
                claims.append(claim)
                metadata[e["id"]] = deepcopy(e["metadata_json"])
                metadata[e["id"]]["claim"] = claim
    differences, stats = scoped_differences(claims, scopes)
    by_claim = defaultdict(list)
    for d in differences:
        for c in d["claims"]:
            by_claim[c["id"]].append(d)
    for meta in metadata.values():
        meta["source_differences"] = by_claim[meta["claim"]["id"]]
    # This updates derived comparison metadata, never indexed text or content hashes.
    dsn, _, _ = configuration()
    with psycopg.connect(dsn) as conn:
        for element_id, meta in metadata.items():
            conn.execute(
                "UPDATE document_elements SET metadata_json=%s WHERE id=%s",
                (Jsonb(meta), element_id),
            )
            conn.execute(
                """UPDATE content_chunks SET metadata_json=metadata_json || %s
                WHERE id IN (SELECT chunk_id FROM chunk_elements WHERE element_id=%s)
                AND chunk_type='parameter'""",
                (
                    Jsonb(
                        {"source_differences": meta["source_differences"], "claim": meta["claim"]}
                    ),
                    element_id,
                ),
            )
    for bundle in paths:
        elements = read_jsonl(bundle / "elements.jsonl")
        owned_claims = []
        owned_differences = {}
        for e in elements:
            if e["id"] in metadata:
                e["metadata_json"] = metadata[e["id"]]
                owned_claims.append(e["metadata_json"]["claim"])
                for d in e["metadata_json"]["source_differences"]:
                    owned_differences[d["id"]] = d
        chunks = read_jsonl(bundle / "chunks.jsonl")
        for c in chunks:
            for element_id in c["element_ids"]:
                if element_id in metadata:
                    c["metadata_json"].update(
                        {
                            "source_differences": metadata[element_id]["source_differences"],
                            "claim": metadata[element_id]["claim"],
                        }
                    )
        write_jsonl(bundle / "elements.jsonl", elements)
        write_jsonl(bundle / "chunks.jsonl", chunks)
        write_jsonl(bundle / "parameter_claims.jsonl", owned_claims)
        write_jsonl(bundle / "source_differences.jsonl", list(owned_differences.values()))
        seal_bundle(bundle, read_json(bundle / "manifest.json"))
    write_jsonl(output / "parameter_claims.jsonl", claims)
    write_jsonl(output / "source_differences.jsonl", differences)
    write_json(output / "comparison-report.json", stats)
    return stats


def model_bundle(output: Path, slug: str, plan: dict, *, render=True) -> Path:
    from .extract import quality
    from .report import report

    destination = output / "models" / slug
    destination.mkdir(parents=True, exist_ok=True)
    names = [
        "models",
        "documents",
        "pages",
        "elements",
        "chunks",
        "assets",
        "links",
        "parameter_claims",
        "source_differences",
    ]
    rows = {n: {} for n in names}
    first_manifest = None
    for job in plan["jobs"]:
        if slug not in job["models"]:
            continue
        source = bundle_path(output, job)
        manifest = read_json(source / "manifest.json")
        first_manifest = first_manifest or manifest
        for name in names:
            for row in read_jsonl(source / (name + ".jsonl")):
                key = row.get("id") or (
                    row["from_element_id"],
                    row["to_element_id"],
                    row["relation_type"],
                )
                rows[name][key] = row
        for asset in read_jsonl(source / "assets.jsonl"):
            target = destination / asset["storage_key"]
            if not target.exists():
                target.parent.mkdir(parents=True, exist_ok=True)
                try:
                    os.link(source / asset["storage_key"], target)
                except OSError:
                    shutil.copyfile(source / asset["storage_key"], target)
    if first_manifest is None:
        raise ValueError(f"机型没有任何处理产物: {slug}")
    for name in names:
        write_jsonl(destination / (name + ".jsonl"), list(rows[name].values()))
    manifest = {
        **first_manifest,
        "model_slug": slug,
        "model_name": plan["model_names"][slug],
        "created_at": plan["created_at"],
        "stage": "enriched",
        "batch_root": str(output.resolve()),
        "selection": {"mode": "aggregated_model", "include_specs": True, "document_types": None},
    }
    seal_bundle(destination, manifest)
    write_json(
        destination / "quality_report.json",
        quality(
            list(rows["documents"].values()),
            list(rows["pages"].values()),
            list(rows["elements"].values()),
        ),
    )
    figures = [e for e in rows["elements"].values() if e["element_type"] == "figure"]
    write_json(
        destination / "caption-quality-report.json",
        {
            "quality_version": "caption-grounding-v2",
            "figures": len(figures),
            "accepted": sum(e["metadata_json"].get("status") == "described" for e in figures),
            "quarantined": sum(
                e["metadata_json"].get("quality_gate", {}).get("status") == "quarantined"
                for e in figures
            ),
            "vision_reviewed": sum(
                e["metadata_json"].get("quality_gate", {}).get("verification") == "vision_grounding"
                for e in figures
            ),
            "note": "按该型号关联原图汇总，逐图检查与隔离原因保存在元素元数据中。",
        },
    )
    if render:
        report(destination, export_pdf=False)
    return destination


async def finalize_batch(output: Path) -> dict:
    lock = (output / "batch.lock").open("a")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError as exc:
        lock.close()
        raise ValueError("批次仍有处理或汇总进程正在运行") from exc
    try:
        return await _finalize_batch(output)
    finally:
        fcntl.flock(lock, fcntl.LOCK_UN)
        lock.close()


async def _finalize_batch(output: Path) -> dict:
    from .storage import configuration, query

    plan = read_json(output / "plan.json")
    status = read_json(output / "status.json")
    status.update({"status": "finalizing", "updated_at": now()})
    write_json(output / "status.json", status)
    for job in plan["jobs"]:
        state = read_json(output / "jobs" / (job["id"] + ".json"))
        if state["stages"].get("index") != "done":
            raise ValueError("仍有未完成的文档任务，不能报告全量完成")
    comparisons = reconcile_sources(output, plan["jobs"])
    processings, expected, source_pages, included, figures, quarantined = [], set(), 0, 0, 0, 0
    empty_sources = []
    for job in plan["jobs"]:
        bundle = bundle_path(output, job)
        validate_bundle(bundle)
        processings.extend(d["processing_id"] for d in read_jsonl(bundle / "documents.jsonl"))
        source_chunks = read_jsonl(bundle / "chunks.jsonl")
        expected.update(c["id"] for c in source_chunks)
        if not source_chunks:
            empty_sources.append(
                {"job": job["id"], "title": job["title"], "reason": "no_indexable_chinese_content"}
            )
        q = read_json(bundle / "quality_report.json")
        source_pages += q["pages_total"]
        included += q["pages_included"]
        figures += q.get("figures_total", 0)
        quarantined += q.get("figures_quarantined", 0)
    dsn, qurl, collection = configuration()
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        ready = conn.execute(
            "SELECT count(*) AS n FROM document_processings "
            "WHERE id=ANY(%s::uuid[]) AND status='ready'",
            (processings,),
        ).fetchone()["n"]
        active = conn.execute(
            "SELECT count(*) AS n FROM document_versions "
            "WHERE active_processing_id=ANY(%s::uuid[])",
            (processings,),
        ).fetchone()["n"]
        stored = {
            str(r["id"])
            for r in conn.execute(
                "SELECT id FROM content_chunks WHERE processing_id=ANY(%s::uuid[])", (processings,)
            )
        }
    if ready != len(processings) or active != len(processings) or stored != expected:
        raise ValueError("全量文档发布状态或 PostgreSQL 切片不一致")
    with closing(QdrantClient(url=qurl, timeout=30)) as client:
        points, offset = set(), None
        while True:
            page, offset = client.scroll(
                collection,
                scroll_filter=models.Filter(
                    must=[
                        models.FieldCondition(
                            key="processing_id", match=models.MatchAny(any=processings)
                        )
                    ]
                ),
                offset=offset,
                limit=256,
                with_payload=False,
                with_vectors=False,
            )
            points.update(str(p.id) for p in page)
            if offset is None:
                break
    if points != expected:
        raise ValueError("Qdrant 有效版本点ID与 PostgreSQL 不一致")
    model_results = []
    for slug in plan["configuration"]["models"]:
        bundle = model_bundle(output, slug, plan, render=False)
        question = f"{plan['model_names'][slug]} 的技术参数和使用要求有哪些？"
        result = await query(bundle, question, top_k=3)
        if not result["evidence"]:
            raise ValueError(f"机型检索没有返回证据: {slug}")
        write_json(bundle / "retrieval-report.json", {"queries": [result]})
        from .report import report

        report(bundle, export_pdf=False)
        model_results.append(
            {
                "slug": slug,
                "name": plan["model_names"][slug],
                "evidence_count": len(result["evidence"]),
                "preview": f"models/{slug}/preview.html",
            }
        )
        write_json(output / "model-verification.json", model_results)
    result = {
        "complete": True,
        "models": len(model_results),
        "documents": len(processings),
        "pdfs": plan["unique_pdfs"],
        "source_pages": source_pages,
        "included_pages": included,
        "chunks": len(expected),
        "qdrant_matching_points": len(points),
        "point_ids_match": True,
        "figures": figures,
        "quarantined_descriptions": quarantined,
        "documents_without_indexable_chinese": len(empty_sources),
        "comparison_report": comparisons,
        "database": "drone_documents",
        "collection": collection,
    }
    write_json(output / "verification-report.json", result)
    write_json(output / "language-exclusions.json", empty_sources)
    status.update({"status": "complete", "updated_at": now(), "verification": result})
    write_json(output / "status.json", status)
    lines = [
        "# DJI 其他型号全量处理结果",
        "",
        f"完成 {len(model_results)} 个型号、{len(processings)} 个文档版本，"
        f"{len(expected)} 条有效向量。PostgreSQL 与 Qdrant 点 ID 一致。",
        "",
        "按型号完成一次真实检索检查，不代表每个型号均已完成人工答案准确率验收。",
        "",
        "| 型号 | 预览 |",
        "|---|---|",
    ]
    lines += [f"| {m['name']} | [查看]({m['preview']}) |" for m in model_results]
    (output / "REPORT.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    (output / "STATUS.md").write_text(
        "# DJI 全量处理已完成\n\n详见 [REPORT.md](REPORT.md) "
        "和 [verification-report.json](verification-report.json)。\n",
        encoding="utf-8",
    )
    return result
