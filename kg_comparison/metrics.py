"""Ontology-agnostic KG quality metrics, token/cost telemetry, and MINE-1 subgraph helpers."""

from __future__ import annotations

import math
import re
from collections import Counter, defaultdict
from collections.abc import Callable
from typing import Any

from kg_comparison.adapter import normalize_text
from kg_comparison.schema import BaselineRunPayload

# Standalone pronouns/stop-entities indicating OpenIE degenerate nodes (KGGen Sec 2 & Table 1b)
DEGENERATE_ENTITIES: frozenset[str] = frozenset(
    {
        "it",
        "this",
        "that",
        "these",
        "those",
        "they",
        "them",
        "he",
        "she",
        "we",
        "you",
        "i",
        "someone",
        "something",
        "anything",
        "everything",
        "nothing",
        "none",
        "n/a",
        "null",
        "它",
        "这",
        "这个",
        "那",
        "那个",
        "他们",
        "它们",
        "大家",
        "东西",
    }
)

_SENTENCE_SPLIT_RE = re.compile(r"[。！？.!?]\s+\S|[。！？]\S")


def is_valid_triple(relation: dict[str, Any]) -> tuple[bool, list[str]]:
    """Evaluate structural triple well-formedness (KGGen Table 1b, ontology-agnostic).

    Checks:
    1. Non-empty subject, predicate, object.
    2. No self-loop (normalized subject != normalized object).
    3. Concise, non-pronoun endpoints (<= 80 chars, no multi-sentence narrative).
    4. Concise predicate (<= 60 chars, <= 8 whitespace words, no sentence boundary punctuation).
    """
    reasons: list[str] = []
    subj = str(relation.get("subject") or "").strip()
    pred = str(relation.get("raw_predicate") or relation.get("predicate") or "").strip()
    obj = str(relation.get("object") or "").strip()

    if not subj or not pred or not obj:
        reasons.append("empty_field")
        return False, reasons

    norm_subj = normalize_text(subj)
    norm_obj = normalize_text(obj)
    if norm_subj == norm_obj:
        reasons.append("self_loop")

    if norm_subj in DEGENERATE_ENTITIES or norm_obj in DEGENERATE_ENTITIES:
        reasons.append("pronoun_or_degenerate_entity")

    if len(subj) > 80 or len(obj) > 80:
        reasons.append("overlong_entity")

    if _SENTENCE_SPLIT_RE.search(subj) or _SENTENCE_SPLIT_RE.search(obj):
        reasons.append("multi_sentence_entity")

    if len(pred) > 60 or len(pred.split()) > 8 or _SENTENCE_SPLIT_RE.search(pred):
        reasons.append("narrative_or_overlong_predicate")

    return len(reasons) == 0, reasons


def _safe_rate(num: int, den: int) -> float | None:
    return num / den if den > 0 else None


def compute_structural_metrics(
    adapted_by_case: dict[str, dict[str, Any]],
    *,
    corpus_stats: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Compute ontology-agnostic graph quality, grounding, and relation reuse metrics."""
    total_entities = 0
    unique_entities: set[str] = set()
    exact_grounded_entities = 0
    ci_grounded_entities = 0
    ungrounded_entities = 0

    total_relations = 0
    valid_triples = 0
    invalid_reasons: Counter[str] = Counter()
    schema_predicate_relations = 0
    schema_type_valid_relations = 0
    grounded_relation_quotes = 0
    defaulted_assertions = 0
    dangling_endpoints = 0

    predicate_counts: Counter[str] = Counter()
    raw_predicate_counts: Counter[str] = Counter()

    for adapted in adapted_by_case.values():
        if adapted.get("status") != "success":
            continue
        diag = adapted["diagnostics"]
        exact_grounded_entities += diag["exact_grounded_entities"]
        ci_grounded_entities += diag["case_insensitive_grounded_entities"]
        ungrounded_entities += diag["ungrounded_entities_count"]
        defaulted_assertions += diag["defaulted_assertions"]
        dangling_endpoints += diag["dangling_relation_endpoints"]
        grounded_relation_quotes += diag["grounded_relation_quotes"]

        for entity in adapted["entities"]:
            total_entities += 1
            unique_entities.add(normalize_text(entity["name"]))

        for rel in adapted["relations"]:
            total_relations += 1
            ok, reasons = is_valid_triple(rel)
            if ok:
                valid_triples += 1
            else:
                for reason in reasons:
                    invalid_reasons[reason] += 1
            if rel.get("predicate_in_schema"):
                schema_predicate_relations += 1
            if rel.get("type_rule_valid"):
                schema_type_valid_relations += 1
            predicate_counts[normalize_text(str(rel["predicate"]))] += 1
            raw_predicate_counts[str(rel.get("raw_predicate") or rel["predicate"])] += 1

    any_grounded_entities = exact_grounded_entities + ci_grounded_entities
    unique_predicates = len(predicate_counts)
    unique_entity_count = len(unique_entities)

    result: dict[str, Any] = {
        "total_entities": total_entities,
        "unique_entities": unique_entity_count,
        "exact_grounded_entities": exact_grounded_entities,
        "case_insensitive_grounded_entities": ci_grounded_entities,
        "ungrounded_entities_count": ungrounded_entities,
        "entity_exact_grounding_rate": _safe_rate(exact_grounded_entities, total_entities),
        "entity_casefold_grounding_rate": _safe_rate(any_grounded_entities, total_entities),
        "total_relations": total_relations,
        "valid_triples": valid_triples,
        "valid_triple_rate": _safe_rate(valid_triples, total_relations),
        "invalid_triple_reasons": dict(sorted(invalid_reasons.items())),
        "schema_predicate_relations": schema_predicate_relations,
        "schema_predicate_rate": _safe_rate(schema_predicate_relations, total_relations),
        "schema_type_valid_relations": schema_type_valid_relations,
        "schema_type_valid_rate": _safe_rate(schema_type_valid_relations, total_relations),
        "relation_quote_grounding_rate": _safe_rate(grounded_relation_quotes, total_relations),
        "defaulted_assertions_count": defaulted_assertions,
        "dangling_relation_endpoints": dangling_endpoints,
        "unique_predicates": unique_predicates,
        "relation_reuse_ratio": (
            total_relations / unique_predicates if unique_predicates > 0 else None
        ),
        "avg_degree": (
            2.0 * total_relations / unique_entity_count if unique_entity_count > 0 else None
        ),
        "top_predicates": dict(raw_predicate_counts.most_common(15)),
    }
    if corpus_stats:
        result["corpus_stats"] = corpus_stats
    return result


def aggregate_usage_metrics(
    payload: BaselineRunPayload,
    *,
    case_ids: list[str],
    valid_triples: int,
) -> dict[str, Any]:
    """Aggregate token usage, HTTP call counts, and latency across cases + corpus stages."""
    prompt_tokens = payload.corpus_usage.prompt_tokens
    completion_tokens = payload.corpus_usage.completion_tokens
    total_tokens = payload.corpus_usage.total_tokens
    cached_tokens = payload.corpus_usage.cached_tokens
    reasoning_tokens = payload.corpus_usage.reasoning_tokens
    http_calls = payload.corpus_usage.http_calls
    replayed_calls = payload.corpus_usage.replayed_calls
    unknown_usage_calls = payload.corpus_usage.unknown_usage_calls
    total_seconds = float(payload.corpus_seconds or 0.0)
    by_stage: dict[str, Counter[str]] = defaultdict(Counter)

    for stage, stats in payload.corpus_usage.by_stage.items():
        by_stage[stage].update(stats)

    measured_cases = 0
    for cid in case_ids:
        pred = payload.results.get(cid)
        if pred is None:
            continue
        measured_cases += 1
        total_seconds += float(pred.seconds or 0.0)
        u = pred.usage
        prompt_tokens += u.prompt_tokens
        completion_tokens += u.completion_tokens
        total_tokens += u.total_tokens
        cached_tokens += u.cached_tokens
        reasoning_tokens += u.reasoning_tokens
        http_calls += u.http_calls
        replayed_calls += u.replayed_calls
        unknown_usage_calls += u.unknown_usage_calls
        for stage, stats in u.by_stage.items():
            by_stage[stage].update(stats)

    return {
        "measured_cases": measured_cases,
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "total_tokens": total_tokens,
        "cached_tokens": cached_tokens,
        "reasoning_tokens": reasoning_tokens,
        "http_calls": http_calls,
        "replayed_calls": replayed_calls,
        "unknown_usage_calls": unknown_usage_calls,
        "total_seconds": round(total_seconds, 3),
        "avg_seconds_per_case": (
            round(total_seconds / measured_cases, 3) if measured_cases > 0 else None
        ),
        "tokens_per_case": (
            round(total_tokens / measured_cases, 1) if measured_cases > 0 else None
        ),
        "tokens_per_valid_triple": (
            round(total_tokens / valid_triples, 1)
            if valid_triples > 0 and total_tokens > 0
            else None
        ),
        "by_stage": {stage: dict(counter) for stage, counter in sorted(by_stage.items())},
    }


def _tokenize_for_bm25(text: str) -> list[str]:
    """Bilingual tokenizer: English words/numbers + CJK character bigrams/unigrams."""
    text_norm = text.casefold()
    tokens = re.findall(r"[a-z0-9]+", text_norm)
    cjk_runs = re.findall(r"[\u4e00-\u9fff]+", text_norm)
    for run in cjk_runs:
        tokens.extend(list(run))
        if len(run) >= 2:
            tokens.extend(run[i : i + 2] for i in range(len(run) - 1))
    return tokens


def mine1_retrieve_two_hop_subgraph(
    triples: list[tuple[str, str, str]],
    fact_query: str,
    *,
    top_k_nodes: int = 10,
) -> list[tuple[str, str, str]]:
    """Retrieve a MINE-1 style 2-hop subgraph around top-k matching nodes (BM25 fallback).

    Designed for ontology-agnostic fact retention benchmarking across KG baselines.
    """
    if not triples:
        return []
    nodes = sorted({s for s, _, _ in triples} | {o for _, _, o in triples})
    node_tokens = [_tokenize_for_bm25(node) for node in nodes]
    query_tokens = _tokenize_for_bm25(fact_query)
    if not query_tokens:
        return []

    n_docs = len(nodes)
    avgdl = sum(len(t) for t in node_tokens) / max(n_docs, 1)
    df: Counter[str] = Counter()
    for toks in node_tokens:
        for tok in set(toks):
            df[tok] += 1

    scores: list[tuple[float, str]] = []
    k1, b = 1.5, 0.75
    for node, toks in zip(nodes, node_tokens, strict=True):
        tf = Counter(toks)
        dl = len(toks)
        score = 0.0
        for q in query_tokens:
            if q not in tf:
                continue
            idf = math.log((n_docs - df[q] + 0.5) / (df[q] + 0.5) + 1.0)
            freq = tf[q]
            score += idf * (freq * (k1 + 1)) / (freq + k1 * (1 - b + b * dl / max(avgdl, 1e-6)))
        if score > 0:
            scores.append((score, node))

    scores.sort(key=lambda item: (-item[0], item[1]))
    seed_nodes = {node for _, node in scores[:top_k_nodes]}
    if not seed_nodes:
        return []

    # 1-hop expansion
    hop1_nodes = set(seed_nodes)
    for s, _, o in triples:
        if s in seed_nodes or o in seed_nodes:
            hop1_nodes.add(s)
            hop1_nodes.add(o)

    # 2-hop subgraph edges
    return [t for t in triples if t[0] in hop1_nodes or t[2] in hop1_nodes]


def evaluate_mine1_facts(
    triples: list[tuple[str, str, str]],
    facts: list[str],
    judge_fn: Callable[[str, list[tuple[str, str, str]]], bool],
    *,
    top_k_nodes: int = 10,
) -> dict[str, Any]:
    """Evaluate MINE-1 fact retention score using a caller-supplied judge (LLM or deterministic)."""
    details = []
    supported = 0
    for fact in facts:
        subgraph = mine1_retrieve_two_hop_subgraph(triples, fact, top_k_nodes=top_k_nodes)
        ok = bool(judge_fn(fact, subgraph)) if subgraph else False
        if ok:
            supported += 1
        details.append({"fact": fact, "subgraph_edges": len(subgraph), "supported": ok})
    return {
        "total_facts": len(facts),
        "supported_facts": supported,
        "mine1_score": _safe_rate(supported, len(facts)),
        "details": details,
    }
