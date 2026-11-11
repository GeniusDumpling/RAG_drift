#!/usr/bin/env python3
"""Evaluate fixed manual fact/image targets; unannotated facts are not false positives."""

import argparse
import asyncio
import html
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "backend"), str(ROOT)]
from app.core.config import Settings  # noqa: E402
from app.services.document_graph.client import VisionClient  # noqa: E402
from app.services.document_graph.extraction import policy_id, review  # noqa: E402
from app.services.document_graph.grounding import normalized, validate_result  # noqa: E402
from app.services.document_graph.schema import Proposal  # noqa: E402
from app.services.document_graph.source import (  # noqa: E402
    load_bundle,
    read_json,
    save_json,
    sha256,
)


def same_name(entity, aliases):
    return any(
        normalized(entity.get(k, "")) == normalized(alias)
        for alias in aliases
        for k in ("surface_text", "canonical_name")
    )


def semantic_match(target, fact, entities):
    p = fact["proposal"]
    if p["predicate"] != target["predicate"] or p.get("assertion", "asserted") != "asserted":
        return False
    if not same_name(entities[p["subject"]], target["subject"]):
        return False
    if "object" in target and (
        not p.get("object") or not same_name(entities[p["object"]], target["object"])
    ):
        return False
    if "attribute" in target and normalized(p.get("attribute") or "") not in {
        normalized(a) for a in target["attribute"]
    }:
        return False
    if "value_contains" in target and not any(
        normalized(s) in normalized(p.get("value") or "") for s in target["value_contains"]
    ):
        return False
    if any(p.get(k) != target[k] for k in ("led_pattern", "operating_state") if k in target):
        return False
    qualifiers = normalized(" ".join(p.get("qualifiers", {}).values()))
    return all(normalized(s) in qualifiers for s in target.get("qualifier_contains", []))


def iou(a, b):
    def area(r):
        return max(0, r[2] - r[0]) * max(0, r[3] - r[1])

    overlap = area([max(a[0], b[0]), max(a[1], b[1]), min(a[2], b[2]), min(a[3], b[3])])
    return overlap / (area(a) + area(b) - overlap) if overlap else 0


def link_checks(target, fact, entities):
    expected = target["evidence"]
    # Endpoint evidence may supply the part's native name. The fact's image itself
    # must match; borrowing the endpoint image would hide a wrong-picture link.
    p = fact["proposal"]
    closure = fact["source"] + entities[p["subject"]].get("source", [])
    if p.get("object"):
        closure += entities[p["object"]].get("source", [])
    quote = normalized("\n".join(r["quote"] for r in closure if r["page"] == expected["page"]))
    checks = dict(
        page=any(r["page"] == expected["page"] for r in fact["source"]),
        native_text=all(normalized(s) in quote for s in expected.get("text_contains", [])),
    )
    if "bbox" in expected:
        checks["image_region"] = any(
            iou(r["bbox"], expected["bbox"]) >= 0.5
            for ev in fact["source"]
            if ev["page"] == expected["page"]
            for r in ev["regions"]
        )
    if "visual_label" in expected:
        checks["visual_label"] = any(
            re.findall(r"\d+", ev.get("visual_label") or "") == [expected["visual_label"]]
            for ev in fact["source"]
            if ev["page"] == expected["page"]
        )
    return checks


def evaluate(bundle, gold, results, negatives=None, *, root=None):
    actual = {(m["sha256"], m["manual_version"]) for m in bundle["manuals"]}
    if actual != {(m["sha256"], m["manual_version"]) for m in gold["manuals"]}:
        raise ValueError("golden手册版本或PDF哈希与实际来源不符")
    rows, matched, linked, covered, linked_targets = [], 0, 0, 0, 0
    for group in gold["cases"]:
        cases = [c for c in bundle["cases"] if c.get("group_id", c["id"]) == group["id"]]
        for target in group["targets"]:
            predictions = []
            for case in cases:
                output = results.get(case["id"], {}).get("output")
                if not output:
                    continue
                validate_result(bundle, case, output, output["review"]["policy"], root=root)
                entities = {e["key"]: e for e in output["entities"]}
                for fact in output["facts"]:
                    if semantic_match(target, fact, entities):
                        checks = link_checks(target, fact, entities)
                        predictions.append(
                            dict(
                                case_id=case["id"],
                                index=fact["index"],
                                checks=checks,
                                linked=all(checks.values()),
                                subject=entities[fact["proposal"]["subject"]]["surface_text"],
                                object=entities[fact["proposal"]["object"]]["surface_text"]
                                if fact["proposal"].get("object")
                                else None,
                                proposal=fact["proposal"],
                                evidence=fact["source"],
                            )
                        )
            matched += len(predictions)
            linked += sum(p["linked"] for p in predictions)
            covered += bool(predictions)
            passed = bool(predictions) and all(p["linked"] for p in predictions)
            linked_targets += passed
            rows.append(
                dict(group_id=group["id"], target=target, predictions=predictions, passed=passed)
            )
    negatives = negatives or {}
    negative_rows = []
    for case in gold["negative_associations"]:
        result = negatives.get(case["id"], {})
        decisions = result.get("output", {}).get("review", {}).get("decisions", {}).get("facts", [])
        denied = len(decisions) == 1 and decisions[0].get("supported") is False
        grounding_denied = any(
            r["kind"] == "fact"
            and r["reason"]
            in {
                "灯态值原文行不属于所引用图片表格",
                "灯态模式与原始矢量/图标像素测量不符",
                "编号原文坐标不在所引用的图片区域内",
                "操作原文与引用图片属于不同章节",
            }
            for r in result.get("output", {}).get("rejected", [])
        )
        negative_rows.append(
            dict(
                id=case["id"],
                reason=case["reason"],
                passed=denied or grounding_denied,
                model_rejected=denied,
                grounding_rejected=grounding_denied,
                decisions=decisions,
                rejected=result.get("output", {}).get("rejected", []),
                error_type=result.get("error_type"),
            )
        )
    image_rows = [r for r in rows if "bbox" in r["target"]["evidence"]]
    image_predictions = [p for r in image_rows for p in r["predictions"]]
    expected_windows = {c["id"] for c in bundle["cases"]}
    success = {cid for cid, r in results.items() if r.get("status") == "success"}
    errors = {cid: r.get("error_type") for cid, r in results.items() if r.get("status") == "error"}
    pending = sorted(expected_windows - set(results))
    return dict(
        execution=dict(
            status="complete" if success == expected_windows else "incomplete",
            windows=len(expected_windows),
            successful_windows=len(success),
            errors=errors,
            pending=pending,
        ),
        scope=gold["annotation_scope"],
        human_review_required=gold["human_review_required"],
        source_hash=bundle["source_hash"],
        metrics=dict(
            targets=len(rows),
            image_targets=len(image_rows),
            correctly_linked_image_targets=sum(r["passed"] for r in image_rows),
            image_matched_predictions=len(image_predictions),
            image_correctly_linked_predictions=sum(p["linked"] for p in image_predictions),
            image_link_precision=sum(p["linked"] for p in image_predictions)
            / len(image_predictions)
            if image_predictions
            else None,
            semantically_covered=covered,
            correctly_linked_targets=linked_targets,
            targeted_fact_recall=covered / len(rows),
            correctly_linked_target_recall=linked_targets / len(rows),
            matched_predictions=matched,
            correctly_linked_predictions=linked,
            evidence_link_precision=linked / matched if matched else None,
            negative_rejections=sum(r["passed"] for r in negative_rows),
            model_negative_rejections=sum(r["model_rejected"] for r in negative_rows),
            grounding_negative_rejections=sum(r["grounding_rejected"] for r in negative_rows),
            negative_total=len(negative_rows),
        ),
        targets=rows,
        negatives=negative_rows,
    )


def write_html(report, bundle, source_root, output):
    import os

    def escape(value):
        return html.escape(str(value))

    def raw_details(value, label):
        return (
            "<details><summary>"
            + label
            + "</summary><pre>"
            + escape(json.dumps(value, ensure_ascii=False, indent=2))
            + "</pre></details>"
        )

    def statement(subject, predicate, obj=None, attribute=None, value=None):
        end = obj if obj is not None else ((attribute + " = ") if attribute else "") + (value or "")
        return (
            "<b>" + escape(subject) + "</b> <code>" + escape(predicate) + "</code> " + escape(end)
        )

    def state_line(value):
        patterns = value.get("led_pattern")
        if not patterns:
            return ""
        names = {"steady": "常亮", "blinking": "闪烁", "off": "熄灭"}
        return (
            "<p>模式："
            + escape(value.get("operating_state"))
            + "；LED1→4："
            + escape(" / ".join(names.get(p, p) for p in patterns))
            + "</p>"
        )

    rows = []
    for row in report["targets"]:
        target = row["target"]
        expected = target["evidence"]
        gold = statement(
            target["subject"][0],
            target["predicate"],
            target.get("object", [None])[0],
            target.get("attribute", [None])[0],
            " / ".join(target.get("value_contains", [])),
        )
        gold += state_line(target) + "<p>PDF第" + str(expected["page"]) + "页"
        if expected.get("visual_label"):
            gold += "；图中编号 " + escape(expected["visual_label"])
        gold += (
            "</p><blockquote>"
            + "<br>".join(escape(s) for s in expected.get("text_contains", []))
            + "</blockquote>"
        )
        gold += raw_details(target, "golden字段")
        predictions = []
        for prediction in row["predictions"]:
            fact = prediction["proposal"]
            view = statement(
                prediction["subject"],
                fact["predicate"],
                prediction["object"],
                fact.get("attribute"),
                fact.get("value"),
            ) + state_line(fact)
            if fact.get("qualifiers"):
                view += (
                    "<p>条件："
                    + escape(json.dumps(fact["qualifiers"], ensure_ascii=False))
                    + "</p>"
                )
            for evidence in prediction["evidence"]:
                view += (
                    "<p>PDF第"
                    + str(evidence["page"])
                    + "页</p><blockquote>"
                    + escape(evidence["quote"]).replace("\n", "<br>")
                    + "</blockquote>"
                )
                for region in evidence["regions"]:
                    path = Path(source_root) / bundle["assets"][region["asset_id"]]["storage_key"]
                    relative = os.path.relpath(path, Path(output).parent)
                    view += (
                        '<figure><a target="_blank" href="'
                        + escape(relative)
                        + '"><img src="'
                        + escape(relative)
                    )
                    view += '" alt="' + escape(region["id"]) + '" width="450"></a><figcaption>'
                    view += escape(region["id"]) + "（点击看原图）</figcaption></figure>"
            view += (
                "<p>"
                + escape(
                    "；".join(
                        k + (" ✓" if passed else " ✗") for k, passed in prediction["checks"].items()
                    )
                )
                + "</p>"
            )
            view += raw_details(prediction, "实际字段与证据坐标")
            predictions.append(view)
        rows.append(
            "<tr><td>"
            + escape(row["group_id"])
            + "<br>"
            + escape(target["label"])
            + "</td><td>"
            + gold
            + "</td><td>"
            + ("".join(predictions) or "漏抽 / 语义不匹配")
            + "</td><td>"
            + ("通过" if row["passed"] else "未通过")
            + "</td></tr>"
        )
    metrics = report["metrics"]
    execution = report["execution"]
    summary = (
        f"窗口 {execution['successful_windows']}/{execution['windows']}；正确关联目标 "
        f"{metrics['correctly_linked_targets']}/{metrics['targets']}；匹配预测关联正确 "
        f"{metrics['correctly_linked_predictions']}/{metrics['matched_predictions']}；"
        f"带图目标 {metrics['correctly_linked_image_targets']}/{metrics['image_targets']}；"
        "错误关联负例拒绝 "
        f"{metrics['negative_rejections']}/{metrics['negative_total']}"
    )
    negative_rows = "".join(
        "<tr><td>"
        + escape(n["id"])
        + "</td><td>"
        + escape(n["reason"])
        + "</td><td>"
        + escape("通过" if n["passed"] else "未通过")
        + raw_details(n, "实际审核与拒绝原因")
        + "</td></tr>"
        for n in report["negatives"]
    )
    Path(output).write_text(
        '<!doctype html><meta charset="utf-8"><title>手册图文证据对照</title>'
        "<style>body{font:15px/1.5 sans-serif;margin:24px;max-width:1600px}"
        "table{border-collapse:collapse;width:100%}"
        "td,th{vertical-align:top;border:1px solid #ccc;padding:12px;text-align:left}"
        "th{background:#f4f6f8}"
        "pre{white-space:pre-wrap;max-width:650px;font-size:12px}img{max-width:100%;height:auto}"
        "blockquote{border-left:3px solid #ccc;margin:8px 0;padding:8px;color:#333}"
        "figure{margin:10px 0}"
        "figcaption,summary{font-size:12px;color:#555}details{margin:8px 0}"
        "code{color:#254f83}</style>"
        "<h1>手册图文证据对照</h1><p>由助手对原始手册初标，尚待人工复核。仅评估19个定向目标，不代表全图精确率或F1。</p>"
        "<p><b>"
        + escape(summary)
        + "</b></p>"
        + raw_details(dict(execution=execution, metrics=metrics), "完整指标与运行信息")
        + '<table><tr><th style="width:12%">目标</th><th style="width:30%">金标准</th>'
        "<th>实际事实与原图</th>"
        '<th style="width:5%">结果</th></tr>'
        + "".join(rows)
        + "</table><h2>错误图片 / 灯态负例</h2>"
        "<table><tr><th>编号</th><th>预置错误</th><th>结果</th></tr>" + negative_rows + "</table>",
        encoding="utf-8",
    )


async def run_negatives(bundle, gold, root, cache, output, cache_only):
    results = {}
    settings = Settings()
    policy = policy_id(settings)
    if output.exists():
        previous = read_json(output)
        if previous["source_hash"] != bundle["source_hash"] or previous.get("policy") != policy:
            raise ValueError("负例报告来源或规则不一致，请使用新文件")
        results = previous["results"]
    for item in gold["negative_associations"]:
        case = next(c for c in bundle["cases"] if c["id"] == item["case_id"])
        if results.get(item["id"], {}).get("status") == "success":
            validate_result(bundle, case, results[item["id"]]["output"], policy, root=root)
            continue
        client = VisionClient(settings, cache, cache_only=cache_only)
        try:
            result = await review(
                bundle, case, Proposal.model_validate(item["proposal"]), client, root
            )
            row = dict(status="success", output=result)
        except Exception as exc:
            row = dict(status="error", error_type=type(exc).__name__)
        row.update(trace=client.trace, usage=client.usage.snapshot())
        results[item["id"]] = row
        save_json(
            output,
            dict(
                source_hash=bundle["source_hash"],
                policy=policy,
                model_profile={
                    "llm": settings.llm_model,
                    "vlm": settings.document_graph_vlm_model or settings.vlm_model,
                },
                results=results,
            ),
        )
    return results


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--source-bundle", type=Path, required=True)
    p.add_argument("--results", type=Path, required=True)
    p.add_argument(
        "--gold",
        type=Path,
        default=ROOT / "scripts/evaluation_data/document_multimodal_gold_v1.json",
    )
    p.add_argument("--output", type=Path, required=True)
    p.add_argument("--negative-results", type=Path)
    p.add_argument("--run-negatives", action="store_true")
    p.add_argument("--response-cache", type=Path)
    p.add_argument("--cache-only", action="store_true")
    args = p.parse_args()
    protected = {args.gold.resolve(), args.results.resolve()}
    if args.negative_results and not args.run_negatives:
        protected.add(args.negative_results.resolve())
    outputs = [args.output.resolve(), args.output.with_suffix(".html").resolve()]
    if args.run_negatives and args.negative_results:
        outputs.append(args.negative_results.resolve())
    if any(o in protected for o in outputs) or len(set(outputs)) != len(outputs):
        p.error("报告不能覆盖golden、输入结果或彼此")
    from app.services.document_graph.source import ensure_report_path

    for output in outputs:
        ensure_report_path(args.source_bundle, output)
    bundle, gold = load_bundle(args.source_bundle), read_json(args.gold)
    negatives = {}
    if args.run_negatives:
        if not args.negative_results or not args.response_cache:
            p.error("--run-negatives需要--negative-results和--response-cache")
        negatives = asyncio.run(
            run_negatives(
                bundle,
                gold,
                args.source_bundle,
                args.response_cache,
                args.negative_results,
                args.cache_only,
            )
        )
    elif args.negative_results:
        negatives = read_json(args.negative_results)["results"]
    raw = read_json(args.results)
    if raw["source_hash"] != bundle["source_hash"]:
        raise ValueError("评测结果来源不一致")
    report = evaluate(bundle, gold, raw["results"], negatives, root=args.source_bundle)
    report["gold_sha256"] = sha256(args.gold.read_bytes())
    report["results_sha256"] = sha256(args.results.read_bytes())
    save_json(args.output, report)
    write_html(report, bundle, args.source_bundle, args.output.with_suffix(".html"))
    print(json.dumps(report["metrics"], ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
