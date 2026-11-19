"""Unified format adapters and grounding/schema diagnostics for external baselines and RAG_drift."""

from __future__ import annotations

import json
import re
import sys
import unicodedata
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(ROOT / "backend") not in sys.path:
    sys.path.insert(0, str(ROOT / "backend"))

from app.schemas.video_graph import ExtractedEntity, ExtractedRelation  # noqa: E402
from app.services.video_graph.semantics import (  # noqa: E402
    DERIVED_PREDICATES,
    ENTITY_TYPE_DEFINITIONS,
    PREDICATE_DEFINITIONS,
    RelationSemanticError,
    validate_relation_semantics,
)

from kg_comparison.schema import (  # noqa: E402
    FORMAT_VERSION,
    BaselineEntity,
    BaselineRelation,
    BaselineRunPayload,
    CasePrediction,
    CaseUsage,
)

VALID_ENTITY_TYPES: frozenset[str] = frozenset(ENTITY_TYPE_DEFINITIONS.keys())
VALID_PREDICATES: frozenset[str] = frozenset(PREDICATE_DEFINITIONS.keys()) - DERIVED_PREDICATES
VALID_ASSERTIONS: frozenset[str] = frozenset({"asserted", "uncertain", "negated"})

_DASH_QUOTE_MAP = str.maketrans(
    {"‐": "-", "‑": "-", "–": "-", "—": "-", "‘": "'", "’": "'", "“": '"', "”": '"'}
)


def normalize_text(value: str) -> str:
    """Identical normalization to scripts/evaluate_entity_gold.py."""
    value = unicodedata.normalize("NFKC", value).casefold()
    value = value.translate(_DASH_QUOTE_MAP)
    return "".join(value.split())


def case_text(case: dict[str, Any]) -> str:
    text = case["text"]
    if isinstance(text, str):
        return text
    if isinstance(text, list) and all(isinstance(line, str) for line in text):
        return "\n".join(text)
    raise ValueError(f"{case.get('id', 'unknown')}: text must be str or list[str]")


def locate_mention_in_text(text: str, mention: str) -> tuple[str, tuple[int, int]] | None:
    """Locate mention in source text with whitespace flexibility.

    Returns ("exact", (start, end)) for case-sensitive match (matching RAG_drift locate_quote),
    ("case_insensitive", (start, end)) when only case differs, or None if ungrounded.
    """
    parts = mention.strip().split()
    if not parts:
        return None
    pattern = r"\s+".join(re.escape(part) for part in parts)
    exact = re.search(pattern, text)
    if exact:
        return ("exact", (exact.start(), exact.end()))
    ci = re.search(pattern, text, flags=re.IGNORECASE)
    if ci:
        return ("case_insensitive", (ci.start(), ci.end()))
    return None


def recover_original_spans(text: str, mention: str, *, max_spans: int = 4) -> list[str]:
    """Find distinct original-case spans matching mention (case-insensitive)."""
    parts = mention.strip().split()
    if not parts:
        return []
    pattern = r"\s+".join(re.escape(part) for part in parts)
    spans: list[str] = []
    for match in re.finditer(pattern, text, flags=re.IGNORECASE):
        span = text[match.start() : match.end()]
        if span not in spans:
            spans.append(span)
            if len(spans) >= max_spans:
                break
    return spans


def _extract_rag_drift_usage(raw_case: dict[str, Any]) -> CaseUsage:
    """Recover provider usage from RAG_drift evaluate_* cache_trace if present."""
    usage = CaseUsage()
    trace = raw_case.get("cache_trace")
    if not isinstance(trace, list):
        return usage
    by_stage: dict[str, dict[str, int]] = {}
    for entry in trace:
        if not isinstance(entry, dict):
            continue
        stage = str(entry.get("stage") or entry.get("phase") or "unknown")
        if entry.get("replayed"):
            usage.replayed_calls += 1
            continue
        stage_usage = entry.get("usage")
        if not isinstance(stage_usage, dict):
            continue
        usage.http_calls += int(
            stage_usage.get("http_responses", stage_usage.get("http_attempts", 0))
        )
        usage.prompt_tokens += int(stage_usage.get("prompt_tokens", 0))
        usage.completion_tokens += int(stage_usage.get("completion_tokens", 0))
        usage.total_tokens += int(stage_usage.get("total_tokens", 0))
        usage.cached_tokens += int(stage_usage.get("cached_tokens", 0))
        usage.reasoning_tokens += int(stage_usage.get("reasoning_tokens", 0))
        usage.unknown_usage_calls += int(stage_usage.get("unknown_usage_responses", 0))
        bucket = by_stage.setdefault(stage, {})
        for k, v in stage_usage.items():
            if isinstance(v, int):
                bucket[k] = bucket.get(k, 0) + v
    usage.by_stage = by_stage
    return usage


def convert_rag_drift_case(raw_case: dict[str, Any]) -> CasePrediction:
    """Convert a single RAG_drift evaluate_entity_gold/evaluate_relation_gold case entry."""
    status = raw_case.get("status", "error")
    seconds = float(raw_case.get("seconds") or 0.0)
    usage = _extract_rag_drift_usage(raw_case)
    if status != "success" or not isinstance(raw_case.get("output"), dict):
        detail = raw_case.get("error_detail") or {}
        return CasePrediction(
            status="error",
            seconds=seconds,
            usage=usage,
            error_type=raw_case.get("error_type"),
            error_message=detail.get("message") or detail.get("code"),
        )
    output = raw_case["output"]
    entities = []
    for ent in output.get("entities", []):
        surface = str(ent.get("surface_text") or "").strip()
        canonical = str(ent.get("canonical_name") or surface).strip()
        if not canonical:
            continue
        mentions = [surface] if surface else [canonical]
        entities.append(
            BaselineEntity(
                key=str(ent.get("key") or canonical),
                name=canonical,
                entity_type=ent.get("entity_type"),
                mentions=mentions,
                surface_text=surface or canonical,
                identity_scope=ent.get("identity_scope"),
                confidence=ent.get("confidence"),
                attributes=ent.get("attributes") or {},
            )
        )
    relations = []
    for rel in output.get("relations", []):
        subj = str(rel.get("subject") or "").strip()
        pred = str(rel.get("predicate") or "").strip()
        obj = str(rel.get("object") or "").strip()
        if not (subj and pred and obj):
            continue
        relations.append(
            BaselineRelation(
                subject=subj,
                predicate=pred,
                object=obj,
                assertion=rel.get("assertion"),
                qualifiers=rel.get("qualifiers") or {},
                quote=str(rel.get("quote") or ""),
                confidence=rel.get("confidence"),
            )
        )
    return CasePrediction(
        status="success",
        entities=entities,
        relations=relations,
        seconds=seconds,
        usage=usage,
        diagnostics={"source_format": "rag_drift_chunk_extraction"},
    )


def load_predictions_payload(path: Path) -> BaselineRunPayload:
    """Load either a kg-comparison-v1 payload or a native RAG_drift gold-evaluation JSON."""
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict) or not isinstance(raw.get("results"), dict):
        raise ValueError(f"{path}: 预测文件必须是包含 results 字典的 JSON 对象")
    if raw.get("format_version") == FORMAT_VERSION:
        return BaselineRunPayload.model_validate(raw)

    # Fallback: detect native RAG_drift evaluate_entity_gold / evaluate_relation_gold output
    results = {
        case_id: convert_rag_drift_case(case_payload)
        for case_id, case_payload in raw["results"].items()
    }
    return BaselineRunPayload(
        format_version=FORMAT_VERSION,
        method="rag_drift",
        method_version=str(raw.get("extraction_version") or "rag_drift_import"),
        mode="native",
        granularity="per_chunk",
        model=None,
        temperature=0.0,
        input_scope=str(raw.get("input_scope") or "title_and_current_chunk_only"),
        dataset_version=raw.get("dataset_version"),
        dataset_sha256=raw.get("dataset_sha256"),
        results=results,
        updated_at=raw.get("updated_at"),
    )


def _check_relation_type_rule(
    predicate: str, subject_type: str | None, object_type: str | None
) -> bool:
    if predicate not in VALID_PREDICATES:
        return False
    if subject_type not in VALID_ENTITY_TYPES or object_type not in VALID_ENTITY_TYPES:
        return False
    try:
        validate_relation_semantics(
            ExtractedRelation(
                subject="s",
                object="o",
                predicate=predicate,  # type: ignore[arg-type]
                assertion="asserted",
                quote="q",
                confidence=1.0,
            ),
            ExtractedEntity(
                key="s", surface_text="s", entity_type=subject_type  # type: ignore[arg-type]
            ),
            ExtractedEntity(
                key="o", surface_text="o", entity_type=object_type  # type: ignore[arg-type]
            ),
        )
        return True
    except (RelationSemanticError, ValueError):
        return False


def adapt_case_prediction(
    case: dict[str, Any], prediction: CasePrediction | None
) -> dict[str, Any]:
    """Adapt one case prediction without QualityReview or raising on ungrounded items.

    Returns a normalized structure containing:
    - entities: deduplicated entity records (matching evaluation grouping) with grounding status
    - ungrounded_entities: subset of entities whose mentions cannot be located in chunk text
    - relations: normalized relation records with defaulted assertion/qualifiers & schema flags
    - diagnostics: counts of defaulted assertions, out-of-schema predicates, dangling endpoints
    """
    text = case_text(case)
    if prediction is None or prediction.status != "success":
        return {
            "status": "pending" if prediction is None else "error",
            "entities": [],
            "ungrounded_entities": [],
            "relations": [],
            "diagnostics": {
                "defaulted_assertions": 0,
                "out_of_schema_predicates": 0,
                "invalid_entity_types": 0,
                "dangling_relation_endpoints": 0,
                "exact_grounded_entities": 0,
                "case_insensitive_grounded_entities": 0,
                "ungrounded_entities_count": 0,
                "grounded_relation_quotes": 0,
                "ungrounded_relation_quotes": 0,
            },
        }

    # Step 1: Group entities by (normalize(name), type_or_empty, identity_scope_or_empty)
    # preserving all surface mentions and keys, mirroring scripts/evaluate_entity_gold.py.
    grouped: dict[tuple[str, str, str], dict[str, Any]] = {}
    key_to_index: dict[str, int] = {}
    name_to_index: dict[str, int] = {}
    mention_to_index: dict[str, int] = {}
    invalid_entity_types = 0

    def _register_entity(
        *,
        key: str,
        name: str,
        raw_type: str | None,
        mentions: list[str],
        identity_scope: str | None = None,
        implicit: bool = False,
    ) -> int:
        nonlocal invalid_entity_types
        cleaned_type = (
            raw_type.strip().lower()
            if isinstance(raw_type, str) and raw_type.strip()
            else None
        )
        schema_type = cleaned_type if cleaned_type in VALID_ENTITY_TYPES else None
        if cleaned_type is not None and schema_type is None:
            invalid_entity_types += 1

        group_key = (normalize_text(name), schema_type or "", identity_scope or "")
        if group_key not in grouped:
            # Evaluate grounding across mentions and canonical name
            candidate_mentions: list[str] = []
            for m in mentions:
                m_clean = m.strip()
                if m_clean and m_clean not in candidate_mentions:
                    candidate_mentions.append(m_clean)
            if not candidate_mentions:
                candidate_mentions = [name]

            grounding = "ungrounded"
            recovered_spans: list[str] = []
            for m in [*candidate_mentions, name]:
                loc = locate_mention_in_text(text, m)
                if loc is not None:
                    kind, (start, end) = loc
                    span = text[start:end]
                    if span not in recovered_spans:
                        recovered_spans.append(span)
                    if kind == "exact":
                        grounding = "exact"
                        break
                    if grounding == "ungrounded":
                        grounding = "case_insensitive"

            for span in recovered_spans:
                if span not in candidate_mentions:
                    candidate_mentions.append(span)

            idx = len(grouped)
            grouped[group_key] = {
                "index": idx,
                "name": name,
                "type": schema_type,
                "raw_type": raw_type,
                "mentions": candidate_mentions,
                "keys": [key],
                "identity_scope": identity_scope,
                "grounding": grounding,
                "implicit_from_relation": implicit,
            }
        else:
            record = grouped[group_key]
            idx = record["index"]
            if key not in record["keys"]:
                record["keys"].append(key)
            for m in mentions:
                m_clean = m.strip()
                if m_clean and m_clean not in record["mentions"]:
                    record["mentions"].append(m_clean)
                    if record["grounding"] != "exact":
                        loc = locate_mention_in_text(text, m_clean)
                        if loc is not None:
                            record["grounding"] = loc[0]

        key_to_index[key] = idx
        name_to_index.setdefault(normalize_text(name), idx)
        for m in grouped[group_key]["mentions"]:
            mention_to_index.setdefault(normalize_text(m), idx)
        return idx

    for entity in prediction.entities:
        raw_mentions = list(entity.mentions)
        if entity.surface_text and entity.surface_text not in raw_mentions:
            raw_mentions.insert(0, entity.surface_text)
        _register_entity(
            key=entity.key,
            name=entity.name.strip(),
            raw_type=entity.entity_type,
            mentions=raw_mentions,
            identity_scope=entity.identity_scope,
            implicit=False,
        )

    # Step 2: Adapt relations, fill default assertion/qualifiers, resolve endpoints
    dangling_endpoints = 0
    defaulted_assertions = 0
    out_of_schema_predicates = 0
    grounded_quotes = 0
    ungrounded_quotes = 0

    def _resolve_endpoint(endpoint_ref: str) -> int:
        nonlocal dangling_endpoints
        if endpoint_ref in key_to_index:
            return key_to_index[endpoint_ref]
        norm = normalize_text(endpoint_ref)
        if norm in name_to_index:
            return name_to_index[norm]
        if norm in mention_to_index:
            return mention_to_index[norm]
        dangling_endpoints += 1
        return _register_entity(
            key=f"implicit:{endpoint_ref}",
            name=endpoint_ref.strip(),
            raw_type=None,
            mentions=[endpoint_ref.strip()],
            implicit=True,
        )

    adapted_relations: list[dict[str, Any]] = []
    for rel in prediction.relations:
        si = _resolve_endpoint(rel.subject)
        oi = _resolve_endpoint(rel.object)
        entities_list = list(grouped.values())
        subj_ent = entities_list[si]
        obj_ent = entities_list[oi]

        raw_assertion = rel.assertion.strip().lower() if isinstance(rel.assertion, str) else ""
        if raw_assertion in VALID_ASSERTIONS:
            assertion = raw_assertion
            assertion_defaulted = False
        else:
            assertion = "asserted"
            assertion_defaulted = True
            defaulted_assertions += 1

        qualifiers = {
            str(k): str(v)
            for k, v in (rel.qualifiers or {}).items()
            if isinstance(k, str) and isinstance(v, str) and k.strip() and v.strip()
        }

        predicate = rel.predicate.strip()
        predicate_norm = predicate.lower().replace(" ", "_")
        canonical_predicate = predicate_norm if predicate_norm in VALID_PREDICATES else predicate
        predicate_in_schema = canonical_predicate in VALID_PREDICATES
        if not predicate_in_schema:
            out_of_schema_predicates += 1

        type_rule_valid = _check_relation_type_rule(
            canonical_predicate, subj_ent["type"], obj_ent["type"]
        )

        quote = (rel.quote or "").strip()
        quote_loc = locate_mention_in_text(text, quote) if quote else None
        quote_grounding = quote_loc[0] if quote_loc is not None else "ungrounded"
        if quote_grounding != "ungrounded":
            grounded_quotes += 1
        else:
            ungrounded_quotes += 1

        adapted_relations.append(
            {
                "subject_index": si,
                "object_index": oi,
                "subject": subj_ent["name"],
                "subject_type": subj_ent["type"],
                "object": obj_ent["name"],
                "object_type": obj_ent["type"],
                "predicate": canonical_predicate,
                "raw_predicate": predicate,
                "predicate_in_schema": predicate_in_schema,
                "type_rule_valid": type_rule_valid,
                "assertion": assertion,
                "assertion_defaulted": assertion_defaulted,
                "qualifiers": qualifiers,
                "quote": quote,
                "quote_grounding": quote_grounding,
                "confidence": rel.confidence,
            }
        )

    all_entities = list(grouped.values())
    ungrounded_entities = [e for e in all_entities if e["grounding"] == "ungrounded"]
    exact_count = sum(1 for e in all_entities if e["grounding"] == "exact")
    ci_count = sum(1 for e in all_entities if e["grounding"] == "case_insensitive")

    return {
        "status": "success",
        "entities": all_entities,
        "ungrounded_entities": ungrounded_entities,
        "relations": adapted_relations,
        "diagnostics": {
            "defaulted_assertions": defaulted_assertions,
            "out_of_schema_predicates": out_of_schema_predicates,
            "invalid_entity_types": invalid_entity_types,
            "dangling_relation_endpoints": dangling_endpoints,
            "exact_grounded_entities": exact_count,
            "case_insensitive_grounded_entities": ci_count,
            "ungrounded_entities_count": len(ungrounded_entities),
            "grounded_relation_quotes": grounded_quotes,
            "ungrounded_relation_quotes": ungrounded_quotes,
        },
    }
