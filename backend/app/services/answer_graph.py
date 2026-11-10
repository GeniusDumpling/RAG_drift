"""Ground graph recall in searchable source chunks and merge by chunk identity."""

import asyncio
from dataclasses import dataclass
from time import perf_counter
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.models.content import ContentChunk
from app.schemas.search import EvidenceObject, GraphAnswerEvidence, SearchFilters
from app.schemas.video_graph import GraphRetrieveRequest
from app.services.graph_retrieval import retrieve_graph_context
from app.services.retrieval import _hydrate_and_rank_evidence, _SearchHit
from app.services.video_graph.grounding import source_span


@dataclass(frozen=True)
class GraphRecall:
    evidence: list[EvidenceObject]
    trace: dict[str, Any]


async def recall_graph_evidence(
    session: AsyncSession, query: str, filters: SearchFilters, settings: Settings
) -> GraphRecall:
    started = perf_counter()
    trace: dict[str, Any] = {"status": "disabled", "backend": "neo4j"}
    evidence: list[EvidenceObject] = []
    if settings.answer_graph_enabled:
        try:
            async with (
                session.begin_nested(),
                asyncio.timeout(settings.answer_graph_timeout_seconds),
            ):
                result = await retrieve_graph_context(
                    session,
                    GraphRetrieveRequest(query=query[:1000], top_k=settings.answer_graph_top_k),
                    settings,
                )
                nodes = {node.id: node.label for node in result.graph.nodes}
                edges = {edge.id: edge for edge in result.graph.edges if edge.kind == "relation"}
                grounded: dict[UUID, list[GraphAnswerEvidence]] = {}
                hits = []
                for entry in result.evidence:
                    edge = edges.get(str(entry.relation_id))
                    if entry.chunk_id is None or edge is None:
                        continue
                    if edge.source not in nodes or edge.target not in nodes:
                        continue
                    if entry.chunk_id not in grounded:
                        hits.append(_SearchHit(
                            entry.chunk_id, entry.content_item_id, 1.0, "graph"
                        ))
                        grounded[entry.chunk_id] = []
                    grounded[entry.chunk_id].append(GraphAnswerEvidence(
                        **entry.model_dump(), subject=nodes[edge.source],
                        predicate=edge.predicate, object=nodes[edge.target],
                    ))
                evidence = await _hydrate_and_rank_evidence(
                    session, hits=hits, filters=filters, top_k=settings.answer_graph_top_k,
                    snippet_terms=(), max_per_content=2,
                )
                texts = dict((await session.execute(
                    select(ContentChunk.id, ContentChunk.display_text).where(
                        ContentChunk.id.in_([item.chunk_id for item in evidence])
                    )
                )).tuples().all())
                for item in evidence:
                    item.graph_evidence = [
                        entry for entry in grounded[item.chunk_id]
                        if entry.content_item_id == item.content_item_id
                        and source_span(texts[item.chunk_id], entry.quote) is not None
                    ][:3]
                evidence = [item for item in evidence if item.graph_evidence]
                trace.update(
                    status="retrieved" if evidence else "empty",
                    raw_evidence_count=len(result.evidence),
                    truncated=result.graph.truncated,
                    chunk_ids=[str(item.chunk_id) for item in evidence],
                )
        except Exception as exc:
            # Keep credentials, source text and provider error bodies out of logs.
            evidence = []
            trace.update(status="fallback", reason=type(exc).__name__)
    trace.update(evidence_count=len(evidence), latency_ms=round((perf_counter() - started) * 1000))
    return GraphRecall(evidence, trace)


def merge_graph_evidence(
    regular: list[EvidenceObject], graph: list[EvidenceObject]
) -> list[EvidenceObject]:
    # Rank interleaving avoids treating extraction confidence as relevance.
    regular_by_id = {item.chunk_id: item for item in regular}
    graph_by_id = {item.chunk_id: item for item in graph}
    merged: dict[UUID, EvidenceObject] = {}
    for index in range(max(len(regular), len(graph))):
        for items in (regular, graph):
            if index >= len(items):
                continue
            item = items[index]
            previous = merged.get(item.chunk_id)
            if previous is None:
                candidate = regular_by_id.get(item.chunk_id, item).model_copy(deep=True)
                graph_item = graph_by_id.get(item.chunk_id)
                if graph_item is not None:
                    candidate.graph_evidence = graph_item.graph_evidence
                merged[item.chunk_id] = candidate
    return list(merged.values())
