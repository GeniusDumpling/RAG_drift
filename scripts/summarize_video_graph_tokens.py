"""Summarize live provider usage from golden runs (replays are not token measurements)."""

import argparse
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
sys.path.insert(0, str(ROOT))

from app.schemas.video_graph import ChunkExtraction  # noqa: E402

from scripts.evaluate_entity_gold import matching, measures, prediction_entities  # noqa: E402


def summarize(payload, entities, usage_index=None):
    totals, phases, entity_counts = Counter(), defaultdict(Counter), Counter()
    logical, missing = Counter(), []
    traces = Counter()
    for case in entities["cases"]:
        result = payload["results"].get(case["id"], {})
        for trace in result.get("cache_trace", []):
            traces["replayed_stages" if trace["replayed"] else "live_stages"] += 1
            usage = trace.get("usage")
            if usage:
                totals.update(usage)
                phases[trace["phase"]].update(usage)
            elif not trace["replayed"]:
                traces["stages_without_usage"] += 1
            measured = usage or (usage_index or {}).get(trace.get("request_hash"))
            if measured and measured.get("measured_responses"):
                logical.update({k: measured[k] for k in (
                    "prompt_tokens", "completion_tokens", "total_tokens")})
            else:
                missing.append(trace.get("request_hash"))
        # Run outputs have already passed the evaluator's source/receipt validation.
        # Reuse its strict one-to-one entity matcher; do not reinterpret labels.
        predicted = (prediction_entities(ChunkExtraction.model_validate(result["output"]))
                     if result.get("status") == "success" else [])
        counts = matching(case["entities"], predicted, case["models"], typed=True)
        entity_counts.update({k: counts[k] for k in ("tp", "fp", "fn")})
    return {
        "extraction_version": payload["extraction_version"],
        "cases_completed": len(payload["results"]),
        "relation_metrics": payload["metrics"]["overall"],
        "entity_metrics": measures(entity_counts),
        "stages": dict(traces), "usage": dict(totals),
        "phases": {key: dict(value) for key, value in phases.items()},
        "request_usage_including_historical_exact_replays": dict(logical),
        "requests_without_measurement": missing,
        "cache_hit_fraction": (totals["cached_tokens"] / totals["prompt_tokens"]
                               if totals["cache_measured_responses"] and totals["prompt_tokens"]
                               else None),
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("runs", nargs="+", type=Path)
    parser.add_argument("--entities", type=Path,
                        default=ROOT / "scripts/evaluation_data/entity_gold_v3.json")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--reference-runs", nargs="*", type=Path, default=[],
                        help="真实调用记录，用于匹配重放请求的历史用量；不代表本次账单")
    args = parser.parse_args()
    entities = json.loads(args.entities.read_text())
    usage_index = {}
    for path in [*args.reference_runs, *args.runs]:
        for result in json.loads(path.read_text())["results"].values():
            for trace in result.get("cache_trace", []):
                if trace.get("usage") and trace.get("request_hash"):
                    usage_index.setdefault(trace["request_hash"], trace["usage"])
    output = {str(path): summarize(json.loads(path.read_text()), entities, usage_index)
              for path in args.runs}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(output, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps(output, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
