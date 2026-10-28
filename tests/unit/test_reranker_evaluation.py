from argparse import Namespace

from app.core.config import Settings

from scripts import evaluate_answer_evidence_counts as base
from scripts.evaluate_reranker import VARIANTS, coverage_and_score


def test_reference_weights_and_failed_generations_affect_composite_score():
    rubric = {"facts": [{"id": "F1", "weight": 1}, {"id": "F2", "weight": 3}]}
    grade = {"grounding": 5, "relevance": 5, "covered_fact_ids": ["F1"]}
    coverage, score, abstention = coverage_and_score(rubric, grade, "generated")
    assert coverage == 0.25
    assert score == 3.5
    assert abstention is None
    assert coverage_and_score(rubric, grade, "error")[1] == 0


def test_negative_controls_require_abstention_without_unsupported_claims():
    grade = {"grounding": 5, "relevance": 5, "abstained": True, "unsupported_claims": []}
    assert coverage_and_score({"facts": []}, grade, "generated") == (None, 5, True)
    grade["unsupported_claims"] = ["invented detail"]
    assert coverage_and_score({"facts": []}, grade, "generated") == (None, 3, False)


async def test_anonymous_judge_evaluates_all_seven_variants_with_their_own_contexts(
    tmp_path, monkeypatch
):
    args = Namespace(output_dir=tmp_path)
    snapshot = {"id": "q", "question": "question", "aspects": [], "contexts": {}}
    for variant in VARIANTS:
        snapshot["contexts"][variant] = {"text": "context:" + variant}
        base.write_json(
            tmp_path / "answers" / f"q__k{variant}__r0.json",
            {
                "answer": "answer:" + variant,
                "status": "generated",
            },
        )
    base.write_json(tmp_path / "rubrics/q.json", {"rubric": {"facts": []}})

    async def completion(settings, system, payload, **kwargs):
        assert "reasoning_effort" not in kwargs
        assert kwargs["max_tokens"] == 16000
        answers = payload["answers"]
        assert {a["id"] for a in answers} == set("ABCDEFG")
        assert {a["answer"] for a in answers} == {"answer:" + v for v in VARIANTS}
        assert all(
            a["provided_evidence"].split(":")[1] == a["answer"].split(":")[1] for a in answers
        )
        return {
            "output": {
                "grades": [
                    {
                        "id": a["id"],
                        "grounding": 5,
                        "relevance": 5,
                        "covered_fact_ids": [],
                        "citation_claims": 0,
                        "supported_citation_claims": 0,
                        "abstained": True,
                        "unsupported_claims": [],
                        "note": "missing evidence",
                    }
                    for a in answers
                ]
            },
            "usage": None,
            "attempt": 1,
        }

    monkeypatch.setattr(base, "json_completion", completion)
    await base.grade(args, Settings(_env_file=None), snapshot, 0, ks=tuple(VARIANTS))
    saved = base.read_json(tmp_path / "grades/q__r0.json")
    assert set(saved["label_to_k"].values()) == set(VARIANTS)
    assert len(saved["grades"]) == 7
