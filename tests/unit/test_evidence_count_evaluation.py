from argparse import Namespace

import pytest

from scripts.evaluate_answer_evidence_counts import KS, summarize, write_json


def experiment(tmp_path):
    args = Namespace(output_dir=tmp_path, repeats=2, max_chars=12000)
    questions = [
        {"id": "normal", "question": "factual question", "category": "factual"},
        {"id": "negative", "question": "missing information", "category": "negative"},
    ]
    for question in questions:
        negative = question["id"] == "negative"
        write_json(
            tmp_path / "rubrics" / (question["id"] + ".json"),
            {
                "rubric": {
                    "answerable": not negative,
                    "facts": []
                    if negative
                    else [{"id": "F1", "weight": 1}, {"id": "F2", "weight": 3}],
                }
            },
        )
        for repetition in range(2):
            labels = dict(zip("ABCD", KS, strict=True))
            grades = []
            for label, k in labels.items():
                write_json(
                    tmp_path / "answers" / f"{question['id']}__k{k}__r{repetition}.json",
                    {
                        "question_id": question["id"],
                        "k": k,
                        "repetition": repetition,
                        "status": "generated",
                        "answer": "test answer",
                        "answer_chars": 11,
                        "context_chars": 1000,
                        "actual_evidence_count": k,
                        "source_count": k,
                        "truncated_primary_count": 0,
                        "ttft_seconds": 1,
                        "generation_seconds": 2,
                        "invalid_citations": [],
                    },
                )
                grades.append(
                    {
                        "id": label,
                        "grounding": 5,
                        "relevance": 5,
                        "covered_fact_ids": []
                        if negative
                        else (["F1", "F2"] if k == 20 else ["F1"]),
                        "citation_claims": 0 if negative else 2,
                        "supported_citation_claims": 0 if negative else 2,
                        "abstained": negative,
                        "unsupported_claims": [],
                    }
                )
            write_json(
                tmp_path / "grades" / f"{question['id']}__r{repetition}.json",
                {
                    "label_to_k": labels,
                    "grades": grades,
                },
            )
    return args, questions


def test_paired_report_weights_reference_facts_and_bootstraps_questions(tmp_path):
    args, questions = experiment(tmp_path)
    report = summarize(args, questions)
    groups = {group["k"]: group for group in report["aggregates"]}
    assert groups[4]["reference_coverage"] == 0.25
    assert groups[4]["score"] == 4.25
    assert groups[20]["reference_coverage"] == 1
    assert groups[20]["score"] == 5
    assert groups[20]["abstention_correct"] == 2
    assert groups[20]["abstention_trials"] == 2
    comparison = report["paired_score_differences"]["20"]
    assert comparison["question_pairs"] == 2  # Four repeats do not become four independent topics.
    assert comparison["difference_from_8"] == 0.75
    assert comparison["bootstrap_95_interval"] == [0, 1.5]


def test_generation_failures_remain_in_quality_but_not_success_latency(tmp_path):
    import json

    args, questions = experiment(tmp_path)
    path = tmp_path / "answers/normal__k4__r0.json"
    failed = json.loads(path.read_text())
    failed.update(status="error", generation_seconds=100)
    write_json(path, failed)
    report = summarize(args, questions)
    group = next(group for group in report["aggregates"] if group["k"] == 4)
    assert group["runs"] == 4
    assert group["generated"] == 3
    assert group["score"] == pytest.approx((0 + 3.5 + 5 + 5) / 4)
    assert group["mean_generation_seconds"] == 2
