#!/usr/bin/env python3
"""Step 1: Independent baseline KG evaluator (bypasses QualityReview)."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import tempfile
import unicodedata
from collections import Counter, defaultdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(ROOT / "backend") not in sys.path:
    sys.path.insert(0, str(ROOT / "backend"))

from kg_comparison.adapter import (  # noqa: E402
    adapt_case_prediction,
    load_predictions_payload,
    normalize_text,
)
from kg_comparison.metrics import (  # noqa: E402
    aggregate_usage_metrics,
    compute_structural_metrics,
)
from kg_comparison.schema import BaselineRunPayload  # noqa: E402
from scripts.entity_gold_dataset import (  # noqa: E402
    DEFAULT_DATASET as DEFAULT_ENTITY_DATASET,
)
from scripts.entity_gold_dataset import (  # noqa: E402
    cell,
)
from scripts.entity_gold_dataset import (  # noqa: E402
    validate as validate_entity_dataset,
)

DEFAULT_RELATION_DATASET = ROOT / "scripts/evaluation_data/relation_gold_v3.json"
SCORING_RULE = "kg-comparison-independent-v1"
SYMMETRIC_PREDICATES = frozenset({"compatible_with", "connected_to"})

RELATION_METRICS = {
    "triple": "三元组（含类型）",
    "assertion": "三元组+断言",
    "qualified": "三元组+断言+限定（严格）",
    "untyped_triple": "三元组（忽略实体类型）",
    "endpoint_pair": "实体对覆盖（忽略谓词与类型）",
}


def stable_hash(value: Any) -> str:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def quote_text(relation: dict[str, Any]) -> str:
    quote = relation.get("quote", "")
    if isinstance(quote, str):
        return quote
    if isinstance(quote, list) and all(isinstance(line, str) for line in quote):
        return "\n".join(quote)
    return ""


def owner_models(name: str, models: list[str]) -> set[str]:
    """Reject explicit conflicting model prefixes; identical to scripts/evaluate_entity_gold.py."""
    value = unicodedata.normalize("NFKC", name).casefold().strip()
    found: list[tuple[int, str]] = []
    for model in models:
        for variant in {model, re.sub(r"^DJI\s+", "", model, flags=re.I)}:
            words = unicodedata.normalize("NFKC", variant).casefold().split()
            prefix = r"\s*".join(re.escape(word) for word in words)
            match = re.match(prefix + r"(?=$|[\s\W]|[\u3400-\u9fff])", value)
            if match:
                found.append((match.end(), model))
    if not found:
        return set()
    longest = max(size for size, _ in found)
    return {model for size, model in found if size == longest}


def match_reason(
    gold: dict[str, Any], predicted: dict[str, Any], models: list[str]
) -> str | None:
    gold_owners = owner_models(gold["name"], models)
    predicted_owners = owner_models(predicted["name"], models)
    if gold_owners and predicted_owners and gold_owners.isdisjoint(predicted_owners):
        return None
    if normalize_text(gold["name"]) == normalize_text(predicted["name"]):
        return "canonical"
    gold_mentions = {normalize_text(x) for x in gold.get("mentions", [])}
    pred_mentions = {normalize_text(x) for x in predicted.get("mentions", [])}
    if gold_mentions & pred_mentions:
        return "mention"
    return None


def one_to_one(adjacency: list[list[int]], gold_count: int) -> dict[str, Any]:
    """Maximum bipartite matching; neither a prediction nor a gold item scores twice."""
    assignments: dict[int, int] = {}

    def augment(pi: int, seen: set[int]) -> bool:
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


def measures(counts: dict[str, int] | Counter[str]) -> dict[str, Any]:
    tp = counts.get("tp", 0)
    fp = counts.get("fp", 0)
    fn = counts.get("fn", 0)
    return {
        "tp": tp,
        "fp": fp,
        "fn": fn,
        "precision": tp / (tp + fp) if tp + fp else None,
        "recall": tp / (tp + fn) if tp + fn else None,
        "f1": 2 * tp / (2 * tp + fp + fn) if 2 * tp + fp + fn else None,
    }


def score_case_entities(case: dict[str, Any], adapted: dict[str, Any]) -> dict[str, Any]:
    gold = case["entities"]
    predicted = adapted["entities"]
    models = case["models"]

    def _match(*, typed: bool) -> dict[str, Any]:
        adjacency: list[list[int]] = []
        reasons: dict[tuple[int, int], str] = {}
        for pi, pred in enumerate(predicted):
            candidates: list[int] = []
            for gi, answer in enumerate(gold):
                if typed and pred["type"] != answer["type"]:
                    continue
                reason = match_reason(answer, pred, models)
                if reason:
                    candidates.append(gi)
                    reasons[(pi, gi)] = reason
            candidates.sort(key=lambda gi: (reasons[(pi, gi)] != "canonical", gi))
            adjacency.append(candidates)
        matched = one_to_one(adjacency, len(gold))
        matched["matches"] = [
            {
                "gold_index": pair["gold_index"],
                "prediction_index": pair["prediction_index"],
                "via": reasons[(pair["prediction_index"], pair["gold_index"])],
            }
            for pair in matched["matches"]
        ]
        matched["ambiguous_predictions"] = [
            pi for pi, options in enumerate(adjacency) if len(options) > 1
        ]
        return matched

    return {
        "gold": gold,
        "predicted": predicted,
        "ungrounded_entities": adapted["ungrounded_entities"],
        "recognition": _match(typed=False),
        "typed": _match(typed=True),
    }


def qualifiers_key(qualifiers: dict[str, str]) -> tuple[tuple[str, str], ...]:
    return tuple(
        sorted(
            (key, normalize_text(value))
            for key, value in qualifiers.items()
            if not (key == "modality" and value == "actual")
        )
    )


def endpoints(subject: Any, obj: Any, predicate: str) -> tuple[Any, Any]:
    return tuple(sorted((subject, obj))) if predicate in SYMMETRIC_PREDICATES else (subject, obj)


def _align_entities(
    case: dict[str, Any], predicted: list[dict[str, Any]], *, require_type: bool
) -> tuple[dict[str, Any], dict[int, int], list[bool]]:
    adjacency: list[list[int]] = []
    scope: list[bool] = []
    ambiguous: list[dict[str, Any]] = []

    for pi, entity in enumerate(predicted):
        candidates: list[int] = []
        canonical: list[int] = []
        recognized: list[int] = []
        for gi, gold in enumerate(case["entities"]):
            reason = match_reason(gold, entity, case["models"])
            if reason:
                recognized.append(gi)
                if (not require_type) or (entity["type"] == gold["type"]):
                    candidates.append(gi)
                    if reason == "canonical":
                        canonical.append(gi)
        options = canonical or candidates
        scope.append(bool(recognized))
        if len(options) > 1:
            ambiguous.append({"prediction_index": pi, "gold_candidates": options})
        adjacency.append(options if len(options) == 1 else [])

    matched = one_to_one(adjacency, len(case["entities"]))
    mapping = {pair["prediction_index"]: pair["gold_index"] for pair in matched["matches"]}
    info = {
        "gold": case["entities"],
        "predicted": predicted,
        "matches": matched["matches"],
        "ambiguous_predictions": ambiguous,
    }
    return info, mapping, scope


def score_case_relations(case: dict[str, Any], adapted: dict[str, Any]) -> dict[str, Any]:
    predicted_entities = adapted["entities"]
    typed_info, typed_map, scope = _align_entities(case, predicted_entities, require_type=True)
    _, untyped_map, _ = _align_entities(case, predicted_entities, require_type=False)

    gold = case.get("relations", [])
    predicted: list[dict[str, Any]] = []
    dedup: dict[tuple[Any, ...], int] = {}

    for index, rel in enumerate(adapted["relations"]):
        si = rel["subject_index"]
        oi = rel["object_index"]
        identity = (
            endpoints(si, oi, rel["predicate"]),
            rel["predicate"],
            rel["assertion"],
            qualifiers_key(rel["qualifiers"]),
        )
        if identity in dedup:
            predicted[dedup[identity]]["source_indices"].append(index)
            continue
        dedup[identity] = len(predicted)
        predicted.append(
            {
                **rel,
                "source_indices": [index],
                "in_scope": scope[si] and scope[oi],
                "gold_subject": (
                    case["entities"][typed_map[si]]["name"] if si in typed_map else None
                ),
                "gold_object": (
                    case["entities"][typed_map[oi]]["name"] if oi in typed_map else None
                ),
                "untyped_gold_subject": (
                    case["entities"][untyped_map[si]]["name"] if si in untyped_map else None
                ),
                "untyped_gold_object": (
                    case["entities"][untyped_map[oi]]["name"] if oi in untyped_map else None
                ),
            }
        )

    in_scope = [i for i, rel in enumerate(predicted) if rel["in_scope"]]
    detail: dict[str, Any] = {
        "gold": gold,
        "predicted": predicted,
        "entity_alignment": typed_info,
        "out_of_scope": [i for i, rel in enumerate(predicted) if not rel["in_scope"]],
    }

    for metric in RELATION_METRICS:
        adjacency: list[list[int]] = []
        for pi in in_scope:
            pred = predicted[pi]
            matches: list[int] = []
            for gi, answer in enumerate(gold):
                if metric in {"triple", "assertion", "qualified"}:
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
                    if metric == "qualified" and qualifiers_key(
                        pred["qualifiers"]
                    ) != qualifiers_key(answer.get("qualifiers", {})):
                        continue
                    matches.append(gi)
                elif metric == "untyped_triple":
                    if (
                        pred["untyped_gold_subject"] is None
                        or pred["untyped_gold_object"] is None
                    ):
                        continue
                    if pred["predicate"] != answer["predicate"]:
                        continue
                    if endpoints(
                        pred["untyped_gold_subject"],
                        pred["untyped_gold_object"],
                        pred["predicate"],
                    ) != endpoints(answer["subject"], answer["object"], answer["predicate"]):
                        continue
                    matches.append(gi)
                elif metric == "endpoint_pair":
                    if (
                        pred["untyped_gold_subject"] is None
                        or pred["untyped_gold_object"] is None
                    ):
                        continue
                    pred_pair = tuple(
                        sorted((pred["untyped_gold_subject"], pred["untyped_gold_object"]))
                    )
                    gold_pair = tuple(sorted((answer["subject"], answer["object"])))
                    if pred_pair == gold_pair:
                        matches.append(gi)
            adjacency.append(matches)
        result = one_to_one(adjacency, len(gold))
        for pair in result["matches"]:
            pair["prediction_index"] = in_scope[pair["prediction_index"]]
        result["extra_predictions"] = [in_scope[i] for i in result["extra_predictions"]]
        detail[metric] = result

    return detail


def evaluate_payload(
    payload: BaselineRunPayload,
    cases: list[dict[str, Any]],
    *,
    dataset_version: str,
    dataset_sha256: str,
    has_relations: bool = True,
) -> dict[str, Any]:
    """Run full entity + relation + structural + usage evaluation on a BaselineRunPayload."""
    adapted_by_case: dict[str, dict[str, Any]] = {}
    entity_totals: dict[str, Counter[str]] = defaultdict(Counter)
    entity_by_lang: dict[str, dict[str, Counter[str]]] = defaultdict(lambda: defaultdict(Counter))

    rel_totals: dict[str, Counter[str]] = defaultdict(Counter)
    rel_by_lang: dict[str, dict[str, Counter[str]]] = defaultdict(lambda: defaultdict(Counter))
    rel_by_pred: dict[str, dict[str, Counter[str]]] = defaultdict(lambda: defaultdict(Counter))

    errors: list[str] = []
    pending: list[str] = []
    predicted_relations_total = 0
    out_of_scope_total = 0
    case_details: dict[str, dict[str, Any]] = {}

    for case in cases:
        cid = case["id"]
        pred = payload.results.get(cid)
        if pred is None:
            pending.append(cid)
        elif pred.status != "success":
            errors.append(cid)

        adapted = adapt_case_prediction(case, pred)
        adapted_by_case[cid] = adapted

        ent_detail = score_case_entities(case, adapted)
        for metric in ("recognition", "typed"):
            counts = {k: ent_detail[metric][k] for k in ("tp", "fp", "fn")}
            entity_totals[metric].update(counts)
            entity_by_lang[case["language"]][metric].update(counts)

        rel_detail = None
        if has_relations:
            rel_detail = score_case_relations(case, adapted)
            predicted_relations_total += len(rel_detail["predicted"])
            out_of_scope_total += len(rel_detail["out_of_scope"])
            for metric in RELATION_METRICS:
                scored = rel_detail[metric]
                counts = {k: scored[k] for k in ("tp", "fp", "fn")}
                rel_totals[metric].update(counts)
                rel_by_lang[case["language"]][metric].update(counts)
                if metric in {"triple", "assertion", "qualified"}:
                    for pair in scored["matches"]:
                        p_name = rel_detail["gold"][pair["gold_index"]]["predicate"]
                        rel_by_pred[p_name][metric]["tp"] += 1
                    for gi in scored["missed_gold"]:
                        p_name = rel_detail["gold"][gi]["predicate"]
                        rel_by_pred[p_name][metric]["fn"] += 1
                    for pi in scored["extra_predictions"]:
                        p_name = rel_detail["predicted"][pi]["predicate"]
                        rel_by_pred[p_name][metric]["fp"] += 1

        case_details[cid] = {
            "status": adapted["status"],
            "diagnostics": adapted["diagnostics"],
            "ungrounded_entities": adapted["ungrounded_entities"],
            "entity_scoring": ent_detail,
            "relation_scoring": rel_detail,
            "seconds": pred.seconds if pred else 0.0,
            "usage": pred.usage.model_dump() if pred else {},
            "error_type": pred.error_type if pred else None,
            "error_message": pred.error_message if pred else None,
        }

    structural = compute_structural_metrics(adapted_by_case, corpus_stats=payload.corpus_stats)
    usage = aggregate_usage_metrics(
        payload,
        case_ids=[c["id"] for c in cases],
        valid_triples=structural["valid_triples"],
    )

    metrics: dict[str, Any] = {
        "cases": len(cases),
        "successful_cases": len(cases) - len(errors) - len(pending),
        "errors": errors,
        "pending": pending,
        "entities": {
            "overall": {m: measures(entity_totals[m]) for m in ("recognition", "typed")},
            "by_language": {
                lang: {m: measures(counts[m]) for m in ("recognition", "typed")}
                for lang, counts in sorted(entity_by_lang.items())
            },
        },
        "structural": structural,
        "usage": usage,
    }
    if has_relations:
        metrics["relations"] = {
            "predicted_relations": predicted_relations_total,
            "out_of_scope_relations": out_of_scope_total,
            "in_scope_relations": predicted_relations_total - out_of_scope_total,
            "overall": {m: measures(rel_totals[m]) for m in RELATION_METRICS},
            "by_language": {
                lang: {m: measures(counts[m]) for m in RELATION_METRICS}
                for lang, counts in sorted(rel_by_lang.items())
            },
            "by_predicate": {
                p: {m: measures(counts[m]) for m in ("triple", "assertion", "qualified")}
                for p, counts in sorted(rel_by_pred.items())
            },
        }

    return {
        "scoring_rule": SCORING_RULE,
        "evaluated_at": datetime.now(UTC).isoformat(),
        "method": payload.method,
        "method_version": payload.method_version,
        "mode": payload.mode,
        "granularity": payload.granularity,
        "model": payload.model,
        "temperature": payload.temperature,
        "input_scope": payload.input_scope,
        "dataset_version": dataset_version,
        "dataset_sha256": dataset_sha256,
        "metrics": metrics,
        "cases": case_details,
    }


def _fmt_pct(value: float | None) -> str:
    return "—" if value is None else f"{value:.1%}"


def _fmt_num(value: float | int | None, digits: int = 2) -> str:
    if value is None:
        return "—"
    if isinstance(value, int):
        return f"{value:,}"
    return f"{value:.{digits}f}"


def render_evaluation_report(dataset_cases: list[dict[str, Any]], report: dict[str, Any]) -> str:
    """Render a detailed Markdown report for a single evaluated baseline run."""
    m = report["metrics"]
    st = m["structural"]
    u = m["usage"]

    lines = [
        f"# 基线知识图谱评测报告：{report['method']} ({report['mode']})",
        "",
        f"- **方法版本**：`{report['method_version']}`",
        f"- **运行模式 / 粒度**：`{report['mode']}` / `{report['granularity']}`",
        (
            f"- **模型与温度**：`{report.get('model') or 'unspecified'}`"
            f"（`temperature={report.get('temperature')}`）"
        ),
        f"- **数据集**：`{report['dataset_version']}`（SHA256: `{report['dataset_sha256'][:12]}`）",
        (
            f"- **样本统计**：成功 {m['successful_cases']}/{m['cases']}，"
            f"失败 {len(m['errors'])}，待运行 {len(m['pending'])}"
        ),
        "",
        "## 一、实体评测（绕过 QualityReview 强校验，按一对一匹配计分）",
        "",
        "| 范围 | 指标 | TP | FP | FN | Precision | Recall | F1 |",
        "|---|---|---:|---:|---:|---:|---:|---:|",
    ]
    for scope, groups in [
        ("全部", m["entities"]["overall"]),
        *m["entities"]["by_language"].items(),
    ]:
        for key, label in [("recognition", "实体识别（无类型）"), ("typed", "实体及类型正确")]:
            v = groups[key]
            lines.append(
                f"| {scope} | {label} | {v['tp']} | {v['fp']} | {v['fn']} | "
                f"{_fmt_pct(v['precision'])} | {_fmt_pct(v['recall'])} | {_fmt_pct(v['f1'])} |"
            )

    if "relations" in m:
        rel_m = m["relations"]
        lines += [
            "",
            "## 二、关系评测（金标准实体范围内）",
            "",
            (
                f"预测关系去重后 **{rel_m['predicted_relations']}** 条"
                f"（范围内 **{rel_m['in_scope_relations']}**，"
                f"范围外 **{rel_m['out_of_scope_relations']}**）。"
            ),
            "",
            "| 范围 | 指标 | TP | FP | FN | Precision | Recall | F1 |",
            "|---|---|---:|---:|---:|---:|---:|---:|",
        ]
        for scope, groups in [("全部", rel_m["overall"]), *rel_m["by_language"].items()]:
            for key, label in RELATION_METRICS.items():
                v = groups[key]
                lines.append(
                    f"| {scope} | {label} | {v['tp']} | {v['fp']} | {v['fn']} | "
                    f"{_fmt_pct(v['precision'])} | {_fmt_pct(v['recall'])} | {_fmt_pct(v['f1'])} |"
                )

    lines += [
        "",
        "## 三、本体无关结构质量、溯源与成本指标",
        "",
        "| 指标 | 数值 |",
        "|---|---:|",
        f"| 实体总数 / 去重实体数 | {st['total_entities']} / {st['unique_entities']} |",
        (
            f"| 实体原文精确溯源率 | {_fmt_pct(st['entity_exact_grounding_rate'])} "
            f"({st['exact_grounded_entities']}/{st['total_entities']}) |"
        ),
        f"| 实体原文忽略大小写溯源率 | {_fmt_pct(st['entity_casefold_grounding_rate'])} |",
        f"| 未溯源实体数 (`ungrounded_entities`) | {st['ungrounded_entities_count']} |",
        f"| 关系总数 / 唯一谓词数 | {st['total_relations']} / {st['unique_predicates']} |",
        (
            f"| 合法三元组率 (Valid Triple Rate) | {_fmt_pct(st['valid_triple_rate'])} "
            f"({st['valid_triples']}/{st['total_relations']}) |"
        ),
        f"| 关系复用度 (Relation Reuse Ratio) | {_fmt_num(st['relation_reuse_ratio'])} |",
        f"| 平均节点度数 (Avg Degree) | {_fmt_num(st['avg_degree'])} |",
        f"| 受控本体谓词覆盖率 | {_fmt_pct(st['schema_predicate_rate'])} |",
        f"| 受控类型规则通过率 | {_fmt_pct(st['schema_type_valid_rate'])} |",
        f"| 关系引文溯源率 | {_fmt_pct(st['relation_quote_grounding_rate'])} |",
        f"| 默认填充 assertion=asserted 次数 | {st['defaulted_assertions_count']} |",
        (
            f"| 总 Token 消耗 (Prompt / Completion) | {_fmt_num(u['total_tokens'], 0)} "
            f"({_fmt_num(u['prompt_tokens'], 0)} / {_fmt_num(u['completion_tokens'], 0)}) |"
        ),
        f"| HTTP 调用 / 缓存重放 | {u['http_calls']} / {u['replayed_calls']} |",
        (
            f"| 总耗时 / 平均每切片耗时 | {_fmt_num(u['total_seconds'])}s / "
            f"{_fmt_num(u['avg_seconds_per_case'])}s |"
        ),
        f"| 每条合法三元组 Token 成本 | {_fmt_num(u['tokens_per_valid_triple'], 1)} |",
    ]

    if st.get("corpus_stats"):
        cs = st["corpus_stats"]
        lines += [
            "",
            "### 语料级聚类与压缩统计（Stage 2/3）",
            "",
            f"```json\n{json.dumps(cs, ensure_ascii=False, indent=2)}\n```",
        ]

    lines += ["", "## 四、逐切片诊断摘要（含 ungrounded_entities）"]
    for case in dataset_cases:
        cid = case["id"]
        cd = report["cases"][cid]
        lines += ["", f"### {cid} · {case['language']}"]
        if cd["status"] != "success":
            lines.append(f"- 状态：`{cd['status']}`；错误：`{cd.get('error_message') or '无'}`")
            continue
        ent_s = cd["entity_scoring"]
        lines.append(
            f"- 实体识别 TP/FP/FN："
            f"`{ent_s['recognition']['tp']}/{ent_s['recognition']['fp']}/"
            f"{ent_s['recognition']['fn']}`；"
            f"实体+类型 TP/FP/FN："
            f"`{ent_s['typed']['tp']}/{ent_s['typed']['fp']}/{ent_s['typed']['fn']}`"
        )
        if cd.get("relation_scoring"):
            rs = cd["relation_scoring"]
            lines.append(
                f"- 关系 Triple TP/FP/FN："
                f"`{rs['triple']['tp']}/{rs['triple']['fp']}/{rs['triple']['fn']}`；"
                f"Assertion：`{rs['assertion']['tp']}/{rs['assertion']['fp']}/"
                f"{rs['assertion']['fn']}`；"
                f"Untyped Triple：`{rs['untyped_triple']['tp']}/"
                f"{rs['untyped_triple']['fp']}/{rs['untyped_triple']['fn']}`；"
                f"Endpoint-Pair：`{rs['endpoint_pair']['tp']}/"
                f"{rs['endpoint_pair']['fp']}/{rs['endpoint_pair']['fn']}`"
            )
        ungrounded = cd["ungrounded_entities"]
        if ungrounded:
            names = "、".join(cell(e["name"]) for e in ungrounded)
            lines.append(f"- `ungrounded_entities` ({len(ungrounded)})：{names}")
        else:
            lines.append("- `ungrounded_entities`：无")

    return "\n".join(lines) + "\n"


def render_comparison_table(reports: list[dict[str, Any]]) -> str:
    """Render a side-by-side Markdown comparison table across multiple evaluated baselines."""
    header = (
        "| 方法 | 模式/粒度 | 实体识别 F1 | 实体+类型 F1 | Triple F1 | Assertion F1 | "
        "Qualified F1 | Untyped Triple F1 | Endpoint-Pair F1 | 实体精确溯源率 | 未溯源实体 | "
        "合法三元组率 | 关系复用度 | 总 Tokens | 耗时(s) |"
    )
    lines = [
        "# 知识图谱抽图方法横向对比汇总表",
        "",
        header,
        "|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for r in reports:
        m = r["metrics"]
        e_rec = m["entities"]["overall"]["recognition"]["f1"]
        e_typ = m["entities"]["overall"]["typed"]["f1"]
        rel = m.get("relations", {}).get("overall", {})
        st = m["structural"]
        u = m["usage"]
        lines.append(
            f"| {r['method']} | {r['mode']}/{r['granularity']} | "
            f"{_fmt_pct(e_rec)} | {_fmt_pct(e_typ)} | "
            f"{_fmt_pct(rel.get('triple', {}).get('f1'))} | "
            f"{_fmt_pct(rel.get('assertion', {}).get('f1'))} | "
            f"{_fmt_pct(rel.get('qualified', {}).get('f1'))} | "
            f"{_fmt_pct(rel.get('untyped_triple', {}).get('f1'))} | "
            f"{_fmt_pct(rel.get('endpoint_pair', {}).get('f1'))} | "
            f"{_fmt_pct(st['entity_exact_grounding_rate'])} | "
            f"{st['ungrounded_entities_count']} | "
            f"{_fmt_pct(st['valid_triple_rate'])} | "
            f"{_fmt_num(st['relation_reuse_ratio'])} | "
            f"{_fmt_num(u['total_tokens'], 0)} | "
            f"{_fmt_num(u['total_seconds'])} |"
        )
    return "\n".join(lines) + "\n"


def atomic_write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=path.parent, prefix=".kg-cmp-", delete=False
        ) as handle:
            tmp_path = Path(handle.name)
            json.dump(payload, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
        tmp_path.replace(path)
    finally:
        if tmp_path:
            tmp_path.unlink(missing_ok=True)


def load_gold_cases(
    *,
    relation_dataset: Path | None,
    entity_dataset: Path,
    case_ids: list[str] | None = None,
) -> tuple[list[dict[str, Any]], str, str, bool]:
    if relation_dataset is not None and relation_dataset.exists():
        data = json.loads(relation_dataset.read_text(encoding="utf-8"))
        has_relations = True
    else:
        data = json.loads(entity_dataset.read_text(encoding="utf-8"))
        validate_entity_dataset(data)
        has_relations = False

    cases = data["cases"]
    if case_ids:
        selected = set(case_ids)
        unknown = selected - {c["id"] for c in cases}
        if unknown:
            raise ValueError("未知 case-id: " + ", ".join(sorted(unknown)))
        cases = [c for c in cases if c["id"] in selected]
    return cases, data["version"], stable_hash(data), has_relations


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--predictions",
        type=Path,
        help="单次评测输入：kg-comparison-v1 预测 JSON 或 RAG_drift 原生评测结果 JSON",
    )
    parser.add_argument(
        "--compare",
        type=Path,
        nargs="+",
        help="多基线汇总模式：传入多个由本脚本生成的评测 JSON，输出横向对比表",
    )
    parser.add_argument(
        "--relation-dataset",
        type=Path,
        default=DEFAULT_RELATION_DATASET,
        help="关系金标准集路径（包含实体+关系答案）",
    )
    parser.add_argument(
        "--entity-dataset",
        type=Path,
        default=ROOT / DEFAULT_ENTITY_DATASET,
        help="仅实体评测时的实体金标准集路径",
    )
    parser.add_argument(
        "--entities-only",
        action="store_true",
        help="仅评估实体与结构指标，不加载关系金标准",
    )
    parser.add_argument("--case-id", action="append", help="仅评估指定 case ID（可重复指定）")
    parser.add_argument("--output", type=Path, help="评测结果 JSON 输出路径")
    parser.add_argument("--report", type=Path, help="Markdown 报告输出路径")
    args = parser.parse_args(argv)

    if bool(args.predictions) == bool(args.compare):
        parser.error("必须二选一指定 --predictions 或 --compare")

    if args.compare:
        reports = [json.loads(p.read_text(encoding="utf-8")) for p in args.compare]
        md = render_comparison_table(reports)
        if args.report:
            args.report.parent.mkdir(parents=True, exist_ok=True)
            args.report.write_text(md, encoding="utf-8")
        else:
            print(md, end="")
        if args.output:
            atomic_write_json(
                args.output,
                {
                    "compared_at": datetime.now(UTC).isoformat(),
                    "methods": [
                        {
                            "method": r["method"],
                            "mode": r["mode"],
                            "granularity": r["granularity"],
                            "metrics": r["metrics"],
                        }
                        for r in reports
                    ],
                },
            )
        return 0

    payload = load_predictions_payload(args.predictions)
    cases, ds_version, ds_sha, has_relations = load_gold_cases(
        relation_dataset=None if args.entities_only else args.relation_dataset,
        entity_dataset=args.entity_dataset,
        case_ids=args.case_id,
    )
    eval_report = evaluate_payload(
        payload,
        cases,
        dataset_version=ds_version,
        dataset_sha256=ds_sha,
        has_relations=has_relations,
    )
    if args.output:
        atomic_write_json(args.output, eval_report)
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(render_evaluation_report(cases, eval_report), encoding="utf-8")
    print(json.dumps(eval_report["metrics"], ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
