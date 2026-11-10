#!/usr/bin/env python3
"""Explain strict entity FP/FN without changing gold answers or the scoring rules."""

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "backend")]

from app.schemas.video_graph import ChunkExtraction  # noqa: E402
from app.services.video_graph.identity import stable_hash  # noqa: E402
from app.services.video_graph.review import (  # noqa: E402
    EntityVerdict,
    result_hash,
    valid_review_items,
)

from scripts.entity_gold_dataset import DEFAULT_DATASET, case_text, validate  # noqa: E402
from scripts.evaluate_entity_gold import (  # noqa: E402
    SCORING_RULE,
    match_reason,
    matching,
    normalize,
    owner_models,
    prediction_entities,
)
from scripts.video_graph_response_cache import ModelResponseCache  # noqa: E402

CATEGORIES = {
    "missed": "漏识别",
    "type": "类型错误",
    "owner": "归属错误",
    "boundary": "边界/命名差异",
    "selection": "价值筛选",
    "review": "待判读",
}


def proposal_review(row, cache, *, expected_text=None):
    if not cache:
        return [], {}
    for trace in row.get("cache_trace", []):
        if trace["phase"] != "review":
            continue
        path = cache / (trace["request_hash"] + ".json")
        if not path.exists():
            continue
        envelope = json.loads(path.read_text())
        identity = {key: envelope[key] for key in ("namespace", "endpoint_hash", "request")}
        if stable_hash(identity) != trace["request_hash"]:
            raise ValueError("候选审核缓存与请求指纹不一致")
        response = ModelResponseCache._load(path, identity)
        messages = envelope["request"]["messages"]
        data = json.loads(next(m["content"] for m in messages if m["role"] == "user"))
        if data.get("entities") and not data.get("relations"):
            if expected_text is not None and data.get("current_chunk") != expected_text:
                raise ValueError("候选审核缓存不属于当前片段")
            keys = {e["key"] for e in data["entities"]}
            return data["entities"], valid_review_items(
                response.get("entities"),
                EntityVerdict,
                "key",
                keys,
            )
    return [], {}


def classify_errors(case, predicted, proposals=(), verdicts=None):
    """Only exact type/owner evidence is auto-paired; ambiguous cases stay for source review."""
    gold = case["entities"]
    typed = matching(gold, predicted, case["models"], typed=True)
    recognition = matching(gold, predicted, case["models"], typed=False)
    missing, extra = set(typed["missed_gold"]), set(typed["extra_predictions"])
    errors = []

    def add(gi, pi, category, note):
        errors.append(
            {
                "id": f"{case['id']}:g{gi}:p{pi}",
                "case_id": case["id"],
                "gold": gold[gi] if gi is not None else None,
                "predicted": predicted[pi] if pi is not None else None,
                "category": category,
                "note": note,
            }
        )
        missing.discard(gi)
        extra.discard(pi)

    for pair in recognition["matches"]:
        gi, pi = pair["gold_index"], pair["prediction_index"]
        if gi in missing and pi in extra and gold[gi]["type"] != predicted[pi]["type"]:
            add(gi, pi, "type", "名称/原文叫法可严格匹配，但类型不同。")
    for gi in sorted(missing.copy()):
        options = []
        for pi in sorted(extra):
            a, b = gold[gi], predicted[pi]
            first, second = (
                owner_models(a["name"], case["models"]),
                owner_models(b["name"], case["models"]),
            )
            # Another gold component may already belong to this predicted owner,
            # with a different boundary. Shared words alone cannot disprove ownership.
            if any(
                other["type"] == b["type"] and owner_models(other["name"], case["models"]) & second
                for other in gold
            ):
                continue
            if (
                first
                and second
                and first.isdisjoint(second)
                and ({normalize(v) for v in a["mentions"]} & {normalize(v) for v in b["mentions"]})
            ):
                options.append(pi)
        if len(options) == 1:
            add(gi, options[0], "owner", "原文叫法相同，但规范名明确绑定到另一型号。")
    verdicts = verdicts or {}
    for gi in sorted(missing):
        candidates = [
            p
            for p in proposals
            if match_reason(
                gold[gi],
                {
                    "name": p.get("canonical_name") or p["surface_text"],
                    "mentions": [p["surface_text"]],
                },
                case["models"],
            )
        ]
        rejected = [
            verdicts[p["key"]]
            for p in candidates
            if verdicts.get(p["key"], {}).get("accepted") is False
        ]
        if rejected:
            add(
                gi,
                None,
                "selection",
                "候选已提出，实体审核拒绝："
                + "；".join(str(r.get("reason", "")) for r in rejected),
            )
        else:
            note = (
                "候选已提出；需检查边界修正、身份、合并或程序校验。"
                if candidates
                else "候选输入未找到严格对应项；可能漏识别，也可能是边界/命名差异。"
            )
            add(gi, None, "review", note)
    for pi in sorted(extra):
        add(None, pi, "review", "严格评分中的多抽；需区分价值筛选、边界、别名和身份错误。")
    return errors, typed


def analyze(dataset, evaluation, cache=None):
    if evaluation.get("dataset_sha256") != stable_hash(dataset):
        raise ValueError("评测与当前金标准不一致")
    if evaluation.get("scoring_rule") != SCORING_RULE:
        raise ValueError("评测评分口径不同")
    errors, totals, failed = [], Counter(tp=0, fp=0, fn=0), []
    for case in dataset["cases"]:
        row = evaluation["results"].get(case["id"])
        if not row or row["status"] != "success":
            failed.append(case["id"])
            totals["fn"] += len(case["entities"])
            errors.extend(
                {
                    "id": f"{case['id']}:g{index}:pNone",
                    "case_id": case["id"],
                    "gold": entity,
                    "predicted": None,
                    "category": "review",
                    "note": "片段执行失败或待运行；FN计入原评分，不能直接归因于实体漏识别。",
                }
                for index, entity in enumerate(case["entities"])
            )
            continue
        output = ChunkExtraction.model_validate(row["output"])
        if not output.review or output.review.result_hash != result_hash(output):
            raise ValueError("原始抽取输出与来源凭据的结果指纹不一致")
        proposals, verdicts = proposal_review(row, cache, expected_text=case_text(case))
        items, score = classify_errors(case, prediction_entities(output), proposals, verdicts)
        errors.extend(items)
        totals.update({key: score[key] for key in ("tp", "fp", "fn")})
    return {
        "evaluation_sha256": stable_hash(evaluation),
        "dataset_sha256": stable_hash(dataset),
        "extraction_version": evaluation["extraction_version"],
        "scoring_rule": SCORING_RULE,
        "strict_counts": dict(totals),
        "failed_or_pending_cases": failed,
        "errors": errors,
    }


def apply_review(expected, saved):
    def fixed(payload):
        return {
            **payload,
            "errors": [
                {k: v for k, v in row.items() if k not in {"category", "note"}}
                for row in payload["errors"]
            ],
        }

    if fixed(expected) != fixed(saved):
        raise ValueError("错误清单或原始输入已变化，使用新的分析文件名")
    for row in saved["errors"]:
        if row["category"] not in CATEGORIES or not row["note"].strip():
            raise ValueError("错误分类必须使用已定义类别并保留依据")
    return saved


def report(payload):
    rows = payload["errors"]
    counts = Counter(r["category"] for r in rows)
    lines = [
        "# 实体错误分类",
        "",
        f"抽取版本：`{payload['extraction_version']}`。",
        "",
        "分组只解释原有严格FP/FN，不修改匹配结果；一项类型/归属错误可同时贡献一个FP和FN。",
        "自动确定类型及明确所有者冲突；其余需读原文判读，不能把全部未匹配项视为幻觉。",
        "分类为助手分析，未经用户逐条确认；样本为开发集。",
        "",
        f"严格计数：{payload['strict_counts']}；失败/待运行：{payload['failed_or_pending_cases']}。",
        "",
        "| 分类 | 错误项 | 对应FP | 对应FN |",
        "|---|---:|---:|---:|",
    ]
    for key, label in CATEGORIES.items():
        group = [r for r in rows if r["category"] == key]
        lines.append(
            f"| {label} | {counts[key]} | {sum(r['predicted'] is not None for r in group)} | "
            f"{sum(r['gold'] is not None for r in group)} |"
        )
    for row in rows:

        def label(entity):
            return f"{entity['name']} [{entity['type']}]" if entity else "—"

        lines += [
            "",
            f"- **{row['id']} · {CATEGORIES[row['category']]}**："
            f"标准 {label(row['gold'])}；预测 {label(row['predicted'])}。{row['note']}",
        ]
    return "\n".join(lines) + "\n"


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=Path, default=DEFAULT_DATASET)
    parser.add_argument("--evaluation", type=Path, required=True)
    parser.add_argument("--response-cache", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args(argv)
    if len({p.resolve() for p in [args.dataset, args.evaluation, args.output, args.report]}) != 4:
        parser.error("标注、评测、错误分析和报告必须使用不同路径")
    dataset = json.loads(args.dataset.read_text())
    validate(dataset)
    payload = analyze(dataset, json.loads(args.evaluation.read_text()), args.response_cache)
    if args.output.exists():
        payload = apply_review(payload, json.loads(args.output.read_text()))
    for path in [args.output, args.report]:
        path.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
    args.report.write_text(report(payload))
    print(
        json.dumps(
            {
                "strict_counts": payload["strict_counts"],
                "categories": dict(Counter(r["category"] for r in payload["errors"])),
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
