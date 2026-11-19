"""Offline unit tests for kg_comparison (Step 1 baseline evaluator & Step 2 KGGen adapter)."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from typing import Any

import pytest

from kg_comparison import evaluate_baseline
from kg_comparison.adapter import adapt_case_prediction, load_predictions_payload
from kg_comparison.kggen import run as run_kggen
from kg_comparison.kggen.engine import KGGenConfig, extract_case_library
from kg_comparison.llm_client import DiskResponseCache, parse_json_content
from kg_comparison.metrics import (
    aggregate_usage_metrics,
    compute_structural_metrics,
    evaluate_mine1_facts,
    is_valid_triple,
    mine1_retrieve_two_hop_subgraph,
)
from kg_comparison.schema import (
    BaselineEntity,
    BaselineRelation,
    BaselineRunPayload,
    CasePrediction,
    CaseUsage,
)

pytestmark = pytest.mark.no_db


def _sample_case(
    case_id: str = "C001",
    *,
    language: str = "zh",
    title: str = "DJI Avata 360 室内测试",
    text: str | list[str] = "Avata360有桨叶保护，而且Mini 4和Mini 4 Pro不支持这个配件。",
    models: list[str] | None = None,
    entities: list[dict[str, Any]] | None = None,
    relations: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    return {
        "id": case_id,
        "language": language,
        "models": models or ["DJI Avata 360", "DJI Mini 4", "DJI Mini 4 Pro"],
        "source": {
            "title": title,
            "url": f"https://example.com/{case_id}",
            "chunk_id": "00000000-0000-0000-0000-000000000001",
            "start_seconds": 0.0,
            "end_seconds": 10.0,
        },
        "text": text,
        "entities": entities or [],
        "relations": relations or [],
        "notes": [],
    }


def test_adapter_grounding_defaults_and_dangling_endpoints() -> None:
    case = _sample_case(
        text="Avata360 has Propeller Guard and uses O4 transmission.",
        language="en",
    )
    pred = CasePrediction(
        status="success",
        entities=[
            BaselineEntity(
                key="e1",
                name="DJI Avata 360",
                entity_type="product",
                mentions=["Avata360"],
            ),
            BaselineEntity(
                key="e2",
                name="propeller guard",
                entity_type="component",
                mentions=["propeller guard"],
            ),
            BaselineEntity(
                key="e3",
                name="Phantom Hallucination",
                entity_type="component",
                mentions=["Phantom Hallucination"],
            ),
        ],
        relations=[
            # Missing assertion and qualifiers -> adapter fills defaults; dangling object endpoint
            BaselineRelation(
                subject="e1",
                predicate="contains",
                object="e2",
                assertion=None,
                qualifiers={},
                quote="Avata360 has Propeller Guard",
            ),
            BaselineRelation(
                subject="e1",
                predicate="is equipped with",
                object="O4 transmission",
                assertion=None,
                qualifiers={},
                quote="",
            ),
        ],
    )

    adapted = adapt_case_prediction(case, pred)
    assert adapted["status"] == "success"
    by_name = {e["name"]: e for e in adapted["entities"]}
    assert by_name["DJI Avata 360"]["grounding"] == "exact"
    assert by_name["propeller guard"]["grounding"] == "case_insensitive"
    assert "Propeller Guard" in by_name["propeller guard"]["mentions"]
    assert by_name["Phantom Hallucination"]["grounding"] == "ungrounded"
    assert [e["name"] for e in adapted["ungrounded_entities"]] == ["Phantom Hallucination"]
    assert by_name["O4 transmission"]["implicit_from_relation"] is True

    diag = adapted["diagnostics"]
    assert diag["defaulted_assertions"] == 2
    assert diag["out_of_schema_predicates"] == 1
    assert diag["dangling_relation_endpoints"] == 1
    assert diag["exact_grounded_entities"] == 2  # DJI Avata 360 + implicit O4 transmission
    assert diag["case_insensitive_grounded_entities"] == 1
    assert diag["ungrounded_entities_count"] == 1

    assert adapted["relations"][0]["assertion"] == "asserted"
    assert adapted["relations"][0]["qualifiers"] == {}
    assert adapted["relations"][0]["type_rule_valid"] is True
    assert adapted["relations"][1]["predicate_in_schema"] is False


def test_load_native_rag_drift_payload_and_usage(tmp_path: Path) -> None:
    native_json = {
        "dataset_version": "relation-gold-v3",
        "dataset_sha256": "abc123",
        "extraction_version": "v2-test",
        "input_scope": "title_and_current_chunk_only",
        "results": {
            "EG001": {
                "status": "success",
                "seconds": 3.5,
                "output": {
                    "entities": [
                        {
                            "key": "avata",
                            "surface_text": "Avata360",
                            "canonical_name": "DJI Avata 360",
                            "entity_type": "product",
                            "identity_scope": "global",
                            "confidence": 0.9,
                        }
                    ],
                    "relations": [
                        {
                            "subject": "avata",
                            "object": "guard",
                            "predicate": "contains",
                            "quote": "Avata360有桨叶保护",
                            "assertion": "asserted",
                            "confidence": 0.85,
                            "qualifiers": {"modality": "actual"},
                        }
                    ],
                },
                "cache_trace": [
                    {
                        "stage": "entity_extraction",
                        "replayed": False,
                        "usage": {
                            "http_responses": 1,
                            "prompt_tokens": 100,
                            "completion_tokens": 40,
                            "total_tokens": 140,
                        },
                    },
                    {"stage": "entity_review", "replayed": True, "usage": None},
                ],
            }
        },
    }
    path = tmp_path / "native.json"
    path.write_text(json.dumps(native_json, ensure_ascii=False), encoding="utf-8")

    loaded = load_predictions_payload(path)
    assert loaded.method == "rag_drift"
    assert loaded.method_version == "v2-test"
    pred = loaded.results["EG001"]
    assert pred.status == "success"
    assert pred.entities[0].name == "DJI Avata 360"
    assert pred.entities[0].mentions == ["Avata360"]
    assert pred.usage.prompt_tokens == 100
    assert pred.usage.total_tokens == 140
    assert pred.usage.http_calls == 1
    assert pred.usage.replayed_calls == 1


def test_evaluate_baseline_entity_owner_conflict_and_relation_metrics(tmp_path: Path) -> None:
    case = _sample_case(
        case_id="C1",
        text="DJI Mini 4 Pro supports ActiveTrack and connects to RC 2. Mini 4 is older.",
        language="en",
        models=["DJI Mini 4", "DJI Mini 4 Pro"],
        entities=[
            {"name": "DJI Mini 4", "type": "product", "mentions": ["Mini 4"]},
            {"name": "DJI Mini 4 Pro", "type": "product", "mentions": ["DJI Mini 4 Pro"]},
            {"name": "ActiveTrack", "type": "technology", "mentions": ["ActiveTrack"]},
            {"name": "DJI RC 2", "type": "product", "mentions": ["RC 2"]},
        ],
        relations=[
            {
                "subject": "DJI Mini 4 Pro",
                "predicate": "supports",
                "object": "ActiveTrack",
                "assertion": "asserted",
                "quote": "DJI Mini 4 Pro supports ActiveTrack",
                "qualifiers": {"modality": "actual"},
            },
            {
                "subject": "DJI Mini 4 Pro",
                "predicate": "connected_to",
                "object": "DJI RC 2",
                "assertion": "asserted",
                "quote": "connects to RC 2",
            },
        ],
    )
    payload = BaselineRunPayload(
        method="kggen_open",
        method_version="test-v1",
        mode="open",
        granularity="per_chunk",
        results={
            "C1": CasePrediction(
                status="success",
                entities=[
                    # Should NOT match gold "DJI Mini 4 Pro" because owner_models conflict
                    BaselineEntity(
                        key="Mini 4", name="DJI Mini 4", entity_type=None, mentions=["Mini 4"]
                    ),
                    BaselineEntity(
                        key="Mini 4 Pro",
                        name="DJI Mini 4 Pro",
                        entity_type="product",
                        mentions=["DJI Mini 4 Pro"],
                    ),
                    BaselineEntity(
                        key="ActiveTrack",
                        name="ActiveTrack",
                        entity_type="component",  # wrong type for untyped_triple test
                        mentions=["ActiveTrack"],
                    ),
                    BaselineEntity(
                        key="RC 2", name="RC 2", entity_type="product", mentions=["RC 2"]
                    ),
                ],
                relations=[
                    BaselineRelation(
                        subject="Mini 4 Pro",
                        predicate="supports",
                        object="ActiveTrack",
                        assertion=None,
                        qualifiers={},
                    ),
                    # Symmetric predicate reversed order
                    BaselineRelation(
                        subject="RC 2",
                        predicate="connected_to",
                        object="Mini 4 Pro",
                        assertion="asserted",
                        qualifiers={},
                    ),
                ],
            )
        },
    )

    report = evaluate_baseline.evaluate_payload(
        payload, [case], dataset_version="test", dataset_sha256="sha", has_relations=True
    )
    ent_overall = report["metrics"]["entities"]["overall"]
    assert ent_overall["recognition"]["tp"] == 4
    assert ent_overall["typed"]["tp"] == 2  # Mini 4 Pro + RC 2 (Mini 4 has None, ActiveTrack wrong)

    rel_overall = report["metrics"]["relations"]["overall"]
    # Triple (requires typed endpoints): only connected_to matches
    assert rel_overall["triple"]["tp"] == 1
    assert rel_overall["triple"]["fn"] == 1
    # Untyped triple ignores ActiveTrack's mismatched type -> both relations match!
    assert rel_overall["untyped_triple"]["tp"] == 2
    assert rel_overall["endpoint_pair"]["tp"] == 2
    # Qualified treats missing modality and explicit modality='actual' identically on matched triple
    assert rel_overall["qualified"]["tp"] == 1

    # Verify --compare CLI builds valid Markdown summary table
    eval_a = tmp_path / "a.json"
    eval_b = tmp_path / "b.json"
    cmp_md = tmp_path / "compare.md"
    cmp_json = tmp_path / "compare.json"
    eval_a.write_text(json.dumps(report, ensure_ascii=False), encoding="utf-8")
    report_b = {**report, "method": "rag_drift", "mode": "native"}
    eval_b.write_text(json.dumps(report_b, ensure_ascii=False), encoding="utf-8")

    rc = evaluate_baseline.main(
        ["--compare", str(eval_a), str(eval_b), "--report", str(cmp_md), "--output", str(cmp_json)]
    )
    assert rc == 0
    md_text = cmp_md.read_text(encoding="utf-8")
    assert "| kggen_open | open/per_chunk |" in md_text
    assert "| rag_drift | native/per_chunk |" in md_text


def test_structural_usage_and_mine1_metrics() -> None:
    assert is_valid_triple({"subject": "Avata 360", "predicate": "uses", "object": "O4"})[0]
    ok, reasons = is_valid_triple({"subject": "it", "predicate": "uses", "object": "O4"})
    assert not ok and "pronoun_or_degenerate_entity" in reasons
    ok2, reasons2 = is_valid_triple(
        {"subject": "Avata 360", "predicate": "uses", "object": "avata 360"}
    )
    assert not ok2 and "self_loop" in reasons2
    ok_zh, reasons_zh = is_valid_triple({"subject": "它", "predicate": "uses", "object": "O4"})
    assert not ok_zh and "pronoun_or_degenerate_entity" in reasons_zh
    ok3, reasons3 = is_valid_triple(
        {
            "subject": "Avata 360",
            "predicate": "this is a very long descriptive sentence with more than eight words here",
            "object": "O4",
        }
    )
    assert not ok3 and "narrative_or_overlong_predicate" in reasons3

    case = _sample_case(text="Avata 360 uses O4.")
    case_usage = CaseUsage(prompt_tokens=60, completion_tokens=30, total_tokens=90, http_calls=2)
    adapted = {
        "C001": adapt_case_prediction(
            case,
            CasePrediction(
                status="success",
                entities=[
                    BaselineEntity(key="Avata 360", name="Avata 360", mentions=["Avata 360"]),
                    BaselineEntity(key="O4", name="O4", mentions=["O4"]),
                ],
                relations=[
                    BaselineRelation(subject="Avata 360", predicate="uses", object="O4"),
                    BaselineRelation(subject="it", predicate="has", object="O4"),
                ],
                seconds=1.5,
                usage=case_usage,
            ),
        )
    }
    st = compute_structural_metrics(adapted)
    assert st["valid_triples"] == 1
    assert st["total_relations"] == 2
    assert st["valid_triple_rate"] == pytest.approx(0.5)
    assert st["relation_reuse_ratio"] == pytest.approx(1.0)
    assert st["avg_degree"] is not None

    payload = BaselineRunPayload(
        method="kggen_open",
        method_version="v1",
        corpus_usage=CaseUsage(
            prompt_tokens=20, completion_tokens=10, total_tokens=30, http_calls=1
        ),
        corpus_seconds=0.5,
        results={
            "C001": CasePrediction(
                status="success",
                seconds=1.5,
                usage=case_usage,
            )
        },
    )
    u = aggregate_usage_metrics(payload, case_ids=["C001"], valid_triples=st["valid_triples"])
    assert u["total_tokens"] == 120
    assert u["http_calls"] == 3
    assert u["total_seconds"] == pytest.approx(2.0)
    assert u["tokens_per_valid_triple"] == pytest.approx(120.0)

    triples = [
        ("DJI Avata 360", "contains", "桨叶保护罩"),
        ("桨叶保护罩", "mitigates", "炸机"),
        ("unrelated", "uses", "foo"),
    ]
    sub = mine1_retrieve_two_hop_subgraph(triples, "Avata 360 桨叶保护罩", top_k_nodes=1)
    assert ("DJI Avata 360", "contains", "桨叶保护罩") in sub
    assert ("桨叶保护罩", "mitigates", "炸机") in sub
    assert ("unrelated", "uses", "foo") not in sub

    mine1 = evaluate_mine1_facts(
        triples,
        ["Avata 360 mitigates 炸机", "Phantom 5 supports RTK"],
        lambda fact, sg: any(e[2] == "炸机" for e in sg) and "炸机" in fact,
        top_k_nodes=2,
    )
    assert mine1["supported_facts"] == 1
    assert mine1["mine1_score"] == pytest.approx(0.5)


def test_disk_response_cache_and_json_parser(tmp_path: Path) -> None:
    assert parse_json_content('```json\n{"entities": ["A"]}\n```') == {"entities": ["A"]}
    with pytest.raises(ValueError):
        parse_json_content('["not-an-object"]')

    cache = DiskResponseCache(tmp_path / "cache", cache_only=False)
    calls = 0

    def _compute() -> tuple[dict[str, Any], dict[str, Any]]:
        nonlocal calls
        calls += 1
        return {"ok": True}, {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}

    resp1, usage1, rep1 = cache.get(
        namespace="ns1", endpoint_hash="ep1", request_body={"q": 1}, compute=_compute
    )
    resp2, usage2, rep2 = cache.get(
        namespace="ns1", endpoint_hash="ep1", request_body={"q": 1}, compute=_compute
    )
    assert calls == 1
    assert not rep1 and rep2
    assert resp1 == resp2 == {"ok": True}
    assert usage1 == usage2 == {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}

    cache_ro = DiskResponseCache(tmp_path / "cache", cache_only=True)
    with pytest.raises(KeyError):
        cache_ro.get(namespace="ns1", endpoint_hash="ep1", request_body={"q": 2}, compute=_compute)


class _MockLLMClient:
    """Deterministic fake client recording prompts and accumulating token usage per stage."""

    def __init__(self, handler: Any) -> None:
        self.handler = handler
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
        self.calls.append({"stage": stage, "namespace": namespace, "system": system, "user": user})
        usage_acc.http_calls += 1
        usage_acc.prompt_tokens += 50
        usage_acc.completion_tokens += 20
        usage_acc.total_tokens += 70
        bucket = usage_acc.by_stage.setdefault(
            stage, {"http_calls": 0, "prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
        )
        bucket["http_calls"] += 1
        bucket["prompt_tokens"] += 50
        bucket["completion_tokens"] += 20
        bucket["total_tokens"] += 70
        return self.handler(stage=stage, system=system, user=user, namespace=namespace)


def test_kggen_open_and_constrained_per_chunk_and_corpus_clustering(tmp_path: Path) -> None:
    dataset_path = tmp_path / "dataset.json"
    dataset_obj = {
        "version": "relation-gold-v3",
        "status": "human_reviewed",
        "cases": [
            _sample_case(
                case_id="EG001",
                title="Avata 360 Review",
                text="Avata360 has Propeller Guard. DJI Mini 4 differs from DJI Mini 4 Pro.",
                models=["DJI Avata 360", "DJI Mini 4", "DJI Mini 4 Pro"],
                entities=[
                    {"name": "DJI Avata 360", "type": "product", "mentions": ["Avata360"]},
                    {
                        "name": "Propeller Guard",
                        "type": "component",
                        "mentions": ["Propeller Guard"],
                    },
                    {"name": "DJI Mini 4", "type": "product", "mentions": ["DJI Mini 4"]},
                    {"name": "DJI Mini 4 Pro", "type": "product", "mentions": ["DJI Mini 4 Pro"]},
                ],
                relations=[
                    {
                        "subject": "DJI Avata 360",
                        "predicate": "contains",
                        "object": "Propeller Guard",
                        "assertion": "asserted",
                        "quote": "Avata360 has Propeller Guard",
                    }
                ],
            ),
            _sample_case(
                case_id="EG002",
                title="Avata 360 Safety",
                text="Avata 360 may include Propeller Guard.",
                models=["DJI Avata 360"],
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

    # 1) KGGen-Open corpus_batch: Stage 1 + Stage 2 + Stage 3 entity & predicate clustering
    def _open_handler(*, stage: str, system: str, user: str, namespace: str) -> dict[str, Any]:
        if stage == "kggen_stage1_entities":
            if "EG001" in namespace:
                return {
                    "entities": [
                        {"name": "avata360", "mentions": ["avata360"]},
                        {"name": "Propeller Guard", "mentions": ["Propeller Guard"]},
                        {"name": "DJI Mini 4", "mentions": ["DJI Mini 4"]},
                        {"name": "DJI Mini 4 Pro", "mentions": ["DJI Mini 4 Pro"]},
                    ]
                }
            return {
                "entities": [
                    {"name": "DJI Avata 360", "mentions": ["Avata 360"]},
                    {"name": "Propeller Guard", "mentions": ["Propeller Guard"]},
                ]
            }
        if stage == "kggen_stage1_relations":
            if "EG001" in namespace:
                return {
                    "relations": [
                        {"subject": "avata360", "predicate": "has", "object": "Propeller Guard"}
                    ]
                }
            return {
                "relations": [
                    {
                        "subject": "DJI Avata 360",
                        "predicate": "includes",
                        "object": "Propeller Guard",
                    }
                ]
            }
        if stage == "kggen_stage3_cluster_entities":
            assert "DJI Mini 4" in system  # anti-overmerge guardrails present
            return {
                "clusters": [
                    {"canonical": "DJI Avata 360", "aliases": ["avata360", "DJI Avata 360"]}
                ]
            }
        if stage == "kggen_stage3_cluster_predicates":
            return {"clusters": [{"canonical": "contains", "aliases": ["has", "includes"]}]}
        raise AssertionError(f"Unexpected stage: {stage}")

    open_client = _MockLLMClient(_open_handler)
    open_out = tmp_path / "open_batch.preds.json"
    open_eval_out = tmp_path / "open_batch.eval.json"
    open_eval_md = tmp_path / "open_batch.md"

    payload_open, eval_open = run_kggen.run_experiment(
        dataset_path=dataset_path,
        output_path=open_out,
        eval_output_path=open_eval_out,
        eval_report_path=open_eval_md,
        config=KGGenConfig(mode="open", granularity="corpus_batch", enable_clustering=True),
        client=open_client,
        workers=1,
    )
    assert isinstance(payload_open, BaselineRunPayload)
    assert eval_open is not None and open_eval_md.exists()

    # Check Stage 3 compression and mention preservation
    cs = payload_open.corpus_stats
    assert cs["pre_cluster_entities"] == 5
    assert cs["post_cluster_entities"] == 4
    assert cs["entity_alias_map"] == {"avata360": "DJI Avata 360"}
    assert cs["predicate_alias_map"] == {"has": "contains", "includes": "contains"}
    # EG001 entity canonicalized to "DJI Avata 360" while preserving exact text span "Avata360"!
    eg1_entities = {e.name: e for e in payload_open.results["EG001"].entities}
    assert "DJI Avata 360" in eg1_entities
    assert "Avata360" in eg1_entities["DJI Avata 360"].mentions
    assert "DJI Mini 4" in eg1_entities and "DJI Mini 4 Pro" in eg1_entities
    # Grounding rate stays 100% exact because original mentions were preserved
    assert eval_open["metrics"]["structural"]["entity_exact_grounding_rate"] == pytest.approx(1.0)
    assert eval_open["metrics"]["entities"]["overall"]["recognition"]["f1"] == pytest.approx(1.0)
    assert eval_open["metrics"]["relations"]["overall"]["untyped_triple"]["tp"] == 2

    # 2) KGGen-Constrained (without assertion vs with_assertion) skips predicate clustering
    def _constrained_handler(
        *, stage: str, system: str, user: str, namespace: str
    ) -> dict[str, Any]:
        if stage == "kggen_stage1_entities":
            assert "product_family" in system and "fault" in system
            return {
                "entities": [
                    {"name": "DJI Avata 360", "entity_type": "product", "mentions": ["Avata360"]},
                    {
                        "name": "Propeller Guard",
                        "entity_type": "component",
                        "mentions": ["Propeller Guard"],
                    },
                ]
                if "EG001" in namespace
                else [
                    {"name": "DJI Avata 360", "entity_type": "product", "mentions": ["Avata 360"]},
                    {
                        "name": "Propeller Guard",
                        "entity_type": "component",
                        "mentions": ["Propeller Guard"],
                    },
                ]
            }
        if stage == "kggen_stage1_relations":
            assert "compatible_with" in system and "reports_fault" in system
            if "EG002" in namespace and "uncertain" in system:
                return {
                    "relations": [
                        {
                            "subject": "DJI Avata 360",
                            "predicate": "contains",
                            "object": "Propeller Guard",
                            "assertion": "uncertain",
                            "quote": "Avata 360 may include Propeller Guard",
                        }
                    ]
                }
            return {
                "relations": [
                    {
                        "subject": "DJI Avata 360",
                        "predicate": "contains",
                        "object": "Propeller Guard",
                    }
                ]
            }
        if stage == "kggen_stage3_cluster_entities":
            return {"clusters": []}
        raise AssertionError(f"Stage should not run in constrained mode: {stage}")

    constrained_client = _MockLLMClient(_constrained_handler)
    payload_con, eval_con = run_kggen.run_experiment(
        dataset_path=dataset_path,
        output_path=tmp_path / "constrained_batch.preds.json",
        eval_output_path=tmp_path / "constrained_batch.eval.json",
        config=KGGenConfig(
            mode="constrained",
            granularity="corpus_batch",
            enable_clustering=True,
            with_assertion=True,
        ),
        client=constrained_client,
        workers=1,
    )
    assert isinstance(payload_con, BaselineRunPayload)
    assert eval_con is not None
    assert not any(
        c["stage"] == "kggen_stage3_cluster_predicates" for c in constrained_client.calls
    )
    assert payload_con.results["EG002"].relations[0].assertion == "uncertain"
    assert eval_con["metrics"]["relations"]["overall"]["assertion"]["tp"] == 2

    # 3) Resumable execution check: running again on completed output performs 0 extra LLM calls
    prev_calls = len(constrained_client.calls)
    resumed, _ = run_kggen.run_experiment(
        dataset_path=dataset_path,
        output_path=tmp_path / "constrained_batch.preds.json",
        config=KGGenConfig(
            mode="constrained",
            granularity="corpus_batch",
            enable_clustering=True,
            with_assertion=True,
        ),
        client=constrained_client,
        workers=1,
    )
    assert isinstance(resumed, BaselineRunPayload)
    assert len(constrained_client.calls) == prev_calls

    # 4) per_chunk open & constrained (with_assertion=False) preserve case mentions & defaults
    open_chunk_client = _MockLLMClient(_open_handler)
    payload_open_pc, _ = run_kggen.run_experiment(
        dataset_path=dataset_path,
        output_path=tmp_path / "open_per_chunk.preds.json",
        config=KGGenConfig(mode="open", granularity="per_chunk", enable_clustering=False),
        client=open_chunk_client,
        workers=1,
    )
    assert isinstance(payload_open_pc, BaselineRunPayload)
    eg1_stages = payload_open_pc.results["EG001"].usage.by_stage
    assert eg1_stages["kggen_stage1_entities"]["http_calls"] == 1
    assert eg1_stages["kggen_stage1_relations"]["http_calls"] == 1
    assert "Avata360" in payload_open_pc.results["EG001"].entities[0].mentions
    assert not any(c["stage"].startswith("kggen_stage3") for c in open_chunk_client.calls)

    con_no_assert_client = _MockLLMClient(_constrained_handler)
    payload_con_pc, eval_con_pc = run_kggen.run_experiment(
        dataset_path=dataset_path,
        output_path=tmp_path / "con_per_chunk.preds.json",
        config=KGGenConfig(
            mode="constrained",
            granularity="per_chunk",
            enable_clustering=False,
            with_assertion=False,
        ),
        eval_output_path=tmp_path / "con_per_chunk.eval.json",
        client=con_no_assert_client,
        workers=1,
    )
    assert isinstance(payload_con_pc, BaselineRunPayload)
    assert eval_con_pc is not None
    assert payload_con_pc.results["EG002"].relations[0].assertion is None
    assert eval_con_pc["metrics"]["structural"]["defaulted_assertions_count"] == 2
    assert eval_con_pc["metrics"]["relations"]["overall"]["triple"]["tp"] == 2
    assert eval_con_pc["metrics"]["relations"]["overall"]["assertion"]["tp"] == 1


def test_kggen_library_backend_missing_raises_and_cli_dry_run(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(importlib.util, "find_spec", lambda name: None)
    with pytest.raises(ImportError, match="kg_gen"):
        extract_case_library(_sample_case(), KGGenConfig(backend="library"))

    rc1 = run_kggen.main(["--mode", "open", "--granularity", "per_chunk", "--dry-run"])
    assert rc1 == 0
    out1 = json.loads(capsys.readouterr().out)
    assert out1["model_called"] is False
    assert out1["cases"] == 20
    assert out1["enable_clustering"] is False

    rc2 = run_kggen.main(["--mode", "constrained", "--granularity", "corpus_batch", "--dry-run"])
    assert rc2 == 0
    out2 = json.loads(capsys.readouterr().out)
    assert out2["model_called"] is False
    assert out2["cases"] == 20
    assert out2["enable_clustering"] is True
