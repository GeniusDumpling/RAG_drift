#!/usr/bin/env python3
"""Paired reranker experiment on frozen candidates; preparation uses read-only SQL."""

from __future__ import annotations

import argparse
import asyncio
import csv
import json
import os
import random
import statistics
import sys
from pathlib import Path

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "backend"))
from app.core.config import get_settings  # noqa: E402
from app.models.content import ContentChunk  # noqa: E402
from app.schemas.search import EvidenceObject  # noqa: E402
from app.services.answer_context import prepare_answer_context  # noqa: E402
from app.services.reranking import SiliconFlowReranker  # noqa: E402

from scripts import evaluate_answer_evidence_counts as base  # noqa: E402

VARIANTS = {
    "baseline8": (60, 8, False),
    "c20_k8": (20, 8, True),
    "c40_k4": (40, 4, True),
    "c40_k8": (40, 8, True),
    "c40_k12": (40, 12, True),
    "c40_k20": (40, 20, True),
    "c60_k8": (60, 8, True),
}


async def prepare(args, settings, questions):
    definition = {
        "variants": {k: list(v) for k, v in VARIANTS.items()},
        "repeats": args.repeats,
        "max_chars": args.max_chars,
        "model": settings.llm_model,
        "reranker_model": settings.reranker_model,
        "reranker_document_max_chars": settings.reranker_document_max_chars,
        "reranker_timeout_seconds": settings.reranker_timeout_seconds,
        "frozen_manifest_sha256": base.digest((args.source_dir / "manifest.json").read_text()),
        "questions_sha256": base.digest(json.dumps(questions, ensure_ascii=False, sort_keys=True)),
        "source_sha256": {
            name: base.digest((ROOT / name).read_text())
            for name in (
                "backend/app/services/reranking.py",
                "backend/app/services/answer_context.py",
                "backend/app/services/answer_generation.py",
                "scripts/evaluate_reranker.py",
                "scripts/evaluate_answer_evidence_counts.py",
            )
        },
        "notes": [
            "Frozen 60-candidate pools from prior evaluation; prefixes are NOT fresh retrievals.",
            "No reranker fallback is allowed in this experiment.",
            "Baseline8 exactly reuses the frozen original context, but generates fresh answers.",
            "Same frozen quote-checked reference rubrics for all variants, including baseline.",
            "Reference rubrics cover original top-20 sources, not every possible candidate fact.",
            "Model-assisted grades; same model generates and judges; small selected sample.",
            "12000-character context budget, two passages per source; reranked order preserved.",
            "Reranker latency measured once per question/pool, shared across K/repetitions.",
            "Generation and rerank phases run separately; combined latency is an estimate.",
        ],
    }
    manifest = args.output_dir / "manifest.json"
    if manifest.exists() and base.read_json(manifest) != definition:
        raise ValueError("Experiment manifest changed; use a new output directory")
    base.write_json(manifest, definition)
    engine = create_async_engine(settings.database_url, pool_pre_ping=True)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        for question in questions:
            path = args.output_dir / "snapshots" / (question["id"] + ".json")
            if path.exists():
                continue
            frozen = base.read_json(args.source_dir / "snapshots" / (question["id"] + ".json"))
            evidence = [EvidenceObject.model_validate(e) for e in frozen["candidates"]]
            async with factory() as session:
                await session.execute(
                    text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
                )
                chunks = (
                    await session.scalars(
                        select(ContentChunk).where(
                            ContentChunk.id.in_([e.chunk_id for e in evidence]),
                            ContentChunk.embed_status != "obsolete",
                        )
                    )
                ).all()
                texts = {str(c.id): c.display_text.strip() for c in chunks}
                if texts != frozen["raw_main_texts"]:
                    raise ValueError("Source chunks changed since frozen baseline")
                rankings = {}
                for n in (20, 40, 60):
                    rank_path = args.output_dir / "rankings" / f"{question['id']}__c{n}.json"
                    if rank_path.exists():
                        cached = base.read_json(rank_path)
                        ranked = [EvidenceObject.model_validate(e) for e in cached["evidence"]]
                        trace = cached["trace"]
                    else:
                        result = await SiliconFlowReranker(settings).rerank(
                            question["question"], evidence[:n], texts
                        )
                        if result.trace["status"] != "reranked":
                            raise RuntimeError(f"Rerank failed: {result.trace}")
                        ranked, trace = result.evidence, result.trace
                        base.write_json(
                            rank_path,
                            {
                                "evidence": [e.model_dump(mode="json") for e in ranked],
                                "trace": trace,
                            },
                        )
                    rankings[n] = (ranked, trace)
                contexts = {"baseline8": {**frozen["contexts"]["8"], "rerank_seconds": 0}}
                for variant, (n, k, enabled) in VARIANTS.items():
                    if not enabled:
                        continue
                    ranked, trace = rankings[n]
                    context = await prepare_answer_context(
                        session,
                        ranked,
                        max_chars=args.max_chars,
                        max_evidence=k,
                        preserve_order=True,
                    )
                    contexts[variant] = {
                        "evidence": [e.model_dump(mode="json") for e in context.evidence],
                        "text": context.text,
                        "text_sha256": base.digest(context.text),
                        "context_chunk_ids": [str(c) for c in context.chunk_ids],
                        "actual_evidence_count": len(context.evidence),
                        "source_count": len({e.content_item_id for e in context.evidence}),
                        "truncated_primary_count": sum(
                            texts[str(e.chunk_id)] not in b.split("内容: ", 1)[-1]
                            for e, b in zip(
                                context.evidence, base.blocks(context.text), strict=True
                            )
                        ),
                        "rerank_seconds": trace["latency_ms"] / 1000,
                    }
                await session.rollback()
            snapshot = {**frozen, "contexts": contexts}
            base.write_json(path, snapshot)
            base.write_json(
                args.output_dir / "rubrics" / (question["id"] + ".json"),
                base.read_json(args.source_dir / "rubrics" / (question["id"] + ".json")),
            )
            base.progress("prepared", question=question["id"], variants=list(contexts))
    finally:
        await engine.dispose()


def coverage_and_score(rubric, grade, status):
    coverage, abstention = None, None
    if rubric["facts"]:
        coverage = sum(f["weight"] for f in rubric["facts"] if f["id"] in grade["covered_fact_ids"])
        coverage /= sum(f["weight"] for f in rubric["facts"])
    else:
        abstention = grade["abstained"] and not grade["unsupported_claims"]
    score = (
        0.4 * grade["grounding"]
        + 0.4 * (coverage if coverage is not None else float(abstention)) * 5
        + 0.2 * grade["relevance"]
    )
    return coverage, score if status == "generated" else 0, abstention


def report(args, questions):
    rows = []
    for question in questions:
        snapshot = base.read_json(args.output_dir / "snapshots" / (question["id"] + ".json"))
        rubric = base.read_json(args.output_dir / "rubrics" / (question["id"] + ".json"))["rubric"]
        for repetition in range(args.repeats):
            gp = args.output_dir / "grades" / f"{question['id']}__r{repetition}.json"
            grades = {}
            if gp.exists():
                bundle = base.read_json(gp)
                grades = {bundle["label_to_k"][g["id"]]: g for g in bundle["grades"]}
            for variant in VARIANTS:
                path = (
                    args.output_dir
                    / "answers"
                    / f"{question['id']}__k{variant}__r{repetition}.json"
                )
                if not path.exists():
                    continue
                answer = base.read_json(path)
                g = grades.get(variant)
                c, score, abstention = (
                    coverage_and_score(rubric, g, answer["status"]) if g else (None, None, None)
                )
                overhead = snapshot["contexts"][variant]["rerank_seconds"]
                rows.append(
                    {
                        **answer,
                        "variant": variant,
                        "category": question["category"],
                        "grade": g,
                        "coverage": c,
                        "score": score,
                        "abstention_correct": abstention,
                        "rerank_seconds": overhead,
                        "combined_seconds": answer["generation_seconds"] + overhead,
                        "combined_ttft_seconds": answer["ttft_seconds"] + overhead
                        if answer["ttft_seconds"] is not None
                        else None,
                    }
                )
    aggregates = []
    for variant in VARIANTS:
        group = [r for r in rows if r["variant"] == variant]
        graded = [r for r in group if r["grade"]]

        def avg(key, items=None, group=group):
            return base.mean(
                [r[key] for r in (group if items is None else items) if r[key] is not None]
            )

        cited = sum(r["grade"]["citation_claims"] for r in graded)
        aggregates.append(
            {
                "variant": variant,
                "runs": len(group),
                "generated": sum(r["status"] == "generated" for r in group),
                "graded": len(graded),
                "score": avg("score"),
                "reference_coverage": avg("coverage"),
                "citation_support_rate": sum(
                    r["grade"]["supported_citation_claims"] for r in graded
                )
                / cited
                if cited
                else None,
                **{
                    key: avg(key)
                    for key in (
                        "generation_seconds",
                        "ttft_seconds",
                        "rerank_seconds",
                        "combined_seconds",
                        "combined_ttft_seconds",
                        "context_chars",
                        "actual_evidence_count",
                        "truncated_primary_count",
                    )
                },
                "invalid_citations": sum(len(r["invalid_citations"]) for r in group),
                "abstention_correct": sum(r["abstention_correct"] is True for r in graded),
                "abstention_trials": sum(r["abstention_correct"] is not None for r in graded),
                "category_scores": {
                    category: base.mean(
                        [
                            r["score"]
                            for r in group
                            if r["category"] == category and r["score"] is not None
                        ]
                    )
                    for category in sorted({q["category"] for q in questions})
                },
            }
        )
    comparisons = {}
    for anchor in ("baseline8", "c40_k8"):
        for variant in VARIANTS:
            if variant == anchor:
                continue
            differences = []
            for q in questions:
                values = {
                    v: [
                        r["score"]
                        for r in rows
                        if r["question_id"] == q["id"]
                        and r["variant"] == v
                        and r["score"] is not None
                    ]
                    for v in (anchor, variant)
                }
                if all(values.values()):
                    differences.append(
                        statistics.mean(values[variant]) - statistics.mean(values[anchor])
                    )
            if differences:
                rng = random.Random(base.SEED)
                samples = sorted(
                    statistics.mean(rng.choices(differences, k=len(differences)))
                    for _ in range(5000)
                )
                comparisons[f"{variant} - {anchor}"] = {
                    "question_pairs": len(differences),
                    "difference": base.mean(differences),
                    "bootstrap_95_interval": [samples[125], samples[4874]],
                }
    result = {"aggregates": aggregates, "paired_differences": comparisons, "rows": rows}
    base.write_json(args.output_dir / "summary.json", result)
    with (args.output_dir / "metrics.csv").open("w") as handle:
        fields = [k for k in rows[0] if k not in ("grade", "answer")] if rows else ["variant"]
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
    lines = [
        "# Reranker 实验结果",
        "",
        "| 配置 | 成功/评分 | 综合分 | 要点覆盖 | 引用支持 | 重排秒 | 生成秒 | 合计秒¹ | 首字秒¹ |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]

    def fmt(value, percent=False):
        return "NA" if value is None else f"{value:.1%}" if percent else f"{value:.2f}"

    for a in aggregates:
        lines.append(
            f"| {a['variant']} | {a['generated']}/{a['graded']} | "
            + " | ".join(
                fmt(a[k], k in ("reference_coverage", "citation_support_rate"))
                for k in (
                    "score",
                    "reference_coverage",
                    "citation_support_rate",
                    "rerank_seconds",
                    "generation_seconds",
                    "combined_seconds",
                    "combined_ttft_seconds",
                )
            )
            + " |"
        )
    lines.extend(
        [
            "",
            "¹ 合计与首字为独立测量的重排时间＋生成时间，不含共有的初始检索和上下文准备。",
            "",
            "| 问题 | " + " | ".join(VARIANTS) + " |",
            "|---|" + "---:|" * len(VARIANTS),
        ]
    )
    for q in questions:
        lines.append(
            "| "
            + q["question"]
            + " | "
            + " | ".join(
                fmt(
                    base.mean(
                        [
                            r["score"]
                            for r in rows
                            if r["question_id"] == q["id"]
                            and r["variant"] == v
                            and r["score"] is not None
                        ]
                    )
                )
                for v in VARIANTS
            )
            + " |"
        )
    (args.output_dir / "summary.md").write_text("\n".join(lines) + "\n")
    answers = ["# 全部原始答案对照", ""]
    for q in questions:
        answers.extend(["## " + q["question"], ""])
        for row in rows:
            if row["question_id"] == q["id"]:
                answers.extend(
                    [f"### {row['variant']} / 重复 {row['repetition'] + 1}", "", row["answer"], ""]
                )
    (args.output_dir / "answers-comparison.md").write_text("\n".join(answers))
    base.progress("summary", aggregates=aggregates, paired_differences=comparisons)


async def run(args):
    os.umask(0o077)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    settings = get_settings().model_copy(update={"reranker_provider": "siliconflow"})
    questions = base.read_json(args.questions)
    if args.phase == "prepare":
        await prepare(args, settings, questions)
        return
    snapshots = [
        base.read_json(args.output_dir / "snapshots" / (q["id"] + ".json")) for q in questions
    ]
    semaphore = asyncio.Semaphore(args.concurrency)

    async def limited(function, *values, **kwargs):
        async with semaphore:
            await function(*values, **kwargs)

    if args.phase == "answers":
        jobs = [
            (s, v, repetition)
            for s in snapshots
            for v in VARIANTS
            for repetition in range(args.repeats)
        ]
        random.Random(base.SEED).shuffle(jobs)
        await asyncio.gather(*(limited(base.generate, args, settings, *job) for job in jobs))
    if args.phase == "grade":
        results = await asyncio.gather(
            *(
                limited(base.grade, args, settings, s, repetition, ks=tuple(VARIANTS))
                for s in snapshots
                for repetition in range(args.repeats)
            ),
            return_exceptions=True,
        )
        failures = [
            type(result).__name__ for result in results if isinstance(result, BaseException)
        ]
        if failures:
            raise RuntimeError(f"Incomplete grades, resume --phase grade: {failures}")
    report(args, questions)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--source-dir", type=Path, default=ROOT / "data/evaluations/evidence-k-20261026"
    )
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument(
        "--questions",
        type=Path,
        default=ROOT / "scripts/evaluation_data/evidence_count_questions.json",
    )
    parser.add_argument("--phase", choices=("prepare", "answers", "grade", "report"), required=True)
    parser.add_argument("--max-chars", type=int, default=12000)
    parser.add_argument("--repeats", type=int, default=2)
    parser.add_argument("--concurrency", type=int, choices=(1, 2, 3, 4), default=3)
    args = parser.parse_args()
    if args.repeats < 1 or args.max_chars != 12000:
        parser.error("Frozen baseline requires 12000 characters and positive repeats")
    asyncio.run(run(args))


if __name__ == "__main__":
    main()
