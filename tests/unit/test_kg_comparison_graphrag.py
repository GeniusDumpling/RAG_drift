"""Offline unit tests for kg_comparison Step 3 (GraphRAG adapter & runner)."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from typing import Any

import pytest

from kg_comparison import evaluate_baseline
from kg_comparison.graphrag import run as run_graphrag
from kg_comparison.graphrag.engine import GraphRAGConfig, extract_case_library
from kg_comparison.kggen import run as run_kggen
from kg_comparison.kggen.engine import KGGenConfig
from kg_comparison.llm_client import ComparisonLLMClient, DiskResponseCache
from kg_comparison.schema import BaselineRunPayload, CaseUsage

pytestmark = pytest.mark.no_db


def _case(
    case_id: str,
    *,
    title: str,
    text: str,
    language: str = "en",
    models: list[str] | None = None,
    entities: list[dict[str, Any]] | None = None,
    relations: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    return {
        "id": case_id,
        "language": language,
        "models": models or ["DJI Avata 360"],
        "source": {
            "title": title,
            "url": f"https://example.com/{case_id}",
            "chunk_id": f"00000000-0000-0000-0000-00000000000{case_id[-1]}",
            "start_seconds": 0.0,
            "end_seconds": 15.0,
        },
        "text": text,
        "entities": entities or [],
        "relations": relations or [],
        "notes": [],
    }


def _build_dataset(tmp_path: Path) -> Path:
    dataset_path = tmp_path / "graphrag_dataset.json"
    dataset_obj = {
        "version": "relation-gold-v3",
        "status": "human_reviewed",
        "cases": [
            _case(
                "EG001",
                title="Avata 360 Flight",
                text="Avata360 has Propeller Guard to prevent crash.",
                entities=[
                    {"name": "DJI Avata 360", "type": "product", "mentions": ["Avata360"]},
                    {
                        "name": "Propeller Guard",
                        "type": "component",
                        "mentions": ["Propeller Guard"],
                    },
                    {"name": "crash", "type": "fault", "mentions": ["crash"]},
                ],
                relations=[
                    {
                        "subject": "DJI Avata 360",
                        "predicate": "contains",
                        "object": "Propeller Guard",
                        "assertion": "asserted",
                        "quote": "Avata360 has Propeller Guard",
                    },
                    {
                        "subject": "Propeller Guard",
                        "predicate": "mitigates",
                        "object": "crash",
                        "assertion": "asserted",
                        "quote": "Propeller Guard to prevent crash",
                    },
                ],
            ),
            _case(
                "EG002",
                title="Avata 360 Safety",
                text="Avata 360 may include Propeller Guard.",
                entities=[
                    {"name": "DJI Avata 360", "type": "product", "mentions": ["Avata 360"]},
                    {
                        "name": "Propeller Guard",
                        "type": "component",
                        "mentions": ["Propeller Guard"],
                    },
                ],
                relations=[
                    {
                        "subject": "DJI Avata 360",
                        "predicate": "contains",
                        "object": "Propeller Guard",
                        "assertion": "uncertain",
                        "quote": "Avata 360 may include Propeller Guard",
                    }
                ],
            ),
        ],
    }
    dataset_path.write_text(json.dumps(dataset_obj, ensure_ascii=False), encoding="utf-8")
    return dataset_path


class _MockGraphRAGClient:
    """Fake client supporting optional real DiskResponseCache to test cache sharing."""

    def __init__(self, handler: Any, *, cache: DiskResponseCache | None = None) -> None:
        self.handler = handler
        self.cache = cache
        self.calls: list[dict[str, Any]] = []

    def request_json(
        self,
        *,
        system: str,
        user: str,
        stage: str,
        namespace: str,
        usage_acc: CaseUsage,
    ) -> dict[str, Any]:
        body = {"system": system, "user": user}
        if self.cache is not None:

            def _compute() -> tuple[dict[str, Any], dict[str, int]]:
                self.calls.append({"stage": stage, "namespace": namespace, "system": system})
                resp = self.handler(stage=stage, system=system, user=user, namespace=namespace)
                return resp, {"prompt_tokens": 40, "completion_tokens": 15, "total_tokens": 55}

            parsed, usage, replayed = self.cache.get(
                namespace=namespace,
                endpoint_hash="mock-endpoint",
                request_body=body,
                compute=_compute,
            )
            bucket = usage_acc.by_stage.setdefault(
                stage,
                {
                    "http_calls": 0,
                    "replayed_calls": 0,
                    "prompt_tokens": 0,
                    "completion_tokens": 0,
                    "total_tokens": 0,
                },
            )
            if replayed:
                usage_acc.replayed_calls += 1
                bucket["replayed_calls"] += 1
            else:
                usage_acc.http_calls += 1
                bucket["http_calls"] += 1
            if usage:
                usage_acc.prompt_tokens += usage["prompt_tokens"]
                usage_acc.completion_tokens += usage["completion_tokens"]
                usage_acc.total_tokens += usage["total_tokens"]
                bucket["prompt_tokens"] += usage["prompt_tokens"]
                bucket["completion_tokens"] += usage["completion_tokens"]
                bucket["total_tokens"] += usage["total_tokens"]
            return parsed

        self.calls.append({"stage": stage, "namespace": namespace, "system": system})
        usage_acc.http_calls += 1
        usage_acc.prompt_tokens += 40
        usage_acc.completion_tokens += 15
        usage_acc.total_tokens += 55
        bucket = usage_acc.by_stage.setdefault(
            stage, {"http_calls": 0, "prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
        )
        bucket["http_calls"] += 1
        bucket["prompt_tokens"] += 40
        bucket["completion_tokens"] += 15
        bucket["total_tokens"] += 55
        return self.handler(stage=stage, system=system, user=user, namespace=namespace)


def test_graphrag_open_gleaning_and_open_mapped_cache_reuse(tmp_path: Path) -> None:
    dataset_path = _build_dataset(tmp_path)
    cache = DiskResponseCache(tmp_path / "cache")

    def _handler(*, stage: str, system: str, user: str, namespace: str) -> dict[str, Any]:
        if stage == "graphrag_extract":
            if "EG001" in namespace:
                return {
                    "entities": [
                        {
                            "name": "avata360",
                            "entity_type": "geo",  # GraphRAG default-style non-schema type
                            "description": "Indoor drone model",
                            "mentions": ["avata360"],
                        },
                        {
                            "name": "Propeller Guard",
                            "entity_type": "component",
                            "description": "Protective shroud",
                            "mentions": ["Propeller Guard"],
                        },
                    ],
                    "relations": [
                        {
                            "source": "avata360",
                            "target": "Propeller Guard",
                            "description": (
                                "Avata360 includes a protective Propeller Guard for indoor flights."
                            ),
                            "strength": 9,
                        }
                    ],
                }
            return {
                "entities": [
                    {
                        "name": "DJI Avata 360",
                        "entity_type": "organization",
                        "description": "Drone model",
                        "mentions": ["Avata 360"],
                    },
                    {
                        "name": "Propeller Guard",
                        "entity_type": "component",
                        "description": "Safety guard",
                        "mentions": ["Propeller Guard"],
                    },
                ],
                "relations": [
                    {
                        "source": "DJI Avata 360",
                        "target": "Propeller Guard",
                        "description": "Avata 360 may include Propeller Guard.",
                        "strength": 6,
                    }
                ],
            }

        if stage == "graphrag_glean":
            if "EG001" in namespace and "glean0" in namespace:
                # Round 0 recovers missed entity "crash" and reversed-order relation
                return {
                    "entities": [
                        {
                            "name": "crash",
                            "entity_type": "event",
                            "description": "Flight crash event",
                            "mentions": ["crash"],
                        }
                    ],
                    "relations": [
                        {
                            "source": "crash",
                            "target": "Propeller Guard",
                            "description": "crash is mitigated by Propeller Guard during flight.",
                            "strength": 7,
                        },
                        {
                            "source": "avata360",
                            "target": "crash",
                            "description": "noise background link",
                            "strength": 3,
                        },
                    ],
                }
            # Subsequent gleaning rounds return empty -> triggers early stop!
            return {"entities": [], "relations": []}

        if stage == "graphrag_post_map":
            if "EG001" in namespace:
                return {
                    "entity_types": {
                        "avata360": "product",
                        "Propeller Guard": "component",
                        "crash": "fault",
                    },
                    "mapped_relations": [
                        {
                            "index": 0,
                            "predicate": "contains",
                            "direction": "forward",
                            "assertion": "asserted",
                            "quote": "Avata360 has Propeller Guard",
                        },
                        {
                            # Tests direction="reverse" swapping crash <-> Propeller Guard
                            "index": 1,
                            "predicate": "mitigates",
                            "direction": "reverse",
                            "assertion": "asserted",
                            "quote": "Propeller Guard to prevent crash",
                        },
                        {"index": 2, "predicate": None, "direction": "forward"},
                    ],
                }
            return {
                "entity_types": {"DJI Avata 360": "product", "Propeller Guard": "component"},
                "mapped_relations": [
                    {
                        "index": 0,
                        "predicate": "contains",
                        "direction": "forward",
                        "assertion": "uncertain",
                        "quote": "Avata 360 may include Propeller Guard",
                    }
                ],
            }

        raise AssertionError(f"Unexpected stage: {stage}")

    # 1) Run GraphRAG-Open with max_gleanings=2 (EG001 stops after glean1; EG002 after glean0)
    open_client = _MockGraphRAGClient(_handler, cache=cache)
    payload_open, eval_open = run_graphrag.run_experiment(
        dataset_path=dataset_path,
        output_path=tmp_path / "gr_open.preds.json",
        eval_output_path=tmp_path / "gr_open.eval.json",
        config=GraphRAGConfig(mode="open", granularity="per_chunk", max_gleanings=2),
        client=open_client,
        workers=1,
    )
    assert isinstance(payload_open, BaselineRunPayload)
    assert eval_open is not None
    # EG001 ran 1 extract + 2 gleanings (stopped on empty glean1); EG002 ran 1 extract + 1 gleaning
    assert payload_open.results["EG001"].diagnostics["gleanings_executed"] == 2
    assert payload_open.results["EG002"].diagnostics["gleanings_executed"] == 1
    assert any(e.name == "crash" for e in payload_open.results["EG001"].entities)
    # Original-case span "Avata360" recovered into mentions
    assert "Avata360" in payload_open.results["EG001"].entities[0].mentions
    # Narrative long descriptions trigger narrative_or_overlong_predicate -> valid_triple_rate < 1
    st_open = eval_open["metrics"]["structural"]
    assert st_open["valid_triple_rate"] < 1.0
    assert "narrative_or_overlong_predicate" in st_open["invalid_triple_reasons"]
    # Entity Recognition and Endpoint-Pair still score cleanly!
    assert eval_open["metrics"]["entities"]["overall"]["recognition"]["tp"] == 5
    assert eval_open["metrics"]["relations"]["overall"]["endpoint_pair"]["tp"] == 3

    # 2) Run GraphRAG-Open-Mapped sharing the exact cache -> 0 live extract/glean HTTP calls!
    mapped_client = _MockGraphRAGClient(_handler, cache=cache)
    payload_mapped, eval_mapped = run_graphrag.run_experiment(
        dataset_path=dataset_path,
        output_path=tmp_path / "gr_mapped.preds.json",
        eval_output_path=tmp_path / "gr_mapped.eval.json",
        config=GraphRAGConfig(
            mode="open_mapped",
            granularity="per_chunk",
            max_gleanings=2,
            with_assertion=True,
        ),
        client=mapped_client,
        workers=1,
    )
    assert isinstance(payload_mapped, BaselineRunPayload)
    assert eval_mapped is not None
    # Only post_map made live calls; all extract + glean calls were replayed from cache!
    assert [c["stage"] for c in mapped_client.calls] == ["graphrag_post_map", "graphrag_post_map"]
    assert payload_mapped.results["EG001"].usage.replayed_calls == 3
    assert payload_mapped.results["EG001"].diagnostics["dropped_unmapped_relations"] == 1
    # Verify reverse swap produced Propeller Guard -> mitigates -> crash
    mitigates_rels = [
        r for r in payload_mapped.results["EG001"].relations if r.predicate == "mitigates"
    ]
    assert len(mitigates_rels) == 1
    assert mitigates_rels[0].subject == "Propeller Guard"
    assert mitigates_rels[0].object == "crash"
    # Closed-schema Triple + Assertion F1 jumps to 100% on the mapped edges!
    assert eval_mapped["metrics"]["relations"]["overall"]["triple"]["tp"] == 3
    assert eval_mapped["metrics"]["relations"]["overall"]["assertion"]["tp"] == 3


def test_graphrag_constrained_corpus_merge_and_compare(tmp_path: Path) -> None:
    dataset_path = _build_dataset(tmp_path)

    def _constrained_handler(
        *, stage: str, system: str, user: str, namespace: str
    ) -> dict[str, Any]:
        if stage == "graphrag_extract":
            assert "product_family" in system and "mitigates" in system
            if "EG001" in namespace:
                return {
                    "entities": [
                        {
                            "name": "Avata360",
                            "entity_type": "product",
                            "description": "Indoor FPV cinewhoop",
                            "mentions": ["Avata360"],
                        },
                        {
                            "name": "Propeller Guard",
                            "entity_type": "component",
                            "description": "360 blade guard",
                            "mentions": ["Propeller Guard"],
                        },
                        {
                            "name": "crash",
                            "entity_type": "fault",
                            "description": "Impact crash",
                            "mentions": ["crash"],
                        },
                    ],
                    "relations": [
                        {
                            "source": "Avata360",
                            "target": "Propeller Guard",
                            "predicate": "contains",
                            "description": "Avata360 has Propeller Guard",
                            "strength": 9,
                            "assertion": "asserted",
                            "quote": "Avata360 has Propeller Guard",
                        },
                        {
                            "source": "Propeller Guard",
                            "target": "crash",
                            "predicate": "mitigates",
                            "description": "Propeller Guard helps prevent crash",
                            "strength": 8,
                            "assertion": "asserted",
                            "quote": "Propeller Guard to prevent crash",
                        },
                    ],
                }
            return {
                "entities": [
                    {
                        "name": "Avata360",  # Same key merged across TextUnits
                        "entity_type": "product",
                        "description": "Compact panoramic drone",
                        "mentions": ["Avata 360"],
                    },
                    {
                        "name": "Propeller Guard",
                        "entity_type": "component",
                        "description": "Protective cage",
                        "mentions": ["Propeller Guard"],
                    },
                ],
                "relations": [
                    {
                        "source": "Avata360",
                        "target": "Propeller Guard",
                        "predicate": "contains",
                        "description": "May include Propeller Guard",
                        "strength": 6,
                        "assertion": "uncertain",
                        "quote": "Avata 360 may include Propeller Guard",
                    }
                ],
            }

        if stage == "graphrag_glean":
            return {"entities": [], "relations": []}

        if stage == "graphrag_summarize_descriptions":
            payload_in = json.loads(user)
            return {"summary": f"Merged summary: {'; '.join(payload_in['descriptions'])}"}

        raise AssertionError(f"Unexpected stage: {stage}")

    client = _MockGraphRAGClient(_constrained_handler)
    preds_path = tmp_path / "gr_con_batch.preds.json"
    eval_path = tmp_path / "gr_con_batch.eval.json"
    md_path = tmp_path / "gr_con_batch.md"

    payload_gr, eval_gr = run_graphrag.run_experiment(
        dataset_path=dataset_path,
        output_path=preds_path,
        eval_output_path=eval_path,
        eval_report_path=md_path,
        config=GraphRAGConfig(
            mode="constrained",
            granularity="corpus_batch",
            max_gleanings=1,
            summarize_descriptions=True,
            with_assertion=True,
        ),
        client=client,
        workers=1,
    )
    assert isinstance(payload_gr, BaselineRunPayload)
    assert eval_gr is not None and md_path.exists()

    cs = payload_gr.corpus_stats
    assert cs["community_detection_enabled"] is False
    assert cs["pre_merge_entities"] == 5
    assert cs["post_merge_entities"] == 3
    assert cs["multi_unit_entities"] == 2
    assert cs["merged_entity_text_units"]["Avata360"] == ["EG001", "EG002"]
    assert payload_gr.results["EG001"].entities[0].attributes["text_unit_ids"] == "EG001,EG002"
    assert "Merged summary:" in payload_gr.results["EG001"].entities[0].attributes["description"]
    # Original-case mentions preserved strictly per case
    assert eval_gr["metrics"]["structural"]["entity_exact_grounding_rate"] == pytest.approx(1.0)
    assert eval_gr["metrics"]["relations"]["overall"]["assertion"]["tp"] == 3

    # Resume runs 0 additional calls
    prev = len(client.calls)
    run_graphrag.run_experiment(
        dataset_path=dataset_path,
        output_path=preds_path,
        config=GraphRAGConfig(
            mode="constrained",
            granularity="corpus_batch",
            max_gleanings=1,
            summarize_descriptions=True,
            with_assertion=True,
        ),
        client=client,
        workers=1,
    )
    assert len(client.calls) == prev

    # Also run a minimal KGGen eval on same dataset and verify --compare outputs both methods
    def _kg_handler(*, stage: str, system: str, user: str, namespace: str) -> dict[str, Any]:
        if stage == "kggen_stage1_entities":
            return {"entities": [{"name": "Avata360", "mentions": ["Avata360"]}]}
        return {"relations": []}

    kg_eval_path = tmp_path / "kg_eval.json"
    run_kggen.run_experiment(
        dataset_path=dataset_path,
        output_path=tmp_path / "kg_preds.json",
        eval_output_path=kg_eval_path,
        config=KGGenConfig(mode="open", granularity="per_chunk"),
        client=_MockGraphRAGClient(_kg_handler),
        workers=1,
    )
    cmp_md = tmp_path / "compare_kg_gr.md"
    rc = evaluate_baseline.main(
        ["--compare", str(kg_eval_path), str(eval_path), "--report", str(cmp_md)]
    )
    assert rc == 0
    cmp_text = cmp_md.read_text(encoding="utf-8")
    assert "| kggen_open | open/per_chunk |" in cmp_text
    assert "| graphrag_constrained | constrained/corpus_batch |" in cmp_text


def test_graphrag_library_missing_and_cli_dry_run(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    # Ensure ComparisonLLMClient reference stays valid without live calls
    assert ComparisonLLMClient is not None
    monkeypatch.setattr(importlib.util, "find_spec", lambda name: None)
    with pytest.raises(ImportError, match="graphrag"):
        extract_case_library(
            _case("C1", title="T", text="Avata360"), GraphRAGConfig(backend="library")
        )

    for argv, expected_mode, expected_gran in [
        (["--mode", "open", "--granularity", "per_chunk", "--dry-run"], "open", "per_chunk"),
        (
            ["--mode", "open_mapped", "--granularity", "per_chunk", "--dry-run"],
            "open_mapped",
            "per_chunk",
        ),
        (
            ["--mode", "constrained", "--granularity", "corpus_batch", "--dry-run"],
            "constrained",
            "corpus_batch",
        ),
    ]:
        rc = run_graphrag.main(argv)
        assert rc == 0
        out = json.loads(capsys.readouterr().out)
        assert out["cases"] == 20
        assert out["model_called"] is False
        assert out["community_detection_enabled"] is False
        assert out["mode"] == expected_mode
        assert out["granularity"] == expected_gran
