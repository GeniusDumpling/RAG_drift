"""Microsoft GraphRAG Extraction + Gleanings + Merge adapter (community detection disabled)."""

from __future__ import annotations

import importlib.util
import json
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(ROOT / "backend") not in sys.path:
    sys.path.insert(0, str(ROOT / "backend"))

from app.services.video_graph.semantics import (  # noqa: E402
    DERIVED_PREDICATES,
    ENTITY_TYPE_DEFINITIONS,
    PREDICATE_DEFINITIONS,
)

from kg_comparison.adapter import (  # noqa: E402
    VALID_ASSERTIONS,
    VALID_ENTITY_TYPES,
    VALID_PREDICATES,
    case_text,
    locate_mention_in_text,
    recover_original_spans,
)
from kg_comparison.schema import (  # noqa: E402
    BaselineEntity,
    BaselineRelation,
    CasePrediction,
    CaseUsage,
)

GRAPHRAG_DEFAULT_ENTITY_TYPES = ("organization", "person", "geo", "event")


class GraphRAGConfig(BaseModel):
    """Configuration for GraphRAG-Open, GraphRAG-Open-Mapped, and GraphRAG-Constrained."""

    model_config = ConfigDict(extra="forbid")

    mode: Literal["open", "open_mapped", "constrained"] = "open"
    granularity: Literal["per_chunk", "corpus_batch"] = "per_chunk"
    backend: Literal["native", "library"] = "native"
    max_gleanings: int = Field(default=1, ge=0, le=3)
    summarize_descriptions: bool = False
    with_assertion: bool = False
    request_quote: bool = False
    input_scope: Literal["title_and_current_chunk_only", "chunk_only"] = (
        "title_and_current_chunk_only"
    )

    @property
    def effective_request_quote(self) -> bool:
        return self.request_quote or (
            self.mode in {"constrained", "open_mapped"} and self.with_assertion
        )


def normalize_graphrag_key(value: str) -> str:
    """Whitespace-collapsed, case-folded entity/edge key for GraphRAG Element Merge."""
    return " ".join(value.strip().split()).casefold()


def format_case_input(case: dict[str, Any], input_scope: str) -> str:
    """Build exact chunk input aligned with RAG_drift without GraphRAG's TextUnit chunker."""
    chunk_text = case_text(case)
    if input_scope == "title_and_current_chunk_only":
        title = str((case.get("source") or {}).get("title") or "").strip()
        if title:
            return f"Title: {title}\n\n{chunk_text}"
    return chunk_text


def _choose_preferred_display(variants: Counter[str]) -> str:
    def _sort_key(item: tuple[str, int]) -> tuple[int, int, int, str]:
        text, count = item
        has_upper = 1 if any(ch.isupper() for ch in text) else 0
        return (-count, -has_upper, -len(text), text)

    return min(variants.items(), key=_sort_key)[0]


def _extract_mode(config: GraphRAGConfig) -> Literal["open", "constrained"]:
    return "open" if config.mode in {"open", "open_mapped"} else "constrained"


def _extraction_prompt(config: GraphRAGConfig, *, is_gleaning: bool = False) -> str:
    mode = _extract_mode(config)
    glean_header = (
        "MANY entities or relationships were missed in the previous extraction pass.\n"
        "Inspect the source text and the list of already_extracted items, then output ONLY "
        "additional missed entities and relationships using the exact same JSON schema. "
        'If nothing was missed, return {"entities": [], "relations": []}.\n\n'
        if is_gleaning
        else ""
    )
    if mode == "open":
        default_types = ", ".join(GRAPHRAG_DEFAULT_ENTITY_TYPES)
        return (
            f"{glean_header}"
            "You are implementing Microsoft GraphRAG joint entity and relationship extraction.\n"
            "Given a technical UAV/drone text document, identify all significant entities and "
            "all clearly related pairs of entities.\n"
            "Entity rules:\n"
            f"- Default GraphRAG entity types: [{default_types}] (you may use a concise "
            "domain type like product/component/software/technology/fault if clearly more apt).\n"
            "- Preserve exact original casing from the text and include verbatim spans in "
            '"mentions".\n'
            "- Provide a concise 'description' of the entity based on the text.\n"
            "Relationship rules:\n"
            "- Each relation connects a 'source' entity and 'target' entity from the entities.\n"
            "- Provide a natural-language narrative 'description' explaining why source and "
            "target are related, plus an integer 'strength' (1 to 10).\n"
            "Output ONLY a JSON object with format:\n"
            '{"entities": [{"name": "...", "entity_type": "...", "description": "...", '
            '"mentions": ["exact span"]}], '
            '"relations": [{"source": "...", "target": "...", '
            '"description": "narrative relationship description", "strength": 8}]}'
        )

    type_lines = "\n".join(
        f"- {etype}: {desc}" for etype, desc in ENTITY_TYPE_DEFINITIONS.items()
    )
    pred_lines = "\n".join(
        f"- {pred}: {desc}"
        for pred, desc in PREDICATE_DEFINITIONS.items()
        if pred not in DERIVED_PREDICATES
    )
    rel_fields = (
        '"source": "...", "target": "...", "predicate": "<allowed predicate>", '
        '"description": "...", "strength": 8'
    )
    extra_rules = ""
    if config.with_assertion:
        extra_rules += (
            "- For each relation, classify 'assertion' as 'asserted', 'uncertain', or "
            "'negated', and include verbatim 'quote' from the text.\n"
        )
        rel_fields += ', "assertion": "asserted|uncertain|negated", "quote": "..."'
    elif config.effective_request_quote:
        extra_rules += "- Include verbatim 'quote' from the text for each relation.\n"
        rel_fields += ', "quote": "..."'

    return (
        f"{glean_header}"
        "You are implementing schema-constrained GraphRAG joint entity and relationship "
        "extraction.\n"
        f"Allowed entity_type values:\n{type_lines}\n\n"
        f"Allowed predicate values:\n{pred_lines}\n\n"
        "Rules:\n"
        "- Preserve exact original casing from the text and include verbatim spans in "
        '"mentions".\n'
        "- Only emit relations using the exact allowed predicates above and entities from "
        "your extracted list.\n"
        "- Include a concise 'description' and integer 'strength' (1 to 10) per relation.\n"
        f"{extra_rules}"
        "Output ONLY a JSON object with format:\n"
        '{"entities": [{"name": "...", "entity_type": "<allowed type>", '
        '"description": "...", "mentions": ["exact span"]}], '
        f'"relations": [{{{rel_fields}}}]}}'
    )


def _post_map_prompt(config: GraphRAGConfig) -> str:
    type_lines = "\n".join(
        f"- {etype}: {desc}" for etype, desc in ENTITY_TYPE_DEFINITIONS.items()
    )
    pred_lines = "\n".join(
        f"- {pred}: {desc}"
        for pred, desc in PREDICATE_DEFINITIONS.items()
        if pred not in DERIVED_PREDICATES
    )
    assert_rule = ""
    extra_fields = ""
    if config.with_assertion:
        assert_rule = (
            "5. Classify 'assertion' as 'asserted', 'uncertain', or 'negated' and include "
            "verbatim 'quote' from the text.\n"
        )
        extra_fields = ', "assertion": "asserted|uncertain|negated", "quote": "..."'
    elif config.effective_request_quote:
        assert_rule = "5. Include verbatim 'quote' from the text.\n"
        extra_fields = ', "quote": "..."'

    return (
        "You are performing post-hoc ontology mapping from open Microsoft GraphRAG output "
        "into the closed RAG_drift UAV ontology.\n"
        f"Allowed entity_type values:\n{type_lines}\n\n"
        f"Allowed predicate values:\n{pred_lines}\n\n"
        "Rules:\n"
        "1. Map each extracted entity name to one allowed entity_type (or null if none fits).\n"
        "2. Map each indexed relation to one allowed predicate (or null if none fits).\n"
        "3. Specify 'direction': 'forward' (source -> predicate -> target) or 'reverse' "
        "(target -> predicate -> source) if the narrative description expresses the inverse.\n"
        "4. Do NOT invent new entities or relations outside the provided lists.\n"
        f"{assert_rule}"
        "Output ONLY a JSON object:\n"
        '{"entity_types": {"<entity_name>": "<allowed_type_or_null>"}, '
        '"mapped_relations": [{"index": 0, "predicate": "<allowed_predicate_or_null>", '
        f'"direction": "forward|reverse"{extra_fields}}}]}}'
    )


def _summarize_prompt(kind: Literal["entity", "relation"]) -> str:
    return (
        f"You are performing Microsoft GraphRAG Element Merge {kind} description summarization.\n"
        "Synthesize the list of descriptions collected across multiple text units into ONE "
        "concise, coherent, factual summary sentence without inventing any new claims.\n"
        'Output ONLY JSON: {"summary": "..."}'
    )


def _preserve_mentions(name: str, raw_mentions: list[str], chunk_text: str) -> list[str]:
    preserved: list[str] = []
    has_exact = False
    for m in raw_mentions:
        loc = locate_mention_in_text(chunk_text, m)
        if loc is not None and loc[0] == "exact":
            span = chunk_text[loc[1][0] : loc[1][1]]
            if span not in preserved:
                preserved.append(span)
            has_exact = True
        else:
            if m not in preserved:
                preserved.append(m)
            for recovered in recover_original_spans(chunk_text, m):
                if recovered not in preserved:
                    preserved.append(recovered)
                    has_exact = True
    if not has_exact:
        for recovered in recover_original_spans(chunk_text, name):
            if recovered not in preserved:
                preserved.append(recovered)
    return preserved or [name]


def _parse_strength(value: Any) -> int:
    try:
        num = int(round(float(value)))
        return min(10, max(1, num))
    except (TypeError, ValueError):
        return 5


def _merge_extraction_pass(
    raw: dict[str, Any],
    *,
    chunk_text: str,
    case_id: str,
    chunk_id: str,
    config: GraphRAGConfig,
    entities_by_key: dict[str, dict[str, Any]],
    relations_by_sig: dict[tuple[str, str, str], dict[str, Any]],
) -> tuple[int, int]:
    """Merge one GraphRAG extraction or gleaning response into case accumulators."""
    mode = _extract_mode(config)
    added_entities = 0
    added_relations = 0

    raw_entities = raw.get("entities")
    if isinstance(raw_entities, list):
        for item in raw_entities:
            if isinstance(item, str):
                name = item.strip()
                raw_type = None
                desc = ""
                raw_mentions: list[str] = []
            elif isinstance(item, dict):
                name = str(
                    item.get("name") or item.get("entity") or item.get("surface_text") or ""
                ).strip()
                rt = item.get("entity_type") or item.get("type")
                raw_type = str(rt).strip().lower() if isinstance(rt, str) and rt.strip() else None
                desc = str(item.get("description") or "").strip()
                rm = item.get("mentions")
                raw_mentions = (
                    [str(m).strip() for m in rm if isinstance(m, str) and m.strip()]
                    if isinstance(rm, list)
                    else []
                )
            else:
                continue

            if not name:
                continue
            ekey = normalize_graphrag_key(name)
            mentions = _preserve_mentions(name, raw_mentions, chunk_text)
            if ekey not in entities_by_key:
                entities_by_key[ekey] = {
                    "name": name,
                    "entity_type": raw_type,
                    "descriptions": [desc] if desc else [],
                    "mentions": mentions,
                    "text_unit_ids": [case_id],
                    "chunk_ids": [chunk_id] if chunk_id else [],
                }
                added_entities += 1
            else:
                rec = entities_by_key[ekey]
                for m in mentions:
                    if m not in rec["mentions"]:
                        rec["mentions"].append(m)
                if desc and desc not in rec["descriptions"]:
                    rec["descriptions"].append(desc)
                if rec["entity_type"] is None and raw_type is not None:
                    rec["entity_type"] = raw_type

    raw_relations = raw.get("relations")
    if isinstance(raw_relations, list):
        for item in raw_relations:
            if not isinstance(item, dict):
                continue
            src = str(item.get("source") or item.get("subject") or "").strip()
            tgt = str(item.get("target") or item.get("object") or "").strip()
            desc = str(item.get("description") or "").strip()
            raw_pred = str(item.get("predicate") or item.get("relation") or "").strip()
            strength = _parse_strength(item.get("strength"))

            if not src or not tgt:
                continue

            if mode == "open":
                pred_text = desc or raw_pred or "related_to"
                assertion = None
                quote = (
                    str(item.get("quote") or "").strip() if config.effective_request_quote else ""
                )
            else:
                pred_norm = raw_pred.lower().replace(" ", "_")
                pred_text = pred_norm if pred_norm in VALID_PREDICATES else (raw_pred or desc)
                if not pred_text:
                    continue
                raw_assert = item.get("assertion")
                assertion = (
                    str(raw_assert).strip().lower()
                    if config.with_assertion
                    and isinstance(raw_assert, str)
                    and raw_assert.strip().lower() in VALID_ASSERTIONS
                    else None
                )
                quote = (
                    str(item.get("quote") or "").strip() if config.effective_request_quote else ""
                )

            src_key = normalize_graphrag_key(src)
            tgt_key = normalize_graphrag_key(tgt)
            src_name = entities_by_key[src_key]["name"] if src_key in entities_by_key else src
            tgt_name = entities_by_key[tgt_key]["name"] if tgt_key in entities_by_key else tgt
            sig = (src_key, normalize_graphrag_key(pred_text), tgt_key)

            if sig not in relations_by_sig:
                relations_by_sig[sig] = {
                    "subject": src_name,
                    "predicate": pred_text,
                    "object": tgt_name,
                    "description": desc or pred_text,
                    "strength": strength,
                    "assertion": assertion,
                    "quote": quote,
                    "text_unit_ids": [case_id],
                }
                added_relations += 1

    return added_entities, added_relations


def _build_case_prediction(
    *,
    entities_by_key: dict[str, dict[str, Any]],
    relations_list: list[dict[str, Any]],
    elapsed: float,
    usage_acc: CaseUsage,
    diagnostics: dict[str, Any],
) -> CasePrediction:
    entities: list[BaselineEntity] = []
    for rec in entities_by_key.values():
        attrs: dict[str, str] = {"text_unit_ids": ",".join(rec["text_unit_ids"])}
        if rec.get("chunk_ids"):
            attrs["chunk_ids"] = ",".join(rec["chunk_ids"])
        if rec.get("descriptions"):
            attrs["description"] = " | ".join(rec["descriptions"])[:500]
        if rec.get("graphrag_raw_type"):
            attrs["graphrag_raw_type"] = str(rec["graphrag_raw_type"])[:80]

        mentions = rec["mentions"] or [rec["name"]]
        entities.append(
            BaselineEntity(
                key=rec["name"],
                name=rec["name"],
                entity_type=rec["entity_type"],
                mentions=mentions,
                surface_text=mentions[0],
                attributes=attrs,
            )
        )

    relations: list[BaselineRelation] = []
    for rel in relations_list:
        strength = int(rel.get("strength") or 5)
        relations.append(
            BaselineRelation(
                subject=rel["subject"],
                predicate=rel["predicate"],
                object=rel["object"],
                assertion=rel.get("assertion"),
                qualifiers={},
                quote=rel.get("quote") or "",
                confidence=round(min(1.0, max(0.0, strength / 10.0)), 3),
                description=rel.get("description") or "",
                strength=strength,
                text_unit_ids=list(rel.get("text_unit_ids") or []),
            )
        )

    return CasePrediction(
        status="success",
        entities=entities,
        relations=relations,
        seconds=elapsed,
        usage=usage_acc,
        diagnostics=diagnostics,
    )


def post_map_case(
    *,
    case_id: str,
    formatted_input: str,
    entities_by_key: dict[str, dict[str, Any]],
    relations_list: list[dict[str, Any]],
    client: Any,
    config: GraphRAGConfig,
    usage_acc: CaseUsage,
) -> tuple[list[dict[str, Any]], int, int]:
    """Post-hoc map open GraphRAG entities & narrative edges into closed RAG_drift ontology."""
    if not entities_by_key and not relations_list:
        return [], 0, 0

    ent_payload = [
        {
            "name": rec["name"],
            "raw_type": rec["entity_type"],
            "description": " | ".join(rec["descriptions"])[:300],
        }
        for rec in entities_by_key.values()
    ]
    rel_payload = [
        {
            "index": idx,
            "source": rel["subject"],
            "target": rel["object"],
            "description": rel["description"] or rel["predicate"],
        }
        for idx, rel in enumerate(relations_list)
    ]
    user_payload = json.dumps(
        {"text": formatted_input, "entities": ent_payload, "relations": rel_payload},
        ensure_ascii=False,
    )
    raw = client.request_json(
        system=_post_map_prompt(config),
        user=user_payload,
        stage="graphrag_post_map",
        namespace=f"graphrag:open_mapped:{case_id}:post_map",
        usage_acc=usage_acc,
    )

    # Update entity types while preserving raw GraphRAG type in attributes
    raw_ent_types = raw.get("entity_types")
    if isinstance(raw_ent_types, dict):
        norm_type_map = {
            normalize_graphrag_key(str(k)): v for k, v in raw_ent_types.items() if str(k).strip()
        }
        for ekey, rec in entities_by_key.items():
            if rec["entity_type"] is not None:
                rec["graphrag_raw_type"] = rec["entity_type"]
            mapped_t = norm_type_map.get(ekey)
            if isinstance(mapped_t, str) and mapped_t.strip().lower() in VALID_ENTITY_TYPES:
                rec["entity_type"] = mapped_t.strip().lower()
            elif ekey in norm_type_map:
                rec["entity_type"] = None

    mapped_relations: list[dict[str, Any]] = []
    dropped = 0
    seen_indices: set[int] = set()
    raw_mapped_rels = raw.get("mapped_relations")
    if isinstance(raw_mapped_rels, list):
        for item in raw_mapped_rels:
            if not isinstance(item, dict):
                continue
            idx = item.get("index")
            if not isinstance(idx, int) or not (0 <= idx < len(relations_list)):
                continue
            if idx in seen_indices:
                continue
            seen_indices.add(idx)
            orig = relations_list[idx]

            raw_pred = item.get("predicate")
            if not isinstance(raw_pred, str) or not raw_pred.strip():
                dropped += 1
                continue
            pred_norm = raw_pred.strip().lower().replace(" ", "_")
            if pred_norm not in VALID_PREDICATES:
                dropped += 1
                continue

            direction = str(item.get("direction") or "forward").strip().lower()
            subj = orig["object"] if direction == "reverse" else orig["subject"]
            obj = orig["subject"] if direction == "reverse" else orig["object"]

            raw_assert = item.get("assertion")
            assertion = (
                str(raw_assert).strip().lower()
                if config.with_assertion
                and isinstance(raw_assert, str)
                and raw_assert.strip().lower() in VALID_ASSERTIONS
                else None
            )
            quote = (
                str(item.get("quote") or orig.get("quote") or "").strip()
                if config.effective_request_quote
                else ""
            )

            mapped_relations.append(
                {
                    **orig,
                    "subject": subj,
                    "predicate": pred_norm,
                    "object": obj,
                    "assertion": assertion,
                    "quote": quote,
                }
            )

    dropped += max(0, len(relations_list) - len(seen_indices))
    return mapped_relations, len(mapped_relations), dropped


def extract_case_native(
    case: dict[str, Any], client: Any, config: GraphRAGConfig
) -> CasePrediction:
    """Run GraphRAG joint extraction + gleaning loop (plus optional post-map) on one chunk."""
    started = time.perf_counter()
    usage_acc = CaseUsage()
    chunk_text = case_text(case)
    formatted_input = format_case_input(case, config.input_scope)
    case_id = str(case["id"])
    chunk_id = str((case.get("source") or {}).get("chunk_id") or "")
    extract_mode = _extract_mode(config)

    entities_by_key: dict[str, dict[str, Any]] = {}
    relations_by_sig: dict[tuple[str, str, str], dict[str, Any]] = {}

    raw_extract = client.request_json(
        system=_extraction_prompt(config, is_gleaning=False),
        user=formatted_input,
        stage="graphrag_extract",
        namespace=f"graphrag:{extract_mode}:{case_id}:extract",
        usage_acc=usage_acc,
    )
    _merge_extraction_pass(
        raw_extract,
        chunk_text=chunk_text,
        case_id=case_id,
        chunk_id=chunk_id,
        config=config,
        entities_by_key=entities_by_key,
        relations_by_sig=relations_by_sig,
    )

    gleanings_executed = 0
    for g in range(config.max_gleanings):
        glean_user = json.dumps(
            {
                "text": formatted_input,
                "already_extracted": {
                    "entities": [rec["name"] for rec in entities_by_key.values()],
                    "relations": [
                        {
                            "source": rel["subject"],
                            "predicate": rel["predicate"],
                            "target": rel["object"],
                        }
                        for rel in relations_by_sig.values()
                    ],
                },
            },
            ensure_ascii=False,
        )
        raw_glean = client.request_json(
            system=_extraction_prompt(config, is_gleaning=True),
            user=glean_user,
            stage="graphrag_glean",
            namespace=f"graphrag:{extract_mode}:{case_id}:glean{g}",
            usage_acc=usage_acc,
        )
        gleanings_executed += 1
        add_e, add_r = _merge_extraction_pass(
            raw_glean,
            chunk_text=chunk_text,
            case_id=case_id,
            chunk_id=chunk_id,
            config=config,
            entities_by_key=entities_by_key,
            relations_by_sig=relations_by_sig,
        )
        if add_e == 0 and add_r == 0:
            break

    relations_list = list(relations_by_sig.values())
    diagnostics: dict[str, Any] = {
        "graphrag_backend": "native",
        "graphrag_mode": config.mode,
        "community_detection_enabled": False,
        "gleanings_executed": gleanings_executed,
    }

    if config.mode == "open_mapped":
        relations_list, mapped_cnt, dropped_cnt = post_map_case(
            case_id=case_id,
            formatted_input=formatted_input,
            entities_by_key=entities_by_key,
            relations_list=relations_list,
            client=client,
            config=config,
            usage_acc=usage_acc,
        )
        diagnostics["mapped_relations_count"] = mapped_cnt
        diagnostics["dropped_unmapped_relations"] = dropped_cnt

    elapsed = round(time.perf_counter() - started, 4)
    return _build_case_prediction(
        entities_by_key=entities_by_key,
        relations_list=relations_list,
        elapsed=elapsed,
        usage_acc=usage_acc,
        diagnostics=diagnostics,
    )


def merge_graphrag_corpus(
    cases_by_id: dict[str, dict[str, Any]],
    results: dict[str, CasePrediction],
    client: Any,
    config: GraphRAGConfig,
    usage_acc: CaseUsage,
) -> tuple[dict[str, CasePrediction], dict[str, Any]]:
    """GraphRAG Element Merge across TextUnits (community detection strictly disabled)."""
    case_order = [cid for cid in cases_by_id if cid in results]
    for cid in results:
        if cid not in cases_by_id:
            case_order.append(cid)

    entity_variants: dict[str, Counter[str]] = defaultdict(Counter)
    entity_type_votes: dict[str, Counter[str]] = defaultdict(Counter)
    entity_descriptions: dict[str, list[str]] = defaultdict(list)
    entity_text_units: dict[str, list[str]] = defaultdict(list)
    entity_chunk_ids: dict[str, list[str]] = defaultdict(list)
    surface_mentions_by_case: dict[str, dict[str, list[str]]] = defaultdict(
        lambda: defaultdict(list)
    )

    edge_buckets: dict[tuple[str, ...], dict[str, Any]] = {}
    pre_merge_entities = 0
    pre_merge_relations = 0
    succeeded_units = 0

    for cid in case_order:
        pred = results[cid]
        if pred.status != "success":
            continue
        succeeded_units += 1
        case = cases_by_id.get(cid, {})
        chunk_text = case_text(case) if case else ""
        chunk_id = str((case.get("source") or {}).get("chunk_id") or "")

        local_to_key: dict[str, str] = {}
        for ent in pred.entities:
            pre_merge_entities += 1
            ekey = normalize_graphrag_key(ent.name)
            local_to_key[ent.key] = ekey
            local_to_key.setdefault(ent.name, ekey)
            entity_variants[ekey][ent.name] += 1
            if ent.entity_type:
                entity_type_votes[ekey][ent.entity_type] += 1
            if cid not in entity_text_units[ekey]:
                entity_text_units[ekey].append(cid)
            if chunk_id and chunk_id not in entity_chunk_ids[ekey]:
                entity_chunk_ids[ekey].append(chunk_id)
            desc = (ent.attributes or {}).get("description", "").strip()
            if desc and desc not in entity_descriptions[ekey]:
                entity_descriptions[ekey].append(desc)

            raw_mentions = list(ent.mentions)
            if ent.surface_text and ent.surface_text not in raw_mentions:
                raw_mentions.insert(0, ent.surface_text)
            if chunk_text:
                for span in recover_original_spans(chunk_text, ent.name):
                    if span not in raw_mentions:
                        raw_mentions.append(span)
            bucket = surface_mentions_by_case[cid][ekey]
            for m in raw_mentions or [ent.name]:
                m_clean = m.strip()
                if m_clean and m_clean not in bucket:
                    bucket.append(m_clean)

        for rel in pred.relations:
            pre_merge_relations += 1
            skey = local_to_key.get(rel.subject) or normalize_graphrag_key(rel.subject)
            okey = local_to_key.get(rel.object) or normalize_graphrag_key(rel.object)
            if config.mode == "open":
                edge_id: tuple[str, ...] = (skey, okey)
            else:
                edge_id = (skey, normalize_graphrag_key(rel.predicate), okey)

            raw_desc = str(getattr(rel, "description", "") or rel.predicate).strip()
            raw_strength = getattr(rel, "strength", None)
            strength = (
                int(raw_strength)
                if isinstance(raw_strength, int) and raw_strength > 0
                else _parse_strength((rel.confidence or 0.5) * 10)
            )
            if edge_id not in edge_buckets:
                edge_buckets[edge_id] = {
                    "subject_key": skey,
                    "object_key": okey,
                    "predicate": rel.predicate,
                    "descriptions": [raw_desc] if raw_desc else [],
                    "strength_sum": strength,
                    "text_unit_ids": [cid],
                }
            else:
                eb = edge_buckets[edge_id]
                if raw_desc and raw_desc not in eb["descriptions"]:
                    eb["descriptions"].append(raw_desc)
                eb["strength_sum"] += strength
                if cid not in eb["text_unit_ids"]:
                    eb["text_unit_ids"].append(cid)

    entity_displays = {k: _choose_preferred_display(v) for k, v in entity_variants.items()}
    entity_types = {
        k: (entity_type_votes[k].most_common(1)[0][0] if entity_type_votes.get(k) else None)
        for k in entity_variants
    }

    # Optional GraphRAG description summarization across multi-TextUnit elements
    summarized_ent_desc: dict[str, str] = {}
    summarized_edge_desc: dict[tuple[str, ...], str] = {}
    if config.summarize_descriptions and client is not None:
        for idx, (ekey, descs) in enumerate(sorted(entity_descriptions.items())):
            if len(entity_text_units[ekey]) >= 2 and len(descs) >= 2:
                raw_sum = client.request_json(
                    system=_summarize_prompt("entity"),
                    user=json.dumps(
                        {"name": entity_displays[ekey], "descriptions": descs},
                        ensure_ascii=False,
                    ),
                    stage="graphrag_summarize_descriptions",
                    namespace=f"graphrag:{config.mode}:summarize:ent:{idx}",
                    usage_acc=usage_acc,
                )
                summary = str(raw_sum.get("summary") or "").strip()
                if summary:
                    summarized_ent_desc[ekey] = summary[:500]

        for idx, (edge_id, eb) in enumerate(sorted(edge_buckets.items())):
            if len(eb["text_unit_ids"]) >= 2 and len(eb["descriptions"]) >= 2:
                raw_sum = client.request_json(
                    system=_summarize_prompt("relation"),
                    user=json.dumps(
                        {
                            "source": entity_displays.get(eb["subject_key"], eb["subject_key"]),
                            "target": entity_displays.get(eb["object_key"], eb["object_key"]),
                            "descriptions": eb["descriptions"],
                        },
                        ensure_ascii=False,
                    ),
                    stage="graphrag_summarize_descriptions",
                    namespace=f"graphrag:{config.mode}:summarize:rel:{idx}",
                    usage_acc=usage_acc,
                )
                summary = str(raw_sum.get("summary") or "").strip()
                if summary:
                    summarized_edge_desc[edge_id] = summary[:500]

    # Project canonical display names, majority types, and text_unit_ids back per case
    updated_results: dict[str, CasePrediction] = {}
    for cid in case_order:
        orig = results[cid]
        if orig.status != "success":
            updated_results[cid] = orig
            continue

        merged_case_entities: dict[str, BaselineEntity] = {}
        local_to_key: dict[str, str] = {}
        for ent in orig.entities:
            ekey = normalize_graphrag_key(ent.name)
            local_to_key[ent.key] = ekey
            local_to_key.setdefault(ent.name, ekey)
            canon_name = entity_displays[ekey]
            mentions = list(surface_mentions_by_case[cid].get(ekey) or ent.mentions or [canon_name])

            if ekey not in merged_case_entities:
                attrs = dict(ent.attributes or {})
                attrs["text_unit_ids"] = ",".join(entity_text_units[ekey])
                if entity_chunk_ids.get(ekey):
                    attrs["chunk_ids"] = ",".join(entity_chunk_ids[ekey])
                if ekey in summarized_ent_desc:
                    attrs["description"] = summarized_ent_desc[ekey]
                merged_case_entities[ekey] = BaselineEntity(
                    key=canon_name,
                    name=canon_name,
                    entity_type=entity_types.get(ekey),
                    mentions=mentions,
                    surface_text=mentions[0],
                    confidence=ent.confidence,
                    attributes=attrs,
                )

        updated_rels: list[BaselineRelation] = []
        for rel in orig.relations:
            skey = local_to_key.get(rel.subject) or normalize_graphrag_key(rel.subject)
            okey = local_to_key.get(rel.object) or normalize_graphrag_key(rel.object)
            edge_id = (
                (skey, okey)
                if config.mode == "open"
                else (skey, normalize_graphrag_key(rel.predicate), okey)
            )
            eb = edge_buckets.get(edge_id, {})
            subj_canon = entity_displays.get(skey, rel.subject)
            obj_canon = entity_displays.get(okey, rel.object)
            rel_desc = summarized_edge_desc.get(edge_id) or str(
                getattr(rel, "description", "") or ""
            )
            updated_rels.append(
                BaselineRelation(
                    subject=subj_canon,
                    predicate=rel.predicate,
                    object=obj_canon,
                    assertion=rel.assertion,
                    qualifiers=dict(rel.qualifiers),
                    quote=rel.quote,
                    confidence=rel.confidence,
                    description=rel_desc,
                    strength=getattr(rel, "strength", 5),
                    text_unit_ids=list(eb.get("text_unit_ids") or [cid]),
                )
            )

        updated_results[cid] = CasePrediction(
            status="success",
            entities=list(merged_case_entities.values()),
            relations=updated_rels,
            seconds=orig.seconds,
            usage=orig.usage,
            diagnostics={**orig.diagnostics, "graphrag_merged": True},
        )

    post_merge_entities = len(entity_displays)
    post_merge_relations = len(edge_buckets)
    merged_entity_text_units = {
        entity_displays[k]: units
        for k, units in sorted(entity_text_units.items(), key=lambda kv: entity_displays[kv[0]])
        if len(units) >= 2
    }
    avg_units = (
        round(sum(len(u) for u in entity_text_units.values()) / post_merge_entities, 4)
        if post_merge_entities
        else 0.0
    )
    avg_strength = (
        round(
            sum(eb["strength_sum"] for eb in edge_buckets.values()) / post_merge_relations, 3
        )
        if post_merge_relations
        else 0.0
    )

    corpus_stats: dict[str, Any] = {
        "community_detection_enabled": False,
        "total_text_units": succeeded_units,
        "pre_merge_entities": pre_merge_entities,
        "post_merge_entities": post_merge_entities,
        "entity_merge_ratio": (
            round((pre_merge_entities - post_merge_entities) / pre_merge_entities, 4)
            if pre_merge_entities
            else 0.0
        ),
        "pre_merge_relations": pre_merge_relations,
        "post_merge_relations": post_merge_relations,
        "multi_unit_entities": len(merged_entity_text_units),
        "avg_text_units_per_entity": avg_units,
        "avg_edge_strength": avg_strength,
        "merged_entity_text_units": merged_entity_text_units,
    }
    return updated_results, corpus_stats


def extract_case_library(case: dict[str, Any], config: GraphRAGConfig) -> CasePrediction:
    """Optional official `graphrag` package adapter (disabled unless installed)."""
    if importlib.util.find_spec("graphrag") is None:
        raise ImportError(
            "未安装官方 `graphrag` 包。请先安装 `graphrag`，或使用默认 `--backend native`。"
        )
    raise NotImplementedError(
        "已检测到 `graphrag` 包，但受控单切片对比建议使用 `--backend native` "
        "以保证与 RAG_drift 相同的请求格式和 Token 遥测口径。"
    )


class GraphRAGAdapter:
    """High-level GraphRAG orchestrator supporting per-chunk and corpus-batch evaluation."""

    def __init__(self, config: GraphRAGConfig, client: Any = None) -> None:
        self.config = config
        self.client = client

    def extract_case(self, case: dict[str, Any]) -> CasePrediction:
        if self.config.backend == "library":
            return extract_case_library(case, self.config)
        if self.client is None:
            raise ValueError("GraphRAG native 模式需要提供 ComparisonLLMClient 实例")
        return extract_case_native(case, self.client, self.config)

    def merge_corpus(
        self,
        cases_by_id: dict[str, dict[str, Any]],
        results: dict[str, CasePrediction],
        usage_acc: CaseUsage,
    ) -> tuple[dict[str, CasePrediction], dict[str, Any]]:
        return merge_graphrag_corpus(cases_by_id, results, self.client, self.config, usage_acc)
