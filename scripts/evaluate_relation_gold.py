#!/usr/bin/env python3
"""Evaluate reviewed relation answers using the real extractor or saved outputs; no graph writes."""

import argparse
import copy
import json
import sys
import tempfile
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "backend"))

from app.core.config import Settings  # noqa: E402
from app.schemas.video_graph import ChunkExtraction  # noqa: E402
from app.services.video_graph import LLMVideoExtractor, validate_extraction  # noqa: E402
from app.services.video_graph_identity import stable_hash  # noqa: E402

from scripts.evaluate_entity_gold import (  # noqa: E402
    match_reason,
    material_for,
    measures,
    normalize,
    prediction_entities,
    run_case,
)
from scripts.relation_gold_dataset import (  # noqa: E402
    DEFAULT_DATASET,
    DEFAULT_ENTITIES,
    cell,
    quote_text,
    validate,
)
from scripts.video_graph_response_cache import ModelResponseCache  # noqa: E402

SCORING_RULE = "relation-gold-endpoint-one-to-one-v1"
METRICS = {"triple": "三元组", "assertion": "三元组+断言", "qualified": "三元组+断言+限定（严格）"}
SYMMETRIC = {"compatible_with", "connected_to"}


def one_to_one(adjacency, gold_count):
    """Maximum bipartite matching; neither a prediction nor an answer earns credit twice."""
    assignments = {}

    def augment(pi, seen):
        for gi in adjacency[pi]:
            if gi in seen:
                continue
            seen.add(gi)
            if gi not in assignments or augment(assignments[gi], seen):
                assignments[gi] = pi
                return True
        return False

    for pi in sorted(range(len(adjacency)), key=lambda i: (len(adjacency[i]), i)):
        augment(pi, set())
    used = set(assignments.values())
    return {
        "tp": len(assignments),
        "fp": len(adjacency) - len(assignments),
        "fn": gold_count - len(assignments),
        "matches": [
            {"gold_index": gi, "prediction_index": pi} for gi, pi in sorted(assignments.items())
        ],
        "missed_gold": [gi for gi in range(gold_count) if gi not in assignments],
        "extra_predictions": [pi for pi in range(len(adjacency)) if pi not in used],
    }


def align_entities(case, output):
    """Use names/types/mentions only, never gold relations to choose a component owner."""
    predicted = prediction_entities(output)
    adjacency, scope, ambiguous = [], [], []
    for pi, entity in enumerate(predicted):
        candidates, canonical, recognized = [], [], []
        for gi, gold in enumerate(case["entities"]):
            reason = match_reason(gold, entity, case["models"])
            if reason:
                recognized.append(gi)
                if entity["type"] == gold["type"]:
                    candidates.append(gi)
                    if reason == "canonical":
                        canonical.append(gi)
        options = canonical or candidates
        scope.append(bool(recognized))
        if len(options) > 1:
            ambiguous.append({"prediction_index": pi, "gold_candidates": options})
        # Do not arbitrarily map a shared "camera" mention to one of several owners.
        adjacency.append(options if len(options) == 1 else [])
    matched = one_to_one(adjacency, len(case["entities"]))
    mapping = {p["prediction_index"]: p["gold_index"] for p in matched["matches"]}
    keys = {key: pi for pi, entity in enumerate(predicted) for key in entity["keys"]}
    return (
        {
            "gold": case["entities"],
            "predicted": predicted,
            "matches": matched["matches"],
            "ambiguous_predictions": ambiguous,
        },
        keys,
        mapping,
        scope,
    )


def qualifiers_key(qualifiers):
    # Missing modality and explicit actual have the same meaning in the project schema.
    return tuple(
        sorted(
            (key, normalize(value))
            for key, value in qualifiers.items()
            if not (key == "modality" and value == "actual")
        )
    )


def endpoints(subject, obj, predicate):
    return tuple(sorted((subject, obj))) if predicate in SYMMETRIC else (subject, obj)


def score_relations(case, output):
    alignment, keys, mapping, scope = align_entities(case, output)
    gold = case["relations"]
    predicted, dedup = [], {}
    for index, relation in enumerate(output.relations):
        si, oi = keys[relation.subject], keys[relation.object]
        identity = (
            endpoints(si, oi, relation.predicate),
            relation.predicate,
            relation.assertion,
            qualifiers_key(relation.qualifiers),
        )
        if identity in dedup:
            predicted[dedup[identity]]["source_indices"].append(index)
            continue
        dedup[identity] = len(predicted)
        subject = alignment["predicted"][si]
        obj = alignment["predicted"][oi]
        predicted.append(
            {
                "subject": subject["name"],
                "subject_type": subject["type"],
                "object": obj["name"],
                "object_type": obj["type"],
                "predicate": relation.predicate,
                "assertion": relation.assertion,
                "qualifiers": relation.qualifiers,
                "quote": relation.quote,
                "source_indices": [index],
                "in_scope": scope[si] and scope[oi],
                "gold_subject": case["entities"][mapping[si]]["name"] if si in mapping else None,
                "gold_object": case["entities"][mapping[oi]]["name"] if oi in mapping else None,
            }
        )
    in_scope = [i for i, relation in enumerate(predicted) if relation["in_scope"]]
    detail = {
        "gold": gold,
        "predicted": predicted,
        "entity_alignment": alignment,
        "out_of_scope": [i for i, r in enumerate(predicted) if not r["in_scope"]],
    }
    for metric in METRICS:
        adjacency = []
        for pi in in_scope:
            pred = predicted[pi]
            matches = []
            for gi, answer in enumerate(gold):
                if pred["gold_subject"] is None or pred["gold_object"] is None:
                    continue
                if pred["predicate"] != answer["predicate"]:
                    continue
                if endpoints(
                    pred["gold_subject"], pred["gold_object"], pred["predicate"]
                ) != endpoints(answer["subject"], answer["object"], answer["predicate"]):
                    continue
                if metric != "triple" and pred["assertion"] != answer["assertion"]:
                    continue
                if metric == "qualified" and qualifiers_key(pred["qualifiers"]) != qualifiers_key(
                    answer.get("qualifiers", {})
                ):
                    continue
                matches.append(gi)
            adjacency.append(matches)
        result = one_to_one(adjacency, len(gold))
        # Restore indices into the complete prediction list, including unassessed edges.
        for pair in result["matches"]:
            pair["prediction_index"] = in_scope[pair["prediction_index"]]
        result["extra_predictions"] = [in_scope[i] for i in result["extra_predictions"]]
        detail[metric] = result
    return detail


def score_case(case, result):
    output = ChunkExtraction()
    if result and result["status"] == "success":
        output = ChunkExtraction.model_validate(result["output"])
        material = material_for(case)
        validate_extraction(material.chunks[0], output, material=material, require_review=True)
    return score_relations(case, output)


def score_all(cases, results):
    totals = defaultdict(Counter)
    languages = defaultdict(lambda: defaultdict(Counter))
    predicates = defaultdict(lambda: defaultdict(Counter))
    errors, pending, outside, predicted_count = [], [], 0, 0
    for case in cases:
        result = results.get(case["id"])
        try:
            detail = score_case(case, result)
        except (ValueError, KeyError, TypeError) as exc:
            # Saved booleans and old scores are not trusted; invalid outputs are failed cases.
            result.update(
                status="error",
                error_type=type(exc).__name__,
                error_detail={
                    "code": "invalid_saved_extraction",
                    "message": "已保存抽取结果未通过当前结构、来源或审核凭据校验",
                },
            )
            detail = score_relations(case, ChunkExtraction())
        if result is None:
            pending.append(case["id"])
        elif result["status"] != "success":
            errors.append(case["id"])
        if result is not None:
            result["scoring"] = detail
        outside += len(detail["out_of_scope"])
        predicted_count += len(detail["predicted"])
        for metric in METRICS:
            scored = detail[metric]
            counts = {key: scored[key] for key in ("tp", "fp", "fn")}
            totals[metric].update(counts)
            languages[case["language"]][metric].update(counts)
            for pair in scored["matches"]:
                predicates[detail["gold"][pair["gold_index"]]["predicate"]][metric]["tp"] += 1
            for gi in scored["missed_gold"]:
                predicates[detail["gold"][gi]["predicate"]][metric]["fn"] += 1
            for pi in scored["extra_predictions"]:
                predicates[detail["predicted"][pi]["predicate"]][metric]["fp"] += 1
    return {
        "cases": len(cases),
        "successful_cases": len(cases) - len(errors) - len(pending),
        "errors": errors,
        "pending": pending,
        "predicted_relations": predicted_count,
        "out_of_scope_relations": outside,
        "in_scope_relations": predicted_count - outside,
        "overall": {metric: measures(totals[metric]) for metric in METRICS},
        "by_language": {
            lang: {m: measures(counts[m]) for m in METRICS}
            for lang, counts in sorted(languages.items())
        },
        "by_predicate": {
            p: {m: measures(counts[m]) for m in METRICS} for p, counts in sorted(predicates.items())
        },
    }


def relation_label(relation):
    return (
        f"{relation['subject']} → {relation['predicate']} → {relation['object']} "
        f"[{relation['assertion']}]"
    )


def report_text(dataset, payload):
    metrics = payload["metrics"]
    lines = [
        "# 关系金标准评测",
        "",
        f"抽取版本：`{payload['extraction_version']}`。",
        f"金标准：`{payload['dataset_version']}`；指纹：`{payload['dataset_sha256']}`。",
        f"评分规则：`{payload['scoring_rule']}`；执行方式：`{payload['execution']}`。",
        f"成功 {metrics['successful_cases']}/{metrics['cases']}，"
        f"失败 {len(metrics['errors'])}，待运行 {len(metrics['pending'])}。",
        "",
        "测试完整抽取流程；标题和当前字幕是模型输入，实体及关系答案、models、notes不传给模型。",
        "只评价金标准实体范围内的关系：端点按名称/原文提及与类型一对一对齐，"
        "不靠标准关系猜部件所有者。类型错误或归属歧义仍可产生FP；漏抽端点对应关系计FN。",
        f"预测关系去重后 {metrics['predicted_relations']} 条，"
        f"范围内 {metrics['in_scope_relations']} 条，"
        f"范围外 {metrics['out_of_scope_relations']} 条单列待审，不计TP/FP。"
        "因此本分数不是全图所有预测关系的准确率。",
        "三元组要求两个实体的身份、类型、方向和谓词正确；兼容/连接允许反向，其他关系不允许。",
        "断言指标再要求asserted/uncertain/negated一致。严格限定指标再要求全部qualifiers"
        "逐字归一后相同；缺省modality等价actual。限定同义但措辞/范围不同仍可能记为差异，需人工复核。",
        "quote只检查来源支持，不要求与金标准选择完全相同的引文；本脚本不做LLM语义裁判。"
        "失败及待运行样本仍计入召回分母，未完成结果只是阶段结果。",
        "",
        "| 范围 | 指标 | TP | FP | FN | Precision | Recall | F1 |",
        "|---|---|---:|---:|---:|---:|---:|---:|",
    ]
    for scope, groups in [("全部", metrics["overall"]), *metrics["by_language"].items()]:
        for metric, values in groups.items():
            rates = [
                "—" if values[key] is None else f"{values[key]:.1%}"
                for key in ("precision", "recall", "f1")
            ]
            lines.append(
                f"| {scope} | {METRICS[metric]} | {values['tp']} | {values['fp']} | "
                f"{values['fn']} | {' | '.join(rates)} |"
            )
    lines += [
        "",
        "按谓词统计（三元组+断言）：",
        "",
        "| 谓词 | TP | FP | FN | Precision | Recall | F1 |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    for predicate, groups in metrics["by_predicate"].items():
        values = groups["assertion"]
        rates = [
            "—" if values[k] is None else f"{values[k]:.1%}" for k in ("precision", "recall", "f1")
        ]
        lines.append(
            f"| {predicate} | {values['tp']} | {values['fp']} | {values['fn']} | "
            + " | ".join(rates)
            + " |"
        )
    for case in dataset["cases"]:
        result = payload["results"].get(case["id"])
        lines += [
            "",
            f"## {case['id']} · {case['language']}",
            "",
            f"[{case['source']['title']}]({case['source']['url']})",
            "",
        ]
        if not result:
            lines.append("尚未运行；全部标准关系暂计FN。")
            continue
        if result["status"] != "success":
            lines.append(
                f"执行失败：{result.get('error_detail', {}).get('code', 'execution_error')}；"
                "全部标准关系计FN。"
            )
            continue
        scoring = result["scoring"]
        for title, indices, field in [
            ("漏抽关系（三元组+断言）", scoring["assertion"]["missed_gold"], "gold"),
            (
                "多抽/未匹配关系（三元组+断言）",
                scoring["assertion"]["extra_predictions"],
                "predicted",
            ),
            ("范围外关系（未计分，待审）", scoring["out_of_scope"], "predicted"),
        ]:
            lines += [title + "：", ""]
            for index in indices:
                relation = scoring[field][index]
                qualifier = cell(json.dumps(relation.get("qualifiers", {}), ensure_ascii=False))
                lines += [
                    f"- {cell(relation_label(relation))}",
                    f"  - 限定：{qualifier}",
                    f"  - 证据：{cell(quote_text(relation))}",
                ]
            if not indices:
                lines.append("- 无。")
            lines.append("")
        mismatches = [
            pair
            for pair in scoring["assertion"]["matches"]
            if qualifiers_key(scoring["gold"][pair["gold_index"]].get("qualifiers", {}))
            != qualifiers_key(scoring["predicted"][pair["prediction_index"]]["qualifiers"])
        ]
        if mismatches:
            lines += ["限定差异（断言已匹配）：", ""]
            for pair in mismatches:
                gold = scoring["gold"][pair["gold_index"]]
                pred = scoring["predicted"][pair["prediction_index"]]
                lines += [
                    f"- {cell(relation_label(gold))}",
                    f"  - 标准：{cell(json.dumps(gold.get('qualifiers', {}), ensure_ascii=False))}",
                    f"  - 预测：{cell(json.dumps(pred['qualifiers'], ensure_ascii=False))}",
                ]
        for ambiguous in scoring["entity_alignment"]["ambiguous_predictions"]:
            entity = scoring["entity_alignment"]["predicted"][ambiguous["prediction_index"]]
            names = [case["entities"][i]["name"] for i in ambiguous["gold_candidates"]]
            lines.append(
                f"端点歧义：{cell(entity['name'])} → {cell('、'.join(names))}；未任意分配所有者。"
            )
    return "\n".join(lines) + "\n"


def atomic_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=path.parent,
            prefix=".relation-evaluation-",
            delete=False,
        ) as handle:
            temporary = Path(handle.name)
            json.dump(value, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
        temporary.replace(path)
    finally:
        if temporary:
            temporary.unlink(missing_ok=True)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=Path, default=DEFAULT_DATASET)
    parser.add_argument("--entities", type=Path, default=DEFAULT_ENTITIES)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--report", type=Path)
    parser.add_argument(
        "--predictions", type=Path, help="离线读取已有实体/关系评测的results输出，不调用模型"
    )
    parser.add_argument("--response-cache", type=Path)
    parser.add_argument("--cache-only", action="store_true")
    parser.add_argument("--retry-errors", action="store_true")
    parser.add_argument("--case-id", action="append")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--workers", type=int, default=2)
    args = parser.parse_args(argv)
    if not 1 <= args.workers <= 4 or (args.cache_only and not args.response_cache):
        parser.error("workers须为1–4；cache-only需要response-cache")
    if args.predictions and (args.response_cache or args.cache_only or args.retry_errors):
        parser.error("predictions是离线计分，不与模型缓存或重试参数组合使用")
    protected = {args.dataset.resolve(), args.entities.resolve()}
    if args.predictions:
        protected.add(args.predictions.resolve())
    if args.output.resolve() in protected or (
        args.report and args.report.resolve() in protected | {args.output.resolve()}
    ):
        parser.error("输出和报告不能覆盖金标准、输入预测文件或彼此")
    dataset = json.loads(args.dataset.read_text())
    entity_dataset = json.loads(args.entities.read_text())
    validation = validate(dataset, entity_dataset)
    if args.case_id:
        selected = set(args.case_id)
        unknown = selected - {case["id"] for case in dataset["cases"]}
        if unknown:
            parser.error("未知case-id: " + ", ".join(sorted(unknown)))
        dataset = {**dataset, "cases": [c for c in dataset["cases"] if c["id"] in selected]}
    if args.dry_run:
        print(
            json.dumps(
                {
                    "dataset_version": dataset["version"],
                    "status": validation["status"],
                    "cases": len(dataset["cases"]),
                    "gold_relations": sum(len(c["relations"]) for c in dataset["cases"]),
                    "execution": "offline_predictions" if args.predictions else "extractor",
                    "input_scope": "title_and_current_chunk_only",
                    "scoring_scope": "relations_between_gold_entities",
                    "model_called": False,
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0
    if dataset["status"] != "human_reviewed":
        parser.error("请先完成人工复核并将status设为human_reviewed")
    imported = json.loads(args.predictions.read_text()) if args.predictions else None
    if args.predictions is not None and (
        not isinstance(imported, dict) or not isinstance(imported.get("results"), dict)
    ):
        parser.error("predictions必须含有按case ID索引的results对象，与实体评测输出格式相同")
    settings = None if imported is not None else Settings()
    version = (
        imported.get("extraction_version", "unspecified_import")
        if imported is not None
        else LLMVideoExtractor(settings).version
    )
    scorer_files = [
        Path(__file__),
        ROOT / "scripts/evaluate_entity_gold.py",
        ROOT / "scripts/relation_gold_dataset.py",
        ROOT / "scripts/entity_gold_dataset.py",
    ]
    metadata = {
        "dataset_version": dataset["version"],
        "dataset_sha256": stable_hash(dataset),
        "entity_dataset_sha256": stable_hash(entity_dataset),
        "extraction_version": version,
        "scoring_rule": SCORING_RULE,
        "scorer_sha256": stable_hash({p.name: p.read_text() for p in scorer_files}),
        "input_scope": "title_and_current_chunk_only",
        "scoring_scope": "relations_between_gold_entities",
        "execution": "offline_predictions" if imported is not None else "extractor",
        "predictions_sha256": stable_hash(imported) if imported is not None else None,
    }
    payload = {**metadata, "results": {}}
    if args.output.exists():
        saved = json.loads(args.output.read_text())
        if any(saved.get(k) != v for k, v in metadata.items()):
            parser.error("输出属于其他标注、输入预测、抽取版本或评分规则，请使用新文件名")
        payload = saved
    cases, results = dataset["cases"], payload["results"]

    def save():
        payload["metrics"] = score_all(cases, results)
        payload["updated_at"] = datetime.now(UTC).isoformat()
        atomic_json(args.output, payload)
        if args.report:
            args.report.parent.mkdir(parents=True, exist_ok=True)
            args.report.write_text(report_text(dataset, payload))

    if imported is not None:
        results.clear()
        for case in cases:
            if case["id"] in imported["results"]:
                result = imported["results"][case["id"]]
                if not isinstance(result, dict) or result.get("status") not in {"success", "error"}:
                    parser.error(f"{case['id']}: 预测记录需要success/error状态")
                results[case["id"]] = copy.deepcopy(result)
        save()
    else:
        # Revalidate saved outputs before deciding whether a failed case needs retrying.
        score_all(cases, results)
        pending = [
            case
            for case in cases
            if case["id"] not in results
            or (args.retry_errors and results[case["id"]]["status"] == "error")
        ]
        cache = (
            ModelResponseCache(args.response_cache, cache_only=args.cache_only)
            if args.response_cache
            else None
        )
        with ThreadPoolExecutor(max_workers=args.workers) as pool:
            futures = {
                pool.submit(
                    run_case,
                    case,
                    settings,
                    cache,
                    results.get(case["id"], {}).get("attempt", -1) + 1,
                ): case["id"]
                for case in pending
            }
            for future in as_completed(futures):
                case_id, result = futures[future], future.result()
                previous = results.get(case_id)
                if previous:
                    result["prior_attempts"] = previous.get("prior_attempts", []) + [
                        {
                            k: v
                            for k, v in previous.items()
                            if k not in {"output", "scoring", "prior_attempts"}
                        }
                    ]
                results[case_id] = result
                save()
                print(json.dumps({"case_id": case_id, "status": result["status"]}), flush=True)
        save()
    print(json.dumps(payload["metrics"], ensure_ascii=False, indent=2))
    return 1 if payload["metrics"]["errors"] or payload["metrics"]["pending"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
