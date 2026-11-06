#!/usr/bin/env python3
"""Run frozen source spans through the real extractor, without publishing graph data."""

import argparse
import json
import re
import sys
import uuid
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from copy import deepcopy
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "backend"))

from app.core.config import Settings  # noqa: E402
from app.schemas.video_graph import ChunkExtraction  # noqa: E402
from app.services.video_graph import (  # noqa: E402
    LLMVideoExtractor,
    VideoChunk,
    VideoMaterial,
    locate_quote,  # noqa: E402
    validate_extraction,
)
from app.services.video_graph_diagnostics import describe_graph_error  # noqa: E402
from app.services.video_graph_identity import (  # noqa: E402
    compact_name,
    identity_name,
    input_hash,
    stable_hash,
)

from scripts.video_graph_response_cache import (  # noqa: E402
    CachedEvaluationExtractor,
    ModelResponseCache,
)

DEFAULT_DATASET = ROOT / "scripts/evaluation_data/video_graph_quality_cases.json"
EVALUATION_PROTOCOL = "one-extraction-per-source-v1"


def material_for(case):
    chunk = VideoChunk(
        uuid.UUID(case["chunk_id"]),
        case["text"],
        "transcript_segment",
        case["start_seconds"],
        case["end_seconds"],
    )
    return VideoMaterial(
        uuid.UUID(case["video_id"]),
        case["url"],
        case["title"],
        case["url"],
        "",
        None,
        None,
        (chunk,),
    )


def source_groups(cases):
    groups = {}
    for case in sorted(cases, key=lambda row: row["case_id"]):
        material = material_for(case)
        key = input_hash(material, material.chunks[0], "")
        groups.setdefault(key, []).append(case)
    return list(groups.values())


def retarget_result(row, case):
    result = deepcopy(row)
    result["source_case_id"] = row["case_id"]
    material = material_for(case)
    result["source_input_hash"] = input_hash(material, material.chunks[0], "")
    result.update(case_id=case["case_id"], expectation=case["expectation"])
    if row["status"] == "success":
        extraction = ChunkExtraction.model_validate(row["output"])
        result.update(target_present=target_present(case, extraction),
                      retention_proof=retention_proof(case, extraction))
    return result


def retention_proof(case, extraction):
    """Return concrete edge indices; membership paths never infer sibling membership."""
    target = case["baseline_relation"]

    def names(name, kind):
        normalized = identity_name(name, kind, case["title"])
        values = {compact_name(name), compact_name(normalized)}
        if kind == "product" and normalized.startswith("DJI "):
            values.add(compact_name(normalized.removeprefix("DJI ")))
        return values - {""}

    def entity_names(entity):
        values = names(entity.surface_text, entity.entity_type) | names(
            entity.canonical_name or "", entity.entity_type
        )
        if entity.entity_type == target["object_type"] and entity.entity_type in {
            "fault", "component"
        }:
            # Keep complete descriptive phrases; component owners may prefix the
            # noun, but an owner's name alone never matches another component.
            surfaces = [" ".join(re.findall(r"[^\W_]+", value.casefold())) for value in
                        (entity.surface_text, entity.canonical_name or "")]
            for mention in [target["object"], *case.get("object_mentions", [])]:
                phrase = " ".join(re.findall(r"[^\W_]+", mention.casefold()))
                ending = "$" if entity.entity_type == "component" else r"(?![a-z0-9])"
                pattern = r"(?<![a-z0-9])" + re.escape(phrase) + ending
                if any(re.search(pattern, surface, re.I) for surface in surfaces):
                    values |= names(mention, entity.entity_type)
        return values

    subjects = names(target["subject"], target["subject_type"])
    objects = names(target["object"], target["object_type"])
    for mention in case.get("subject_mentions", []):
        subjects |= names(mention, target["subject_type"])
    for mention in case.get("object_mentions", []):
        objects |= names(mention, target["object_type"])
    entities = {e.key: e for e in extraction.entities}
    for index, relation in enumerate(extraction.relations):
        reverse = relation.predicate in case.get("equivalent_reverse_predicates", [])
        allowed = relation.predicate in case.get("equivalent_predicates", [target["predicate"]])
        if not allowed and not reverse:
            continue
        if case["expectation"] != "issue" and relation.assertion != target["assertion"]:
            continue
        subject, obj = entities[relation.subject], entities[relation.object]
        if relation.predicate in {"compatible_with", "connected_to"}:
            pairs = [(subject, obj), (obj, subject)]
        else:
            pairs = [(obj, subject)] if reverse else [(subject, obj)]
        for subject, obj in pairs:
            if subjects & entity_names(subject) and objects & entity_names(obj):
                return {"kind": "direct", "edges": [index]}
    if case["expectation"] == "supported" and target["predicate"] == "contains":
        starts = {key for key, e in entities.items() if subjects & entity_names(e)}
        ends = {key for key, e in entities.items() if objects & entity_names(e)}
        queue = [(key, []) for key in starts]
        visited = set(starts)
        for current, path in queue:
            if len(path) == 3:
                continue
            for index, relation in enumerate(extraction.relations):
                if (
                    relation.predicate != "contains"
                    or relation.assertion != target["assertion"]
                    or relation.subject != current
                ):
                    continue
                proof = path + [index]
                if relation.object in ends:
                    return {"kind": "composition", "edges": proof}
                if relation.object not in visited:
                    visited.add(relation.object)
                    queue.append((relation.object, proof))
    parts = case.get("composite_object_parts", [])
    if case["expectation"] == "supported" and parts:
        found = []
        for part in parts:
            part_names = names(part, target["object_type"])
            for mention in case.get("composite_object_mentions", {}).get(part, []):
                part_names |= names(mention, target["object_type"])
            indices = [
                i
                for i, r in enumerate(extraction.relations)
                if subjects & entity_names(entities[r.subject])
                and r.predicate in {"uses", "supports", "contains"}
                and r.assertion == target["assertion"]
                and part_names & entity_names(entities[r.object])
            ]
            if not indices:
                return None
            found.append(indices[0])
        return {"kind": "explicit_composite", "edges": found}
    return None


def target_present(case, extraction):
    return retention_proof(case, extraction) is not None


def refresh_scores(cases, results):
    """Validate saved raw outputs; cached booleans are never scoring authority."""
    for case in cases:
        result = results.get(case["case_id"])
        if not result or result["status"] != "success":
            continue
        if result["expectation"] != case["expectation"]:
            raise ValueError("验收标签与已运行样本不一致")
        material = material_for(case)
        extraction = ChunkExtraction.model_validate(result["output"])
        validate_extraction(material.chunks[0], extraction, material=material, require_review=True)
        result["target_present"] = target_present(case, extraction)
        result["retention_proof"] = retention_proof(case, extraction)


def run_case(case, settings, cache=None, cache_attempt=0):
    material = material_for(case)
    extractor = (CachedEvaluationExtractor(settings, cache=cache,
                                          namespace=f"{case['case_id']}:attempt{cache_attempt}")
                 if cache else LLMVideoExtractor(settings))
    started = datetime.now(UTC)
    try:
        extraction = extractor.extract(material, material.chunks[0], "")
        result = {
            "case_id": case["case_id"],
            "status": "success",
            "target_present": target_present(case, extraction),
            "retention_proof": retention_proof(case, extraction),
            "expectation": case["expectation"],
            "output": extraction.model_dump(),
            "seconds": (datetime.now(UTC) - started).total_seconds(),
        }
    except Exception as exc:
        # Provider exceptions may contain sensitive URLs/body; archive only the type.
        result = {
            "case_id": case["case_id"],
            "status": "error",
            "error_type": type(exc).__name__,
            "error_detail": describe_graph_error(
                exc, stage="extraction", chunk=material.chunks[0], locate=locate_quote
            ),
            "expectation": case["expectation"],
            "seconds": (datetime.now(UTC) - started).total_seconds(),
        }
    if cache:
        result.update(cache_attempt=cache_attempt, cache_stats=extractor.cache_stats,
                      cache_trace=extractor.cache_trace)
    return result


def score(cases, results):
    positives = [c for c in cases if c["expectation"] == "supported"]
    negatives = [c for c in cases if c["expectation"] == "issue"]
    pending = [c["case_id"] for c in cases if c["case_id"] not in results]
    errors = [
        c["case_id"]
        for c in cases
        if c["case_id"] in results and results[c["case_id"]]["status"] != "success"
    ]
    retained = sum(results.get(c["case_id"], {}).get("target_present", False) for c in positives)
    rejected = sum(
        results.get(c["case_id"], {}).get("status") == "success"
        and not results[c["case_id"]]["target_present"]
        for c in negatives
    )
    positive_rate = retained / len(positives) if positives else None
    negative_rate = rejected / len(negatives) if negatives else None
    return {
        "cases": len(cases),
        "successful_cases": len(cases) - len(errors) - len(pending),
        "errors": errors,
        "pending": pending,
        "supported_targets": len(positives),
        "supported_retained": retained,
        "supported_retention": positive_rate,
        "bad_targets": len(negatives),
        "bad_targets_rejected": rejected,
        "bad_target_rejection": negative_rate,
        "review_cases": len(cases) - len(positives) - len(negatives),
        "gate": not errors
        and not pending
        and positive_rate is not None
        and positive_rate >= 0.8
        and negative_rate is not None
        and negative_rate >= 0.9,
        "gate_scope": (
            "Frozen target retention/rejection only; "
            "new output relations require separate source review."
        ),
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=Path, default=DEFAULT_DATASET)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--workers", type=int, default=2)
    parser.add_argument("--case-id", action="append")
    parser.add_argument("--retry-errors", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--response-cache", type=Path,
                        help="可选：按精确请求记录并复用本地模型响应，保留错误重试轮次")
    parser.add_argument("--cache-only", action="store_true",
                        help="只重放已记录的精确请求；缺失响应时报错，不调用模型")
    args = parser.parse_args(argv)
    if not 1 <= args.workers <= 8:
        parser.error("--workers must be between 1 and 8")
    if args.cache_only and not args.response_cache:
        parser.error("--cache-only requires --response-cache")
    dataset = json.loads(args.dataset.read_text())
    all_cases = dataset["cases"]
    cases = [c for c in all_cases if not args.case_id or c["case_id"] in args.case_id]
    if args.case_id and set(args.case_id) - {c["case_id"] for c in cases}:
        parser.error("unknown case ID")
    settings = Settings()
    groups = [group for group in source_groups(all_cases)
              if any(case["case_id"] in {c["case_id"] for c in cases} for case in group)]
    if args.dry_run:
        print(
            json.dumps(
                {
                    "cases": len(cases),
                    "distinct_sources": len(groups),
                    "minimum_model_calls": len(groups) * 2,
                    "maximum_stage_calls_without_repair": len(groups) * 18,
                    "maximum_http_attempts": len(groups) * 72,
                    "expectations": dict(Counter(c["expectation"] for c in cases)),
                },
                ensure_ascii=False,
            )
        )
        return 0
    extractor = LLMVideoExtractor(settings)
    payload = {
        "dataset_hash": stable_hash(dataset),
        "target_version": extractor.version,
        "started_at": datetime.now(UTC).isoformat(),
        "results": {},
        "evaluation_protocol": EVALUATION_PROTOCOL,
    }
    if args.output.exists():
        previous = json.loads(args.output.read_text())
        if any(previous.get(k) != payload[k] for k in (
            "dataset_hash", "target_version", "evaluation_protocol"
        )):
            parser.error(
                "output belongs to a different dataset or extraction policy; "
                "select a new output path"
            )
        payload = previous
    results = payload["results"]
    refresh_scores(all_cases, results)
    payload["scorer_hash"] = stable_hash(Path(__file__).read_text())
    pending = [group for group in groups if any(
        case["case_id"] not in results
        or (args.retry_errors and results[case["case_id"]]["status"] == "error")
        for case in group
    )]
    args.output.parent.mkdir(parents=True, exist_ok=True)
    cache = (ModelResponseCache(args.response_cache, cache_only=args.cache_only)
             if args.response_cache else None)
    payload["last_run_response_cache"] = str(args.response_cache) if cache else None

    def save():
        payload["metrics"] = score(all_cases, results)
        payload["updated_at"] = datetime.now(UTC).isoformat()
        temporary = args.output.with_suffix(".tmp")
        temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
        temporary.replace(args.output)

    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = {
            pool.submit(run_case, group[0], settings, cache,
                        max(results.get(case["case_id"], {}).get("cache_attempt", -1)
                            for case in group) + 1): group
            for group in pending
        }
        for future in as_completed(futures):
            row = future.result()
            for case in futures[future]:
                targeted = retarget_result(row, case)
                previous = results.get(case["case_id"])
                if previous is not None:
                    targeted["prior_attempts"] = previous.get("prior_attempts", []) + [
                        {k: v for k, v in previous.items() if k not in {"output", "prior_attempts"}}
                    ]
                results[case["case_id"]] = targeted
            save()
            print(
                json.dumps({k: row[k] for k in row if k != "output"}, ensure_ascii=False),
                flush=True,
            )
    save()
    print(json.dumps(payload["metrics"], ensure_ascii=False), flush=True)
    # A deliberately selected smoke subset cannot claim the full acceptance gate.
    return (
        0
        if (not args.case_id and payload["metrics"]["gate"])
        or (args.case_id and all(results[c["case_id"]]["status"] == "success" for c in cases))
        else 1
    )


if __name__ == "__main__":
    raise SystemExit(main())
