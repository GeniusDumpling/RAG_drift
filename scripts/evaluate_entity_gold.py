#!/usr/bin/env python3
"""Evaluate reviewed entity answers against the current extractor without graph writes."""

import argparse
import json
import re
import sys
import unicodedata
import uuid
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "backend"))

from app.core.config import Settings  # noqa: E402
from app.schemas.video_graph import ChunkExtraction  # noqa: E402
from app.services.video_graph.diagnostics import describe_graph_error  # noqa: E402
from app.services.video_graph.extraction import LLMVideoExtractor  # noqa: E402
from app.services.video_graph.identity import stable_hash  # noqa: E402
from app.services.video_graph.material import VideoChunk, VideoMaterial, locate_quote  # noqa: E402
from app.services.video_graph.validation import validate_extraction  # noqa: E402

from scripts.entity_gold_dataset import DEFAULT_DATASET, case_text, validate  # noqa: E402
from scripts.video_graph_response_cache import (  # noqa: E402
    CachedEvaluationExtractor,
    ModelResponseCache,
)

SCORING_RULE = "entity-record-one-to-one-v1"


def normalize(value):
    value = unicodedata.normalize("NFKC", value).casefold()
    value = value.translate(
        str.maketrans(
            {"‐": "-", "‑": "-", "–": "-", "—": "-", "‘": "'", "’": "'", "“": '"', "”": '"'}
        )
    )
    return "".join(value.split())


def material_for(case):
    source = case["source"]
    chunk = VideoChunk(
        uuid.UUID(source["chunk_id"]),
        case_text(case),
        "transcript_segment",
        source["start_seconds"],
        source["end_seconds"],
    )
    # Evaluation-local identity is stable per video URL. Nothing is published.
    return VideoMaterial(
        uuid.uuid5(uuid.NAMESPACE_URL, source["url"]),
        source["url"],
        source["title"],
        source["url"],
        "",
        None,
        case["language"],
        (chunk,),
    )


def prediction_entities(output):
    """Count graph identities once, retaining every source surface for matching."""
    grouped = {}
    for entity in output.entities:
        name = entity.canonical_name or entity.surface_text
        key = (normalize(name), entity.entity_type, entity.identity_scope)
        group = grouped.setdefault(
            key, {"name": name, "type": entity.entity_type, "mentions": [], "keys": []}
        )
        if entity.surface_text not in group["mentions"]:
            group["mentions"].append(entity.surface_text)
        group["keys"].append(entity.key)
    return list(grouped.values())


def owner_models(name, models):
    """Reject explicit conflicting model prefixes, never infer an absent owner."""
    value = unicodedata.normalize("NFKC", name).casefold().strip()
    found = []
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


def match_reason(gold, predicted, models):
    gold_owners = owner_models(gold["name"], models)
    predicted_owners = owner_models(predicted["name"], models)
    if gold_owners and predicted_owners and gold_owners.isdisjoint(predicted_owners):
        return None
    if normalize(gold["name"]) == normalize(predicted["name"]):
        return "canonical"
    if {normalize(x) for x in gold["mentions"]} & {normalize(x) for x in predicted["mentions"]}:
        return "mention"
    return None


def matching(gold, predicted, models, *, typed):
    adjacency = []
    reasons = {}
    for pi, pred in enumerate(predicted):
        candidates = []
        for gi, answer in enumerate(gold):
            if typed and pred["type"] != answer["type"]:
                continue
            reason = match_reason(answer, pred, models)
            if reason:
                candidates.append(gi)
                reasons[pi, gi] = reason
        candidates.sort(key=lambda gi: (reasons[pi, gi] != "canonical", gi))
        adjacency.append(candidates)
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

    for pi in sorted(range(len(predicted)), key=lambda i: (len(adjacency[i]), i)):
        augment(pi, set())
    pairs = [
        {"gold_index": gi, "prediction_index": pi, "via": reasons[pi, gi]}
        for gi, pi in sorted(assignments.items())
    ]
    used_pred = set(assignments.values())
    return {
        "tp": len(pairs),
        "fp": len(predicted) - len(pairs),
        "fn": len(gold) - len(pairs),
        "matches": pairs,
        "missed_gold": [gi for gi in range(len(gold)) if gi not in assignments],
        "extra_predictions": [pi for pi in range(len(predicted)) if pi not in used_pred],
        "ambiguous_predictions": [pi for pi, options in enumerate(adjacency) if len(options) > 1],
    }


def measures(counts):
    tp, fp, fn = (counts.get(key, 0) for key in ["tp", "fp", "fn"])
    return {
        "tp": tp,
        "fp": fp,
        "fn": fn,
        "precision": tp / (tp + fp) if tp + fp else None,
        "recall": tp / (tp + fn) if tp + fn else None,
        "f1": 2 * tp / (2 * tp + fp + fn) if 2 * tp + fp + fn else None,
    }


def score_case(case, result):
    predicted = []
    if result and result["status"] == "success":
        output = ChunkExtraction.model_validate(result["output"])
        material = material_for(case)
        validate_extraction(material.chunks[0], output, material=material, require_review=True)
        predicted = prediction_entities(output)
    return {
        "gold": case["entities"],
        "predicted": predicted,
        **{
            metric: matching(case["entities"], predicted, case["models"], typed=typed)
            for metric, typed in [("recognition", False), ("typed", True)]
        },
    }


def score_all(cases, results):
    totals, languages = defaultdict(Counter), defaultdict(lambda: defaultdict(Counter))
    errors, pending = [], []
    for case in cases:
        result = results.get(case["id"])
        if result is None:
            pending.append(case["id"])
        elif result["status"] != "success":
            errors.append(case["id"])
        detail = score_case(case, result)
        if result is not None:
            result["scoring"] = detail
        for metric in ["recognition", "typed"]:
            counts = {key: detail[metric][key] for key in ["tp", "fp", "fn"]}
            totals[metric].update(counts)
            languages[case["language"]][metric].update(counts)
    return {
        "cases": len(cases),
        "errors": errors,
        "pending": pending,
        "successful_cases": len(cases) - len(errors) - len(pending),
        "overall": {key: measures(value) for key, value in totals.items()},
        "by_language": {
            lang: {key: measures(value) for key, value in metrics.items()}
            for lang, metrics in languages.items()
        },
    }


def report_text(dataset, payload):
    metrics = payload["metrics"]
    lines = [
        "# 实体抽取基线评测",
        "",
        f"抽取版本：`{payload['extraction_version']}`。",
        f"金标准：`{payload['dataset_version']}`；指纹：`{payload['dataset_sha256']}`。",
        f"执行成功 {metrics['successful_cases']}/{metrics['cases']}，"
        f"错误 {len(metrics['errors'])}，待运行 {len(metrics['pending'])}。",
        "",
        "基于人工确认的实体答案，测试工作区抽取代码。输入仅标题和当前完整字幕，"
        "不传标准答案、models或notes，不写生产图谱。",
        "采用可复现的名称/原文叫法一对一匹配，容忍大小写、排版符号和空白差异；"
        "不进行自由翻译、模糊子串或模型自评。类型正确指标还要求实体类型相同。",
        "同一预测不能匹配多个答案；同一规范身份的重复提及只计一次。"
        "已明确冲突的型号所有者不能匹配。匹配不唯一的项另行标出，不代表所有者身份已核验。",
        "同义词、边界或命名粒度差异可能被判为漏抽/多抽，需结合下方清单复核；"
        "本报告不评属性、关系、完整提及边界或跨语言身份归一。",
        "",
        "| 范围 | 指标 | TP | FP | FN | Precision | Recall | F1 |",
        "|---|---|---:|---:|---:|---:|---:|---:|",
    ]
    for scope, groups in [("全部", metrics["overall"]), *metrics["by_language"].items()]:
        for name, values in groups.items():
            rates = [
                "—" if values[key] is None else f"{values[key]:.1%}"
                for key in ["precision", "recall", "f1"]
            ]
            title = "实体识别" if name == "recognition" else "实体及类型正确"
            lines.append(
                f"| {scope} | {title} | {values['tp']} | {values['fp']} | "
                f"{values['fn']} | {' | '.join(rates)} |"
            )
    for case in dataset["cases"]:
        result = payload["results"].get(case["id"])
        lines += ["", f"## {case['id']} · {case['language']}", ""]
        if not result:
            lines.append("尚未运行。")
            continue
        if result["status"] != "success":
            lines.append(f"执行失败：{result['error_detail']['code']}；标准实体仍计入漏抽分母。")
            continue
        scoring = result["scoring"]
        for title, key, field in [
            ("未匹配标准实体", "missed_gold", "gold"),
            ("未匹配预测实体", "extra_predictions", "predicted"),
        ]:
            lines += [f"{title}（按实体及类型匹配）：", ""]
            for index in scoring["typed"][key]:
                entity = scoring[field][index]
                mention = "；".join(entity["mentions"]).replace("\n", " ⏎ ")
                lines.append(f"- {entity['name']} [{entity['type']}]；原文叫法：{mention}")
            if not scoring["typed"][key]:
                lines.append("- 无。")
            lines.append("")
        for pair in scoring["recognition"]["matches"]:
            gold = scoring["gold"][pair["gold_index"]]
            predicted = scoring["predicted"][pair["prediction_index"]]
            if gold["type"] != predicted["type"]:
                lines.append(
                    f"类型差异：{gold['name']}，标准 {gold['type']}，" f"预测 {predicted['type']}。"
                )
        if scoring["typed"]["ambiguous_predictions"]:
            lines.append(
                "存在原文叫法对应多个标准实体的候选，请人工核对所有者，"
                "一对一分配不等于已证明身份归属。"
            )
    return "\n".join(lines) + "\n"


def run_case(case, settings, cache, attempt):
    material = material_for(case)
    extractor = (
        CachedEvaluationExtractor(
            settings, cache=cache, namespace=f"entity:{case['id']}:attempt{attempt}"
        )
        if cache
        else LLMVideoExtractor(settings)
    )
    started = datetime.now(UTC)
    try:
        output = extractor.extract(material, material.chunks[0], "")
        validate_extraction(material.chunks[0], output, material=material, require_review=True)
        result = {"status": "success", "output": output.model_dump()}
    except Exception as exc:
        result = {
            "status": "error",
            "error_type": type(exc).__name__,
            "error_detail": describe_graph_error(
                exc, stage="extraction", chunk=material.chunks[0], locate=locate_quote
            ),
        }
    result.update(attempt=attempt, seconds=(datetime.now(UTC) - started).total_seconds())
    if cache:
        result.update(cache_stats=extractor.cache_stats, cache_trace=extractor.cache_trace)
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=Path, default=DEFAULT_DATASET)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--report", type=Path)
    parser.add_argument("--response-cache", type=Path)
    parser.add_argument("--cache-only", action="store_true")
    parser.add_argument("--retry-errors", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--workers", type=int, default=2)
    args = parser.parse_args(argv)
    if not 1 <= args.workers <= 4 or (args.cache_only and not args.response_cache):
        parser.error("workers须为1–4；cache-only需要response-cache")
    if args.report and args.report.resolve() in {args.dataset.resolve(), args.output.resolve()}:
        parser.error("报告不能覆盖金标准或评测结果JSON")
    dataset = json.loads(args.dataset.read_text())
    validate(dataset)
    if args.dry_run:
        print(
            json.dumps(
                {
                    "cases": len(dataset["cases"]),
                    "minimum_model_calls": 2 * len(dataset["cases"]),
                    "maximum_stage_calls_without_repair": 18 * len(dataset["cases"]),
                    "maximum_http_attempts": 72 * len(dataset["cases"]),
                    "gold_entities": sum(len(c["entities"]) for c in dataset["cases"]),
                    "status": dataset["status"],
                },
                ensure_ascii=False,
            )
        )
        return 0
    if dataset["status"] != "human_reviewed":
        parser.error("请先完成人工复核并将status设为human_reviewed")
    settings = Settings()
    payload = {
        "dataset_version": dataset["version"],
        "dataset_sha256": stable_hash(dataset),
        "extraction_version": LLMVideoExtractor(settings).version,
        "scoring_rule": SCORING_RULE,
        "scorer_sha256": stable_hash(Path(__file__).read_text()),
        "input_scope": "title_and_current_chunk_only",
        "results": {},
    }
    if args.output.exists():
        saved = json.loads(args.output.read_text())
        if any(saved.get(key) != payload[key] for key in payload if key != "results"):
            parser.error("输出属于其他标注、抽取版本或评分规则，请使用新文件名")
        payload = saved
    cache = (
        ModelResponseCache(args.response_cache, cache_only=args.cache_only)
        if args.response_cache
        else None
    )
    results = payload["results"]
    cases = dataset["cases"]
    payload["metrics"] = score_all(cases, results)
    pending = [
        case
        for case in cases
        if case["id"] not in results
        or (args.retry_errors and results[case["id"]]["status"] == "error")
    ]
    args.output.parent.mkdir(parents=True, exist_ok=True)

    def save():
        payload["metrics"] = score_all(cases, results)
        payload["updated_at"] = datetime.now(UTC).isoformat()
        temporary = args.output.with_suffix(".tmp")
        temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
        temporary.replace(args.output)
        if args.report:
            args.report.parent.mkdir(parents=True, exist_ok=True)
            args.report.write_text(report_text(dataset, payload))

    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = {
            pool.submit(
                run_case, case, settings, cache, results.get(case["id"], {}).get("attempt", -1) + 1
            ): case["id"]
            for case in pending
        }
        for future in as_completed(futures):
            case_id, result = futures[future], future.result()
            previous = results.get(case_id)
            if previous:
                result["prior_attempts"] = previous.get("prior_attempts", []) + [
                    {
                        key: value
                        for key, value in previous.items()
                        if key not in {"output", "scoring", "prior_attempts"}
                    }
                ]
            results[case_id] = result
            save()
            print(json.dumps({"case_id": case_id, "status": result["status"]}), flush=True)
    save()
    print(json.dumps(payload["metrics"], ensure_ascii=False))
    return 1 if payload["metrics"]["errors"] or payload["metrics"]["pending"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
