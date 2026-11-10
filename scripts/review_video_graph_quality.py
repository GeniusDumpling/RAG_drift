#!/usr/bin/env python3
"""Sample new edges reproducibly and check source-review release gates without model calls."""

import argparse
import json
import random
import sys
from collections import Counter
from copy import deepcopy
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "backend"))

from app.core.config import Settings  # noqa: E402
from app.schemas.video_graph import ChunkExtraction  # noqa: E402
from app.services.video_graph.extraction import PROMPT_VERSION  # noqa: E402
from app.services.video_graph.identity import (  # noqa: E402
    entity_identity,
    extraction_version,
    stable_hash,
)

from scripts.evaluate_video_graph_quality import (  # noqa: E402
    DEFAULT_DATASET,
    EVALUATION_PROTOCOL,
    material_for,
    refresh_scores,
    score,
    source_groups,
)

DEFAULT_CRITERIA = ROOT / "scripts/evaluation_data/video_graph_release_criteria.json"


def make_sample(evaluation, dataset, criteria, *, expected_version):
    if evaluation.get("evaluation_protocol") != EVALUATION_PROTOCOL:
        raise ValueError("必须使用同一来源只抽取一次的验收协议")
    if evaluation.get("target_version") != expected_version:
        raise ValueError("验收输出不属于当前抽取版本")
    if evaluation.get("dataset_hash") != stable_hash(dataset):
        raise ValueError("验收来源集或标签与当前固定集不一致")
    cases = {case["case_id"]: case for case in dataset["cases"]}
    results = deepcopy(evaluation["results"])
    if set(results) != set(cases) or any(r["status"] != "success" for r in results.values()):
        raise ValueError("必须先完成固定集全部样本且无执行错误")
    for group in source_groups(dataset["cases"]):
        outputs = {stable_hash(results[case["case_id"]]["output"]) for case in group}
        if len(outputs) != 1:
            raise ValueError("同一来源的验收目标必须共享同一次抽取输出")
    # Revalidate every raw result/receipt and recompute target matches.
    refresh_scores(dataset["cases"], results)
    population, counts = {}, Counter()
    counts["distinct_sources"] = len(source_groups(dataset["cases"]))
    for case_id in sorted(cases):
        case = cases[case_id]
        material = material_for(case)
        extraction = ChunkExtraction.model_validate(results[case_id]["output"])
        entities = {entity.key: entity for entity in extraction.entities}
        ids = {key: entity_identity(entity, material).id for key, entity in entities.items()}
        counts["validated_target_outputs"] += 1
        counts["entity_occurrences"] += len(entities)
        counts["relation_occurrences"] += len(extraction.relations)
        for entity in entities.values():
            counts[entity.identity_scope + "_entity_occurrences"] += 1
        for index, relation in enumerate(extraction.relations):
            if ids[relation.subject] == ids[relation.object]:
                continue
            key = stable_hash([str(ids[relation.subject]), relation.predicate,
                               str(ids[relation.object]), relation.qualifiers])
            population.setdefault(key, {
                "relation_key": key, "case_id": case_id, "relation_index": index,
                "video_id": case["video_id"], "chunk_id": case["chunk_id"],
                "title": case["title"], "url": case["url"],
                "start_seconds": case["start_seconds"], "end_seconds": case["end_seconds"],
                "source_text": case["text"],
                "subject": entities[relation.subject].model_dump(),
                "object": entities[relation.object].model_dump(),
                "relation": relation.model_dump(),
                "support": extraction.review.relation_support[index],
            })
    rule = criteria["output_review"]
    if len(population) < rule["sample_size"]:
        raise ValueError("去重后的新增关系不足固定抽查数量，不能宣称通过")
    selected = random.Random(rule["seed"]).sample(sorted(population), rule["sample_size"])
    rows = sorted([population[key] for key in selected],
                  key=lambda row: (row["case_id"], row["relation_index"]))
    for index, row in enumerate(rows, 1):
        row.update(sample_id=f"Q{index:03}", judgment="pending", note="")
    return {
        "format_version": 1, "target_version": expected_version,
        "evaluation_hash": stable_hash(evaluation), "dataset_hash": stable_hash(dataset),
        "criteria_hash": stable_hash(criteria), "criteria": criteria,
        "target_metrics": score(dataset["cases"], results),
        "population_relations": len(population), "deterministic_checks": dict(counts),
        "sample_videos": len({row["video_id"] for row in rows}),
        "samples": rows,
    }


def release_score(target, judgments, criteria):
    counts = Counter(judgments)
    if set(counts) - {"supported", "issue", "review", "pending"}:
        raise ValueError("未知的原文复核标签")
    rule = criteria["output_review"]
    complete = len(judgments) == rule["sample_size"] and not counts["pending"]
    target_gate = (not target["errors"] and not target["pending"]
                   and (target["supported_retention"] or 0) >= criteria["target_retention_min"]
                   and (target["bad_target_rejection"] or 0) >= criteria["known_bad_rejection_min"])
    output_gate = (complete and counts["supported"] >= rule["minimum_supported"]
                   and counts["issue"] <= rule["maximum_issue"]
                   and counts["review"] <= rule["maximum_review"])
    return {"target_gate": target_gate, "source_review_complete": complete,
            "source_review_counts": dict(counts), "output_gate": output_gate,
            "release_gate": target_gate and output_gate}


def check_review(review, expected):
    def fixed_payload(payload):
        fixed = deepcopy(payload)
        fixed.pop("release_metrics", None)
        for row in fixed["samples"]:
            row.pop("judgment", None)
            row.pop("note", None)
        return fixed

    if fixed_payload(review) != fixed_payload(expected):
        raise ValueError("抽查范围、原文、结果或验收门槛被修改，请重新生成抽查文件")
    for row in review["samples"]:
        if row["judgment"] != "pending" and not row["note"].strip():
            raise ValueError("每条已判读样本必须保留原文判读说明")
    return release_score(expected["target_metrics"],
                         [row["judgment"] for row in review["samples"]], expected["criteria"])


def save(path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
    temporary.replace(path)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["sample", "check"])
    parser.add_argument("--evaluation", type=Path, required=True)
    parser.add_argument("--review", type=Path, required=True)
    parser.add_argument("--dataset", type=Path, default=DEFAULT_DATASET)
    parser.add_argument("--criteria", type=Path, default=DEFAULT_CRITERIA)
    args = parser.parse_args(argv)
    settings = Settings()
    version = extraction_version(PROMPT_VERSION, settings.llm_model,
                                 settings.video_graph_reasoning_effort,
                                 settings.video_graph_extraction_effort)
    evaluation = json.loads(args.evaluation.read_text())
    dataset = json.loads(args.dataset.read_text())
    criteria = json.loads(args.criteria.read_text())
    expected = make_sample(evaluation, dataset, criteria, expected_version=version)
    if args.command == "sample":
        if args.review.exists():
            parser.error("复核文件已存在；为保留原判读，请选择新文件名")
        save(args.review, expected)
        print(json.dumps({"population": expected["population_relations"],
                          "sample": len(expected["samples"]),
                          "target_gate": expected["target_metrics"]["gate"]}))
        return 0
    review = json.loads(args.review.read_text())
    metrics = check_review(review, expected)
    review["release_metrics"] = metrics
    save(args.review, review)
    print(json.dumps(metrics, ensure_ascii=False))
    return 0 if metrics["release_gate"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
