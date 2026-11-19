"""Faithful KGGen 3-stage KG extraction & clustering engine adapted for RAG_drift comparison."""

from __future__ import annotations

import importlib
import importlib.util
import json
import math
import sys
import time
from collections import Counter, defaultdict
from dataclasses import dataclass, field
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
from kg_comparison.metrics import _tokenize_for_bm25  # noqa: E402
from kg_comparison.schema import (  # noqa: E402
    BaselineEntity,
    BaselineRelation,
    CasePrediction,
    CaseUsage,
)


class KGGenConfig(BaseModel):
    """Configuration for KGGen-Open and KGGen-Constrained experimental variants."""

    model_config = ConfigDict(extra="forbid")

    mode: Literal["open", "constrained"] = "open"
    granularity: Literal["per_chunk", "corpus_batch"] = "per_chunk"
    backend: Literal["native", "library"] = "native"
    enable_clustering: bool = False
    with_assertion: bool = False
    request_quote: bool = False
    input_scope: Literal["title_and_current_chunk_only", "chunk_only"] = (
        "title_and_current_chunk_only"
    )
    cluster_size: int = Field(default=128, ge=2, le=512)
    retrieval_top_k: int = Field(default=16, ge=2, le=64)
    max_unclustered_passes: int = Field(default=2, ge=0, le=5)
    embedding_backend: Literal["tfidf", "sentence_transformers"] = "tfidf"
    sentence_transformer_model: str = "all-MiniLM-L6-v2"

    @property
    def effective_request_quote(self) -> bool:
        return self.request_quote or (self.mode == "constrained" and self.with_assertion)


@dataclass
class AggregatedGraph:
    """Intermediate Stage-2 corpus graph preserving per-case surface mentions."""

    case_order: list[str]
    entity_displays: dict[str, str]
    entity_variants: dict[str, Counter[str]]
    entity_types: dict[str, str | None]
    surface_mentions_by_case: dict[str, dict[str, list[str]]]
    predicate_displays: dict[str, str]
    predicate_variants: dict[str, Counter[str]]
    case_entities: dict[str, list[dict[str, Any]]]
    case_relations: dict[str, list[dict[str, Any]]]
    pre_cluster_entities: int
    pre_cluster_predicates: int
    pre_cluster_relations: int
    passthrough_cases: dict[str, CasePrediction] = field(default_factory=dict)


def normalize_kggen_key(value: str) -> str:
    """Case-folded, whitespace-collapsed key used by KGGen Stage 2 aggregation."""
    return " ".join(value.strip().split()).casefold()


def format_case_input(case: dict[str, Any], input_scope: str) -> str:
    """Build exact chunk input matching RAG_drift without invoking external chunkers."""
    chunk_text = case_text(case)
    if input_scope == "title_and_current_chunk_only":
        title = str((case.get("source") or {}).get("title") or "").strip()
        if title:
            return f"Title: {title}\n\n{chunk_text}"
    return chunk_text


def _choose_preferred_display(variants: Counter[str]) -> str:
    """Pick the most frequent display form, breaking ties toward mixed-case / longer forms."""

    def _sort_key(item: tuple[str, int]) -> tuple[int, int, int, str]:
        text, count = item
        has_upper = 1 if any(ch.isupper() for ch in text) else 0
        return (-count, -has_upper, -len(text), text)

    return min(variants.items(), key=_sort_key)[0]


def _stage1_entity_prompt(config: KGGenConfig) -> str:
    if config.mode == "open":
        return (
            "You are a knowledge graph entity extractor (KGGen Stage 1a: TextEntities).\n"
            "Extract key entities from the source text. Extracted entities are subjects or "
            "objects of factual relations.\n"
            "Rules:\n"
            "1. Preserve the EXACT original casing and spelling from the text whenever possible.\n"
            "2. Include all exact surface mentions found in the text body for each entity.\n"
            "3. Keep entity names concise (avoid full sentences or pronouns like 'it').\n"
            "4. Output ONLY a JSON object with format:\n"
            '{"entities": [{"name": "canonical or main surface form", '
            '"mentions": ["exact span in text", ...]}]}'
        )
    type_lines = "\n".join(
        f"- {etype}: {desc}" for etype, desc in ENTITY_TYPE_DEFINITIONS.items()
    )
    return (
        "You are a schema-constrained knowledge graph entity extractor "
        "(KGGen-Constrained Stage 1a).\n"
        "Extract key entities from the source text and assign ONE allowed entity_type.\n"
        f"Allowed entity_type values:\n{type_lines}\n\n"
        "Rules:\n"
        "1. Preserve original casing from the text and include exact verbatim spans in mentions.\n"
        "2. Do not fabricate entities absent from the text.\n"
        "3. Output ONLY a JSON object with format:\n"
        '{"entities": [{"name": "entity name", "entity_type": "<allowed type>", '
        '"mentions": ["exact span in text", ...]}]}'
    )


def _stage1_relation_prompt(config: KGGenConfig) -> str:
    quote_hint = (
        'Include "quote": "<exact verbatim substring from source text>" for each relation.\n'
        if config.effective_request_quote
        else ""
    )
    if config.mode == "open":
        fields = '"subject": "...", "predicate": "...", "object": "..."'
        if config.effective_request_quote:
            fields += ', "quote": "..."'
        return (
            "You are a knowledge graph relation extractor "
            "(KGGen Stage 1b: ExtractRelations).\n"
            "Extract subject-predicate-object triples from the source text.\n"
            "Rules:\n"
            "1. Both subject and object MUST match entity names from the provided entity list.\n"
            "2. Subject and object MUST be distinct (no self-loops).\n"
            "3. Predicate MUST be a concise 1 to 4 word relation phrase (e.g. 'uses', "
            "'contains', 'compatible with', 'causes'), NEVER a full descriptive sentence.\n"
            f"{quote_hint}"
            "4. Output ONLY a JSON object with format:\n"
            f'{{"relations": [{{{fields}}}]}}'
        )

    pred_lines = "\n".join(
        f"- {pred}: {desc}"
        for pred, desc in PREDICATE_DEFINITIONS.items()
        if pred not in DERIVED_PREDICATES
    )
    extra_rules = ""
    fields = '"subject": "...", "predicate": "<allowed predicate>", "object": "..."'
    if config.with_assertion:
        extra_rules = (
            "4. Classify assertion as 'asserted' (explicitly stated), "
            "'uncertain' (speculative/may/likely), or 'negated' (explicitly denied/absent).\n"
        )
        fields += ', "assertion": "asserted|uncertain|negated", "quote": "..."'
    elif config.effective_request_quote:
        fields += ', "quote": "..."'

    return (
        "You are a schema-constrained knowledge graph relation extractor "
        "(KGGen-Constrained Stage 1b).\n"
        "Extract subject-predicate-object triples from the source text using ONLY the provided "
        "entities and allowed predicates.\n"
        f"Allowed predicate values:\n{pred_lines}\n\n"
        "Rules:\n"
        "1. Subject and object MUST come from the provided entities list and be distinct.\n"
        "2. Predicate MUST be one of the exact allowed predicate strings above.\n"
        "3. Omit any relation without clear evidence in the text.\n"
        f"{extra_rules}{quote_hint}"
        "Output ONLY a JSON object with format:\n"
        f'{{"relations": [{{{fields}}}]}}'
    )


def _stage3_cluster_prompt(kind: Literal["entities", "predicates"], mode: str) -> str:
    if kind == "entities":
        return (
            "You are performing KGGen Stage 3 entity canonicalization.\n"
            "Given a candidate list of entity names from a technical UAV/drone corpus, "
            "group ONLY strictly semantically equivalent duplicates (such as casing variants, "
            "spacing variants, singular/plural, tense/stemming, or clear abbreviations/aliases "
            "referring to the exact same referent).\n"
            "CRITICAL ANTI-OVERMERGE RULES:\n"
            "- NEVER merge distinct drone models or generations (e.g. 'DJI Mini 4' vs "
            "'DJI Mini 4 Pro', 'Air 3' vs 'Air 3S', 'Mavic 3' vs 'Mavic 3 Pro').\n"
            "- NEVER merge distinct components, firmware versions, or different medical/tech "
            "concepts (e.g. 'Type 1 diabetes' vs 'Type 2 diabetes', 'MRI' vs 'CT scan').\n"
            "- Only output groups with at least 2 equivalent items.\n"
            "Output ONLY a JSON object:\n"
            '{"clusters": [{"canonical": "best representative from list", '
            '"aliases": ["duplicate1", "duplicate2", ...]}]}'
        )
    scope_note = (
        "Only merge strictly synonymous open relation phrases (e.g. 'has' / 'have' / 'possesses')."
        if mode == "open"
        else "Only merge strictly identical predicate meanings."
    )
    return (
        "You are performing KGGen Stage 3 predicate/edge canonicalization.\n"
        f"{scope_note}\n"
        "Never merge opposite, directional-flipped, or meaningfully distinct predicates.\n"
        "Only output groups with at least 2 equivalent items.\n"
        "Output ONLY a JSON object:\n"
        '{"clusters": [{"canonical": "best representative predicate", '
        '"aliases": ["duplicate1", ...]}]}'
    )


def _parse_stage1_entities(
    raw: dict[str, Any], chunk_text: str, config: KGGenConfig
) -> list[BaselineEntity]:
    raw_list = raw.get("entities")
    if not isinstance(raw_list, list):
        return []

    dedup: dict[tuple[str, str], dict[str, Any]] = {}
    for item in raw_list:
        if isinstance(item, str):
            name = item.strip()
            raw_type = None
            raw_mentions: list[str] = []
        elif isinstance(item, dict):
            name = str(
                item.get("name") or item.get("surface_text") or item.get("entity") or ""
            ).strip()
            raw_type_val = item.get("entity_type") or item.get("type")
            raw_type = (
                str(raw_type_val).strip().lower()
                if isinstance(raw_type_val, str) and raw_type_val.strip()
                else None
            )
            raw_m = item.get("mentions")
            raw_mentions = (
                [str(m).strip() for m in raw_m if isinstance(m, str) and m.strip()]
                if isinstance(raw_m, list)
                else []
            )
        else:
            continue

        if not name:
            continue
        if config.mode == "open":
            raw_type = None
        elif raw_type not in VALID_ENTITY_TYPES:
            # Retain non-schema raw type if emitted so adapter/diagnostics can observe it,
            # unless empty.
            pass

        # Preserve original-case spans from chunk_text without fabricating ungrounded spans.
        preserved_mentions: list[str] = []
        has_exact_span = False
        for m in raw_mentions:
            loc = locate_mention_in_text(chunk_text, m)
            if loc is not None and loc[0] == "exact":
                span = chunk_text[loc[1][0] : loc[1][1]]
                if span not in preserved_mentions:
                    preserved_mentions.append(span)
                has_exact_span = True
            else:
                if m not in preserved_mentions:
                    preserved_mentions.append(m)
                for recovered in recover_original_spans(chunk_text, m):
                    if recovered not in preserved_mentions:
                        preserved_mentions.append(recovered)
                        has_exact_span = True

        if not has_exact_span:
            for recovered in recover_original_spans(chunk_text, name):
                if recovered not in preserved_mentions:
                    preserved_mentions.append(recovered)
                    has_exact_span = True

        if not preserved_mentions:
            preserved_mentions = [name]

        # If name lost casing compared to an exact recovered span, keep name as-is or
        # let canonical stay name while mentions preserves exact text case.
        dedup_key = (normalize_kggen_key(name), raw_type or "")
        if dedup_key not in dedup:
            dedup[dedup_key] = {
                "name": name,
                "entity_type": raw_type,
                "mentions": preserved_mentions,
            }
        else:
            existing = dedup[dedup_key]
            for m in preserved_mentions:
                if m not in existing["mentions"]:
                    existing["mentions"].append(m)

    # Assign unique keys (use entity name directly unless two distinct types collide).
    name_counts = Counter(rec["name"] for rec in dedup.values())
    entities: list[BaselineEntity] = []
    used_keys: set[str] = set()
    for rec in dedup.values():
        key = rec["name"]
        if name_counts[key] > 1 and rec["entity_type"]:
            key = f"{rec['name']} [{rec['entity_type']}]"
        while key in used_keys:
            key = f"{key}_{len(used_keys)}"
        used_keys.add(key)
        surface = rec["mentions"][0] if rec["mentions"] else rec["name"]
        entities.append(
            BaselineEntity(
                key=key,
                name=rec["name"],
                entity_type=rec["entity_type"],
                mentions=rec["mentions"],
                surface_text=surface,
            )
        )
    return entities


def _parse_stage1_relations(
    raw: dict[str, Any], entities: list[BaselineEntity], config: KGGenConfig
) -> list[BaselineRelation]:
    raw_list = raw.get("relations")
    if not isinstance(raw_list, list):
        return []

    by_exact_name: dict[str, str] = {}
    by_norm_name: dict[str, str] = {}
    by_mention: dict[str, str] = {}
    for ent in entities:
        by_exact_name.setdefault(ent.name, ent.key)
        by_norm_name.setdefault(normalize_kggen_key(ent.name), ent.key)
        for m in ent.mentions:
            by_mention.setdefault(normalize_kggen_key(m), ent.key)

    def _resolve(ref: str) -> str:
        if ref in by_exact_name:
            return by_exact_name[ref]
        norm = normalize_kggen_key(ref)
        if norm in by_norm_name:
            return by_norm_name[norm]
        if norm in by_mention:
            return by_mention[norm]
        return ref

    relations: list[BaselineRelation] = []
    for item in raw_list:
        if isinstance(item, list | tuple) and len(item) >= 3:
            subj = str(item[0]).strip()
            pred = str(item[1]).strip()
            obj = str(item[2]).strip()
            assertion = None
            quote = ""
        elif isinstance(item, dict):
            subj = str(item.get("subject") or item.get("head") or "").strip()
            pred = str(item.get("predicate") or item.get("relation") or "").strip()
            obj = str(item.get("object") or item.get("tail") or "").strip()
            raw_assertion = item.get("assertion")
            assertion = (
                str(raw_assertion).strip().lower()
                if config.mode == "constrained"
                and config.with_assertion
                and isinstance(raw_assertion, str)
                and raw_assertion.strip().lower() in VALID_ASSERTIONS
                else None
            )
            quote = (
                str(item.get("quote") or "").strip() if config.effective_request_quote else ""
            )
        else:
            continue

        if not (subj and pred and obj):
            continue

        if config.mode == "constrained":
            pred_norm = pred.lower().replace(" ", "_")
            if pred_norm in VALID_PREDICATES:
                pred = pred_norm

        relations.append(
            BaselineRelation(
                subject=_resolve(subj),
                predicate=pred,
                object=_resolve(obj),
                assertion=assertion,
                qualifiers={},
                quote=quote,
            )
        )
    return relations


def extract_case_native(
    case: dict[str, Any], client: Any, config: KGGenConfig
) -> CasePrediction:
    """Run KGGen Stage 1 (two-step entities -> relations) for a single chunk/case."""
    started = time.perf_counter()
    usage_acc = CaseUsage()
    chunk_text = case_text(case)
    formatted_input = format_case_input(case, config.input_scope)
    case_id = str(case["id"])

    # Stage 1a: Entities
    ent_raw = client.request_json(
        system=_stage1_entity_prompt(config),
        user=formatted_input,
        stage="kggen_stage1_entities",
        namespace=f"kggen:{config.mode}:{case_id}:stage1a",
        usage_acc=usage_acc,
    )
    entities = _parse_stage1_entities(ent_raw, chunk_text, config)

    # Stage 1b: Relations (skipped if < 2 entities)
    skipped_stage1b = len(entities) < 2
    relations: list[BaselineRelation] = []
    if not skipped_stage1b:
        entity_payload = [
            (
                {"name": e.name, "entity_type": e.entity_type}
                if config.mode == "constrained" and e.entity_type
                else {"name": e.name}
            )
            for e in entities
        ]
        rel_user = json.dumps(
            {"text": formatted_input, "entities": entity_payload},
            ensure_ascii=False,
        )
        rel_raw = client.request_json(
            system=_stage1_relation_prompt(config),
            user=rel_user,
            stage="kggen_stage1_relations",
            namespace=f"kggen:{config.mode}:{case_id}:stage1b",
            usage_acc=usage_acc,
        )
        relations = _parse_stage1_relations(rel_raw, entities, config)

    elapsed = round(time.perf_counter() - started, 4)
    return CasePrediction(
        status="success",
        entities=entities,
        relations=relations,
        seconds=elapsed,
        usage=usage_acc,
        diagnostics={
            "kggen_backend": "native",
            "kggen_mode": config.mode,
            "skipped_stage1b": skipped_stage1b,
        },
    )


def aggregate_case_predictions(
    results: dict[str, CasePrediction],
    cases_by_id: dict[str, dict[str, Any]],
) -> AggregatedGraph:
    """Stage 2: Aggregate entities and predicates across cases without erasing original casing."""
    case_order = [cid for cid in cases_by_id if cid in results]
    for cid in results:
        if cid not in cases_by_id:
            case_order.append(cid)

    entity_variants: dict[str, Counter[str]] = defaultdict(Counter)
    entity_type_votes: dict[str, Counter[str]] = defaultdict(Counter)
    surface_mentions_by_case: dict[str, dict[str, list[str]]] = defaultdict(
        lambda: defaultdict(list)
    )
    predicate_variants: dict[str, Counter[str]] = defaultdict(Counter)
    case_entities: dict[str, list[dict[str, Any]]] = defaultdict(list)
    case_relations: dict[str, list[dict[str, Any]]] = defaultdict(list)
    passthrough_cases: dict[str, CasePrediction] = {}
    pre_cluster_relations = 0

    for cid in case_order:
        pred = results[cid]
        if pred.status != "success":
            passthrough_cases[cid] = pred
            continue

        chunk_text = case_text(cases_by_id[cid]) if cid in cases_by_id else ""
        local_key_to_agg: dict[str, str] = {}
        local_seen_entities: dict[str, dict[str, Any]] = {}

        for ent in pred.entities:
            agg_key = normalize_kggen_key(ent.name)
            local_key_to_agg[ent.key] = agg_key
            local_key_to_agg.setdefault(ent.name, agg_key)
            entity_variants[agg_key][ent.name] += 1
            if ent.entity_type:
                entity_type_votes[agg_key][ent.entity_type] += 1

            raw_mentions = list(ent.mentions)
            if ent.surface_text and ent.surface_text not in raw_mentions:
                raw_mentions.insert(0, ent.surface_text)
            if not raw_mentions:
                raw_mentions = [ent.name]
            if chunk_text:
                for span in recover_original_spans(chunk_text, ent.name):
                    if span not in raw_mentions:
                        raw_mentions.append(span)

            case_bucket = surface_mentions_by_case[cid][agg_key]
            for m in raw_mentions:
                m_clean = m.strip()
                if m_clean and m_clean not in case_bucket:
                    case_bucket.append(m_clean)

            if agg_key not in local_seen_entities:
                rec = {
                    "agg_key": agg_key,
                    "entity_type": ent.entity_type,
                    "confidence": ent.confidence,
                    "attributes": dict(ent.attributes),
                }
                local_seen_entities[agg_key] = rec
                case_entities[cid].append(rec)

        for rel in pred.relations:
            subj_key = local_key_to_agg.get(rel.subject) or normalize_kggen_key(rel.subject)
            obj_key = local_key_to_agg.get(rel.object) or normalize_kggen_key(rel.object)
            pred_key = normalize_kggen_key(rel.predicate)
            predicate_variants[pred_key][rel.predicate] += 1

            # Ensure implicit endpoints also retain surface mention tracking.
            if subj_key not in entity_variants:
                entity_variants[subj_key][rel.subject] += 1
                surface_mentions_by_case[cid][subj_key].append(rel.subject)
            if obj_key not in entity_variants:
                entity_variants[obj_key][rel.object] += 1
                surface_mentions_by_case[cid][obj_key].append(rel.object)

            case_relations[cid].append(
                {
                    "subject_key": subj_key,
                    "predicate_key": pred_key,
                    "object_key": obj_key,
                    "assertion": rel.assertion,
                    "qualifiers": dict(rel.qualifiers),
                    "quote": rel.quote,
                    "confidence": rel.confidence,
                }
            )
            pre_cluster_relations += 1

    entity_displays = {
        key: _choose_preferred_display(variants) for key, variants in entity_variants.items()
    }
    entity_types = {
        key: (entity_type_votes[key].most_common(1)[0][0] if entity_type_votes.get(key) else None)
        for key in entity_variants
    }
    predicate_displays = {
        key: _choose_preferred_display(variants) for key, variants in predicate_variants.items()
    }

    return AggregatedGraph(
        case_order=case_order,
        entity_displays=entity_displays,
        entity_variants=dict(entity_variants),
        entity_types=entity_types,
        surface_mentions_by_case={k: dict(v) for k, v in surface_mentions_by_case.items()},
        predicate_displays=predicate_displays,
        predicate_variants=dict(predicate_variants),
        case_entities=dict(case_entities),
        case_relations=dict(case_relations),
        pre_cluster_entities=len(entity_displays),
        pre_cluster_predicates=len(predicate_displays),
        pre_cluster_relations=pre_cluster_relations,
        passthrough_cases=passthrough_cases,
    )


def _pure_python_char_vectors(items: list[str]) -> list[dict[str, float]]:
    vectors: list[dict[str, float]] = []
    for item in items:
        padded = f" {item.casefold()} "
        counts: Counter[str] = Counter()
        for n in (2, 3):
            for idx in range(max(0, len(padded) - n + 1)):
                counts[padded[idx : idx + n]] += 1
        norm = math.sqrt(sum(v * v for v in counts.values())) or 1.0
        vectors.append({k: v / norm for k, v in counts.items()})
    return vectors


def _cosine_sparse(a: dict[str, float], b: dict[str, float]) -> float:
    if len(a) > len(b):
        a, b = b, a
    return sum(val * b[k] for k, val in a.items() if k in b)


def _embed_items(
    items: list[str],
    *,
    embedding_backend: Literal["tfidf", "sentence_transformers"],
    sentence_transformer_model: str,
) -> tuple[str, Any]:
    if not items:
        return ("empty", [])
    if embedding_backend == "sentence_transformers":
        try:
            st_mod = importlib.import_module("sentence_transformers")
        except ImportError as exc:
            raise ImportError(
                "embedding_backend='sentence_transformers' 需要安装 sentence-transformers；"
                "离线对比实验请使用默认 --embedding-backend tfidf。"
            ) from exc
        model = st_mod.SentenceTransformer(sentence_transformer_model)
        vecs = model.encode(items, normalize_embeddings=True, show_progress_bar=False)
        return ("dense", vecs)

    try:
        from sklearn.feature_extraction.text import TfidfVectorizer

        vectorizer = TfidfVectorizer(analyzer="char_wb", ngram_range=(2, 4), lowercase=True)
        matrix = vectorizer.fit_transform(items)
        return ("dense", matrix.toarray())
    except Exception:
        return ("sparse", _pure_python_char_vectors(items))


def _partition_clusters(
    items: list[str],
    vec_kind: str,
    vectors: Any,
    *,
    cluster_size: int,
) -> list[list[int]]:
    if len(items) <= cluster_size:
        return [list(range(len(items)))]
    n_clusters = max(1, math.ceil(len(items) / cluster_size))
    if vec_kind == "dense":
        try:
            from sklearn.cluster import KMeans

            labels = KMeans(n_clusters=n_clusters, random_state=0, n_init="auto").fit_predict(
                vectors
            )
            buckets: dict[int, list[int]] = defaultdict(list)
            for idx, label in enumerate(labels):
                buckets[int(label)].append(idx)
            return [buckets[k] for k in sorted(buckets)]
        except Exception:
            pass
    return [
        list(range(i, min(i + cluster_size, len(items))))
        for i in range(0, len(items), cluster_size)
    ]


def _minmax_norm(values: list[float]) -> list[float]:
    if not values:
        return []
    lo, hi = min(values), max(values)
    if hi - lo < 1e-12:
        return [1.0 if hi > 0 else 0.0 for _ in values]
    return [(v - lo) / (hi - lo) for v in values]


def _hybrid_rank(
    anchor_idx: int,
    candidate_indices: list[int],
    items: list[str],
    vec_kind: str,
    vectors: Any,
    *,
    top_k: int,
) -> list[int]:
    """Rank candidates via 50% BM25 + 50% vector cosine similarity (KGGen Section 4.3)."""
    if len(candidate_indices) <= top_k:
        ordered = [anchor_idx] + [i for i in candidate_indices if i != anchor_idx]
        return ordered

    # BM25 lexical similarity over candidate pool
    cand_tokens = [_tokenize_for_bm25(items[i]) for i in candidate_indices]
    query_tokens = _tokenize_for_bm25(items[anchor_idx])
    n_docs = len(candidate_indices)
    avgdl = sum(len(t) for t in cand_tokens) / max(n_docs, 1)
    df: Counter[str] = Counter()
    for toks in cand_tokens:
        for tok in set(toks):
            df[tok] += 1

    bm25_raw: list[float] = []
    k1, b = 1.5, 0.75
    for toks in cand_tokens:
        tf = Counter(toks)
        dl = len(toks)
        score = 0.0
        for q in query_tokens:
            if q not in tf:
                continue
            idf = math.log((n_docs - df[q] + 0.5) / (df[q] + 0.5) + 1.0)
            freq = tf[q]
            score += idf * (freq * (k1 + 1)) / (freq + k1 * (1 - b + b * dl / max(avgdl, 1e-6)))
        bm25_raw.append(score)

    # Vector cosine similarity
    cos_raw: list[float] = []
    if vec_kind == "dense":
        anchor_vec = vectors[anchor_idx]
        for idx in candidate_indices:
            cand_vec = vectors[idx]
            dot = float(sum(float(x) * float(y) for x, y in zip(anchor_vec, cand_vec, strict=True)))
            cos_raw.append(dot)
    elif vec_kind == "sparse":
        anchor_vec = vectors[anchor_idx]
        for idx in candidate_indices:
            cos_raw.append(_cosine_sparse(anchor_vec, vectors[idx]))
    else:
        cos_raw = [0.0 for _ in candidate_indices]

    bm25_norm = _minmax_norm(bm25_raw)
    cos_norm = _minmax_norm(cos_raw)

    scored: list[tuple[float, str, int]] = []
    for pos, idx in enumerate(candidate_indices):
        if idx == anchor_idx:
            continue
        combined = 0.5 * bm25_norm[pos] + 0.5 * cos_norm[pos]
        scored.append((combined, items[idx], idx))
    scored.sort(key=lambda row: (-row[0], row[1], row[2]))
    return [anchor_idx] + [idx for _, _, idx in scored[: max(0, top_k - 1)]]


def _apply_batch_clusters(
    raw_response: dict[str, Any],
    batch_items: list[str],
    parent: dict[str, str],
) -> set[str]:
    """Validate LLM cluster merges against batch_items and update disjoint-set canonical map."""
    clusters = raw_response.get("clusters")
    if not isinstance(clusters, list):
        return set()

    norm_to_batch: dict[str, str] = {normalize_kggen_key(x): x for x in batch_items}
    exact_batch = set(batch_items)
    claimed_in_batch: set[str] = set()
    merged_items: set[str] = set()

    def _match_batch_item(val: Any) -> str | None:
        if not isinstance(val, str) or not val.strip():
            return None
        s = val.strip()
        if s in exact_batch:
            return s
        return norm_to_batch.get(normalize_kggen_key(s))

    def _find(x: str) -> str:
        while parent.get(x, x) != x:
            parent[x] = parent.get(parent[x], parent[x])
            x = parent[x]
        return x

    for cluster in clusters:
        if not isinstance(cluster, dict):
            continue
        raw_aliases = cluster.get("aliases")
        if not isinstance(raw_aliases, list):
            continue

        members: list[str] = []
        raw_canon = cluster.get("canonical")
        canon = _match_batch_item(raw_canon)
        if canon and canon not in claimed_in_batch:
            members.append(canon)
        for alias_raw in raw_aliases:
            matched = _match_batch_item(alias_raw)
            if matched and matched not in claimed_in_batch and matched not in members:
                members.append(matched)

        if len(members) < 2:
            continue

        if canon in members:
            chosen_canon = canon
        elif (
            canon is None
            and isinstance(raw_canon, str)
            and 0 < len(raw_canon.strip()) <= 80
            and "\n" not in raw_canon
        ):
            chosen_canon = raw_canon.strip()
            parent.setdefault(chosen_canon, chosen_canon)
        else:
            chosen_canon = members[0]

        canon_root = _find(chosen_canon)
        for member in members:
            claimed_in_batch.add(member)
            merged_items.add(member)
            if member == canon_root:
                continue
            member_root = _find(member)
            if member_root != canon_root:
                parent[member_root] = canon_root

    return merged_items


def _disambiguate_item_set(
    *,
    items: list[str],
    kind: Literal["entities", "predicates"],
    client: Any,
    config: KGGenConfig,
    usage_acc: CaseUsage,
) -> tuple[dict[str, str], dict[str, str]]:
    """Run KGGen Stage 3 clustering + hybrid retrieval + LLM disambiguation on items."""
    if len(items) < 2:
        return ({x: x for x in items}, {})

    sorted_items = sorted(items, key=lambda s: (s.casefold(), s))
    vec_kind, vectors = _embed_items(
        sorted_items,
        embedding_backend=config.embedding_backend,
        sentence_transformer_model=config.sentence_transformer_model,
    )
    clusters = _partition_clusters(
        sorted_items, vec_kind, vectors, cluster_size=config.cluster_size
    )
    idx_by_item = {item: idx for idx, item in enumerate(sorted_items)}
    parent: dict[str, str] = {item: item for item in sorted_items}
    stage_name = (
        "kggen_stage3_cluster_entities" if kind == "entities" else "kggen_stage3_cluster_predicates"
    )
    system_prompt = _stage3_cluster_prompt(kind, config.mode)

    unclustered: list[str] = []
    step_counter = 0

    for cluster_idx, cluster_indices in enumerate(clusters):
        remaining = list(cluster_indices)
        if len(remaining) < 2:
            unclustered.extend(sorted_items[i] for i in remaining)
            continue

        while len(remaining) >= 2:
            anchor_idx = remaining[0]
            batch_indices = _hybrid_rank(
                anchor_idx,
                remaining,
                sorted_items,
                vec_kind,
                vectors,
                top_k=config.retrieval_top_k,
            )
            batch_items = [sorted_items[i] for i in batch_indices]
            step_counter += 1
            raw = client.request_json(
                system=system_prompt,
                user=json.dumps({kind: batch_items}, ensure_ascii=False),
                stage=stage_name,
                namespace=f"kggen:{config.mode}:stage3:{kind}:c{cluster_idx}:s{step_counter}",
                usage_acc=usage_acc,
            )
            merged_in_batch = _apply_batch_clusters(raw, batch_items, parent)
            # If the entire remaining cluster fit inside one batch, inspect all items at once.
            if len(remaining) <= config.retrieval_top_k:
                for idx in remaining:
                    item = sorted_items[idx]
                    if item not in merged_in_batch:
                        unclustered.append(item)
                remaining = []
            else:
                if merged_in_batch:
                    merged_indices = {idx_by_item[m] for m in merged_in_batch}
                    if anchor_idx not in merged_indices:
                        unclustered.append(sorted_items[anchor_idx])
                        merged_indices.add(anchor_idx)
                    remaining = [i for i in remaining if i not in merged_indices]
                else:
                    unclustered.append(sorted_items[anchor_idx])
                    remaining = remaining[1:]

        if len(remaining) == 1:
            unclustered.append(sorted_items[remaining[0]])

    # Cross-cluster passes on unclustered nodes (KGGen Figure 1) only when multiple clusters or
    # oversized clusters left uncompared pairs.
    need_cross_passes = len(clusters) > 1 or any(
        len(c) > config.retrieval_top_k for c in clusters
    )
    if need_cross_passes and config.max_unclustered_passes > 0:
        for pass_idx in range(config.max_unclustered_passes):
            if len(unclustered) < 2:
                break
            pass_merged_any = False
            next_unclustered: list[str] = []
            rem_indices = [idx_by_item[item] for item in unclustered]
            while len(rem_indices) >= 2:
                anchor_idx = rem_indices[0]
                batch_indices = _hybrid_rank(
                    anchor_idx,
                    rem_indices,
                    sorted_items,
                    vec_kind,
                    vectors,
                    top_k=config.retrieval_top_k,
                )
                batch_items = [sorted_items[i] for i in batch_indices]
                step_counter += 1
                raw = client.request_json(
                    system=system_prompt,
                    user=json.dumps({kind: batch_items}, ensure_ascii=False),
                    stage=stage_name,
                    namespace=(
                        f"kggen:{config.mode}:stage3:{kind}:p{pass_idx}:s{step_counter}"
                    ),
                    usage_acc=usage_acc,
                )
                merged_in_batch = _apply_batch_clusters(raw, batch_items, parent)
                if merged_in_batch:
                    pass_merged_any = True
                if len(rem_indices) <= config.retrieval_top_k:
                    for idx in rem_indices:
                        item = sorted_items[idx]
                        if item not in merged_in_batch:
                            next_unclustered.append(item)
                    rem_indices = []
                else:
                    merged_indices = {idx_by_item[m] for m in merged_in_batch}
                    if anchor_idx not in merged_indices:
                        next_unclustered.append(sorted_items[anchor_idx])
                        merged_indices.add(anchor_idx)
                    rem_indices = [i for i in rem_indices if i not in merged_indices]
            if len(rem_indices) == 1:
                next_unclustered.append(sorted_items[rem_indices[0]])
            unclustered = next_unclustered
            if not pass_merged_any:
                break

    def _find_root(x: str) -> str:
        while parent.get(x, x) != x:
            parent[x] = parent.get(parent[x], parent[x])
            x = parent[x]
        return x

    canonical_map = {item: _find_root(item) for item in sorted_items}
    alias_map = {k: v for k, v in canonical_map.items() if k != v}
    return canonical_map, alias_map


def cluster_aggregated_graph(
    aggregated: AggregatedGraph,
    results: dict[str, CasePrediction],
    client: Any,
    config: KGGenConfig,
    usage_acc: CaseUsage,
) -> tuple[dict[str, CasePrediction], dict[str, Any]]:
    """Stage 3: Cluster entities (& open predicates), then project back preserving mentions."""
    entity_items = list(aggregated.entity_displays.values())
    ent_display_map, entity_alias_map = _disambiguate_item_set(
        items=entity_items,
        kind="entities",
        client=client,
        config=config,
        usage_acc=usage_acc,
    )
    ent_key_to_canonical = {
        ent_key: ent_display_map.get(display, display)
        for ent_key, display in aggregated.entity_displays.items()
    }

    # Majority-vote entity_type across merged entity keys.
    type_votes_by_canon: dict[str, Counter[str]] = defaultdict(Counter)
    for ent_key, canon_name in ent_key_to_canonical.items():
        etype = aggregated.entity_types.get(ent_key)
        if etype:
            type_votes_by_canon[canon_name][etype] += sum(
                aggregated.entity_variants[ent_key].values()
            )
    canon_entity_type = {
        canon: (votes.most_common(1)[0][0] if votes else None)
        for canon, votes in type_votes_by_canon.items()
    }

    # Only cluster predicates in open mode; constrained mode uses closed schema predicates.
    if config.mode == "open":
        pred_items = list(aggregated.predicate_displays.values())
        pred_display_map, predicate_alias_map = _disambiguate_item_set(
            items=pred_items,
            kind="predicates",
            client=client,
            config=config,
            usage_acc=usage_acc,
        )
    else:
        pred_items = list(aggregated.predicate_displays.values())
        pred_display_map = {p: p for p in pred_items}
        predicate_alias_map = {}

    pred_key_to_canonical = {
        pred_key: pred_display_map.get(display, display)
        for pred_key, display in aggregated.predicate_displays.items()
    }

    updated_results: dict[str, CasePrediction] = {}
    post_entity_keys: set[str] = set()
    post_predicate_keys: set[str] = set()
    post_cluster_relations = 0

    for cid in aggregated.case_order:
        if cid in aggregated.passthrough_cases:
            updated_results[cid] = aggregated.passthrough_cases[cid]
            continue

        orig_pred = results[cid]
        case_mentions = aggregated.surface_mentions_by_case.get(cid, {})

        # Merge entities in this case that collapsed to the same canonical name,
        # strictly preserving all original-case surface mentions from this case.
        merged_entities: dict[str, dict[str, Any]] = {}
        for rec in aggregated.case_entities.get(cid, []):
            agg_key = rec["agg_key"]
            canon_name = ent_key_to_canonical.get(agg_key, aggregated.entity_displays[agg_key])
            canon_key = normalize_kggen_key(canon_name)
            etype = canon_entity_type.get(canon_name) or rec.get("entity_type")
            mentions = list(case_mentions.get(agg_key, []))

            if canon_key not in merged_entities:
                merged_entities[canon_key] = {
                    "key": canon_name,
                    "name": canon_name,
                    "entity_type": etype,
                    "mentions": mentions,
                    "confidence": rec.get("confidence"),
                    "attributes": dict(rec.get("attributes") or {}),
                }
            else:
                bucket = merged_entities[canon_key]
                for m in mentions:
                    if m not in bucket["mentions"]:
                        bucket["mentions"].append(m)
                if bucket["entity_type"] is None and etype is not None:
                    bucket["entity_type"] = etype

        # Also update any implicit entities referenced by relations if not listed in case_entities.
        def _resolve_canon_entity(agg_key: str) -> str:
            if agg_key in ent_key_to_canonical:
                return ent_key_to_canonical[agg_key]
            return agg_key

        final_entities = [
            BaselineEntity(
                key=item["key"],
                name=item["name"],
                entity_type=item["entity_type"],
                mentions=item["mentions"],
                surface_text=item["mentions"][0] if item["mentions"] else item["name"],
                confidence=item["confidence"],
                attributes=item["attributes"],
            )
            for item in merged_entities.values()
        ]
        for ent in final_entities:
            post_entity_keys.add(normalize_kggen_key(ent.name))

        # Remap and deduplicate relations within this case.
        dedup_rels: dict[tuple[Any, ...], BaselineRelation] = {}
        for r in aggregated.case_relations.get(cid, []):
            subj_canon = _resolve_canon_entity(r["subject_key"])
            obj_canon = _resolve_canon_entity(r["object_key"])
            pred_canon = pred_key_to_canonical.get(
                r["predicate_key"],
                aggregated.predicate_displays.get(r["predicate_key"], r["predicate_key"]),
            )
            if normalize_kggen_key(subj_canon) == normalize_kggen_key(obj_canon):
                continue

            post_predicate_keys.add(normalize_kggen_key(pred_canon))
            dedup_id = (
                normalize_kggen_key(subj_canon),
                normalize_kggen_key(pred_canon),
                normalize_kggen_key(obj_canon),
                r.get("assertion") or "",
                tuple(sorted((r.get("qualifiers") or {}).items())),
            )
            if dedup_id not in dedup_rels:
                dedup_rels[dedup_id] = BaselineRelation(
                    subject=subj_canon,
                    predicate=pred_canon,
                    object=obj_canon,
                    assertion=r.get("assertion"),
                    qualifiers=dict(r.get("qualifiers") or {}),
                    quote=r.get("quote") or "",
                    confidence=r.get("confidence"),
                )

        final_relations = list(dedup_rels.values())
        post_cluster_relations += len(final_relations)

        updated_results[cid] = CasePrediction(
            status="success",
            entities=final_entities,
            relations=final_relations,
            seconds=orig_pred.seconds,
            usage=orig_pred.usage,
            diagnostics={
                **orig_pred.diagnostics,
                "stage3_clustered": True,
            },
        )

    pre_e = aggregated.pre_cluster_entities
    post_e = len(post_entity_keys)
    pre_p = aggregated.pre_cluster_predicates
    post_p = len(post_predicate_keys)
    corpus_stats: dict[str, Any] = {
        "pre_cluster_entities": pre_e,
        "post_cluster_entities": post_e,
        "entity_compression_ratio": round((pre_e - post_e) / pre_e, 4) if pre_e else 0.0,
        "pre_cluster_predicates": pre_p,
        "post_cluster_predicates": post_p,
        "predicate_compression_ratio": round((pre_p - post_p) / pre_p, 4) if pre_p else 0.0,
        "pre_cluster_relations": aggregated.pre_cluster_relations,
        "post_cluster_relations": post_cluster_relations,
        "entity_alias_map": entity_alias_map,
        "predicate_alias_map": predicate_alias_map,
    }
    return updated_results, corpus_stats


def extract_case_library(
    case: dict[str, Any],
    config: KGGenConfig,
    *,
    model: str | None = None,
    base_url: str | None = None,
    api_key: str | None = None,
    temperature: float = 0.0,
) -> CasePrediction:
    """Optional official `kg_gen` library adapter (disabled unless package is installed)."""
    if importlib.util.find_spec("kg_gen") is None:
        raise ImportError(
            "未安装官方 `kg_gen` 包（kg-gen）。请先安装 `kg-gen`，或使用默认 `--backend native`。"
        )
    kg_gen_mod = importlib.import_module("kg_gen")
    started = time.perf_counter()
    chunk_text = case_text(case)
    formatted_input = format_case_input(case, config.input_scope)

    kg = kg_gen_mod.KGGen(
        model=model or "openai/deepseek-chat",
        temperature=temperature,
        api_key=api_key,
        api_base=base_url,
    )
    context_note = ""
    if config.mode == "constrained":
        context_note = (
            "Allowed entity types: "
            + ", ".join(ENTITY_TYPE_DEFINITIONS.keys())
            + ". Allowed predicates: "
            + ", ".join(p for p in PREDICATE_DEFINITIONS if p not in DERIVED_PREDICATES)
        )
    graph = kg.generate(
        input_data=formatted_input,
        context=context_note,
        cluster=config.enable_clustering,
    )

    entities: list[BaselineEntity] = []
    for raw_ent in getattr(graph, "entities", []) or []:
        name = str(raw_ent).strip()
        if not name:
            continue
        spans = recover_original_spans(chunk_text, name)
        mentions = spans if spans else [name]
        entities.append(
            BaselineEntity(
                key=name,
                name=name,
                entity_type=None,
                mentions=mentions,
                surface_text=mentions[0],
            )
        )

    relations: list[BaselineRelation] = []
    for rel in getattr(graph, "relations", []) or []:
        if isinstance(rel, list | tuple) and len(rel) >= 3:
            s, p, o = str(rel[0]).strip(), str(rel[1]).strip(), str(rel[2]).strip()
            if s and p and o:
                relations.append(BaselineRelation(subject=s, predicate=p, object=o))

    return CasePrediction(
        status="success",
        entities=entities,
        relations=relations,
        seconds=round(time.perf_counter() - started, 4),
        usage=CaseUsage(),
        diagnostics={"kggen_backend": "library", "kggen_mode": config.mode},
    )


class KGGenAdapter:
    """High-level KGGen orchestrator supporting per-chunk and corpus-batch evaluation."""

    def __init__(
        self,
        config: KGGenConfig,
        client: Any = None,
        *,
        model: str | None = None,
        base_url: str | None = None,
        api_key: str | None = None,
        temperature: float = 0.0,
    ) -> None:
        self.config = config
        self.client = client
        self.model = model
        self.base_url = base_url
        self.api_key = api_key
        self.temperature = temperature

    def extract_case(self, case: dict[str, Any]) -> CasePrediction:
        if self.config.backend == "library":
            return extract_case_library(
                case,
                self.config,
                model=self.model,
                base_url=self.base_url,
                api_key=self.api_key,
                temperature=self.temperature,
            )

        if self.client is None:
            raise ValueError("KGGen native 模式需要提供 ComparisonLLMClient 实例")
        pred = extract_case_native(case, self.client, self.config)
        if (
            self.config.granularity == "per_chunk"
            and self.config.enable_clustering
            and pred.status == "success"
        ):
            cid = str(case["id"])
            stage3_usage = CaseUsage()
            agg = aggregate_case_predictions({cid: pred}, {cid: case})
            updated, stats = cluster_aggregated_graph(
                agg, {cid: pred}, self.client, self.config, stage3_usage
            )
            clustered = updated[cid]
            merged_usage = _merge_usage(pred.usage, stage3_usage)
            clustered.usage = merged_usage
            clustered.diagnostics["per_case_cluster_stats"] = stats
            return clustered
        return pred

    def cluster_corpus(
        self,
        cases_by_id: dict[str, dict[str, Any]],
        results: dict[str, CasePrediction],
        usage_acc: CaseUsage,
    ) -> tuple[dict[str, CasePrediction], dict[str, Any]]:
        agg = aggregate_case_predictions(results, cases_by_id)
        return cluster_aggregated_graph(agg, results, self.client, self.config, usage_acc)


def _merge_usage(first: CaseUsage, second: CaseUsage) -> CaseUsage:
    merged_stages: dict[str, dict[str, int]] = {}
    for src in (first.by_stage, second.by_stage):
        for stage, bucket in src.items():
            target = merged_stages.setdefault(stage, {})
            for k, v in bucket.items():
                target[k] = target.get(k, 0) + v
    return CaseUsage(
        prompt_tokens=first.prompt_tokens + second.prompt_tokens,
        completion_tokens=first.completion_tokens + second.completion_tokens,
        total_tokens=first.total_tokens + second.total_tokens,
        cached_tokens=first.cached_tokens + second.cached_tokens,
        reasoning_tokens=first.reasoning_tokens + second.reasoning_tokens,
        http_calls=first.http_calls + second.http_calls,
        replayed_calls=first.replayed_calls + second.replayed_calls,
        unknown_usage_calls=first.unknown_usage_calls + second.unknown_usage_calls,
        by_stage=merged_stages,
    )
