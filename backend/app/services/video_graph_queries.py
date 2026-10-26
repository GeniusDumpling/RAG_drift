"""Bounded graph browsing and evidence context for a future GraphRAG orchestrator."""

import uuid
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from sqlalchemy import String, case, cast, exists, func, select
from sqlalchemy.orm import Session

from app.models.content import ContentItem
from app.models.search import ContentEntityMention, Entity
from app.models.video_graph import (
    VideoGraphAlias,
    VideoGraphBuild,
    VideoGraphEvidence,
    VideoGraphRelation,
    VideoGraphState,
)
from app.schemas.video_graph import (
    GraphData,
    GraphEdge,
    GraphEvidenceRead,
    GraphNode,
    GraphRetrieveRequest,
    GraphRetrieveResponse,
)
from app.services.graph_display import connected_display_graph
from app.services.video_graph import MENTION_METHOD, load_material, normalized_name


def active_mentions(content_id: uuid.UUID | None = None) -> Any:
    query = (
        select(ContentEntityMention)
        .join(
            VideoGraphState, VideoGraphState.content_item_id == ContentEntityMention.content_item_id
        )
        .where(
            VideoGraphState.withdrawn.is_(False),
            VideoGraphState.active_build_id.is_not(None),
            ContentEntityMention.extraction_method == MENTION_METHOD,
            ContentEntityMention.metadata_json["video_graph_build_id"].astext
            == cast(VideoGraphState.active_build_id, String),
        )
    )
    if content_id:
        query = query.where(ContentEntityMention.content_item_id == content_id)
    return query


def active_evidence(content_id: uuid.UUID | None = None, assertion: str | None = None) -> Any:
    query = (
        select(VideoGraphEvidence, VideoGraphBuild, ContentItem)
        .join(VideoGraphBuild, VideoGraphBuild.id == VideoGraphEvidence.build_id)
        .join(VideoGraphState, VideoGraphState.active_build_id == VideoGraphBuild.id)
        .join(ContentItem, ContentItem.id == VideoGraphBuild.content_item_id)
        .where(
            VideoGraphState.withdrawn.is_(False),
            ContentItem.item_type == "video_description",
        )
    )
    if content_id:
        query = query.where(ContentItem.id == content_id)
    if assertion:
        query = query.where(VideoGraphEvidence.assertion == assertion)
    return query


def _matching_entities(
    *,
    q: str = "",
    entity_type: str | None = None,
    content_id: uuid.UUID | None = None,
) -> Any:
    mentions = active_mentions(content_id).where(ContentEntityMention.entity_id == Entity.id)
    query = select(Entity).where(exists(mentions))
    if entity_type:
        query = query.where(Entity.entity_type == entity_type)
    if q.strip():
        query = query.where(
            exists(
                select(VideoGraphAlias.id).where(
                    VideoGraphAlias.entity_id == Entity.id,
                    VideoGraphAlias.normalized_alias.contains(normalized_name(q), autoescape=True),
                )
            )
        )
    return query


def list_entities(
    session: Session,
    *,
    q: str = "",
    entity_type: str | None = None,
    content_id: uuid.UUID | None = None,
    limit: int = 50,
    offset: int = 0,
) -> dict[str, Any]:
    query = _matching_entities(q=q, entity_type=entity_type, content_id=content_id)
    total = session.scalar(select(func.count()).select_from(query.subquery())) or 0
    entities = session.scalars(
        query.order_by(Entity.canonical_name, Entity.id).offset(offset).limit(limit)
    ).all()
    return {
        "items": [
            {"id": str(e.id), "name": e.canonical_name or e.name, "entity_type": e.entity_type}
            for e in entities
        ],
        "total": total,
        "limit": limit,
        "offset": offset,
    }


def relation_stats(content_id: uuid.UUID | None = None, assertion: str | None = None) -> Any:
    evidence = (
        active_evidence(content_id, assertion)
        .with_only_columns(VideoGraphEvidence.relation_id, VideoGraphEvidence.assertion)
        .subquery()
    )
    return (
        select(
            VideoGraphRelation,
            func.count().label("evidence_count"),
            func.sum(case((evidence.c.assertion == "asserted", 1), else_=0)).label("asserted"),
            func.sum(case((evidence.c.assertion == "uncertain", 1), else_=0)).label("uncertain"),
            func.sum(case((evidence.c.assertion == "negated", 1), else_=0)).label("negated"),
        )
        .join(evidence, evidence.c.relation_id == VideoGraphRelation.id)
        .group_by(VideoGraphRelation.id)
    )


def get_graph(
    session: Session,
    *,
    entity_id: uuid.UUID | None = None,
    content_id: uuid.UUID | None = None,
    q: str = "",
    entity_type: str | None = None,
    assertion: str | None = None,
    hops: int = 1,
    max_nodes: int | None = 80,
    max_edges: int | None = 160,
    min_component_size: int = 0,
) -> GraphData:
    if min_component_size > 0:
        candidates = get_graph(
            session, entity_id=entity_id, content_id=content_id, q=q,
            entity_type=entity_type, assertion=assertion, hops=hops,
            max_nodes=None, max_edges=None,
        )
        return connected_display_graph(
            candidates, min_component_size=min_component_size,
            max_nodes=max_nodes, max_edges=max_edges,
        )
    nodes: dict[str, GraphNode] = {}
    edges: dict[str, GraphEdge] = {}
    truncated = False
    if content_id:
        item = session.get(ContentItem, content_id)
        if item is None or item.item_type != "video_description":
            return GraphData(nodes=[], edges=[])
        state = session.get(VideoGraphState, content_id)
        if state is None or state.withdrawn or state.active_build_id is None:
            return GraphData(nodes=[], edges=[])
        video_key = f"video:{content_id}"
        nodes[video_key] = GraphNode(
            id=video_key, label=item.title or "视频", kind="video", content_item_id=content_id
        )
    matching = _matching_entities(q=q, entity_type=entity_type, content_id=content_id)
    matching_ids = matching.with_only_columns(Entity.id)

    def add_entity(entity: Entity) -> None:
        key = str(entity.id)
        nodes[key] = GraphNode(
            id=key,
            label=entity.canonical_name or entity.name,
            kind="entity",
            entity_type=entity.entity_type,
        )

    if entity_id:
        entity = session.scalar(
            _matching_entities(entity_type=entity_type, content_id=content_id)
            .where(Entity.id == entity_id)
        )
        if entity is None:
            return GraphData(nodes=list(nodes.values()), edges=[])
        add_entity(entity)
        frontier: Any = {entity.id}
    else:
        # Match seeds in SQL, but reserve node capacity for complete relation pairs.
        frontier = matching_ids
    expansion_hops = hops if entity_id or q.strip() else 1
    for _ in range(expansion_hops):
        query = (
            relation_stats(content_id, assertion)
            .where(
                VideoGraphRelation.subject_id.in_(frontier)
                | VideoGraphRelation.object_id.in_(frontier),
            )
            .order_by(func.count().desc(), VideoGraphRelation.id)
        )
        if entity_type:
            typed_ids = _matching_entities(
                entity_type=entity_type, content_id=content_id
            ).with_only_columns(Entity.id)
            query = query.where(
                VideoGraphRelation.subject_id.in_(typed_ids),
                VideoGraphRelation.object_id.in_(typed_ids),
            )
        new_frontier = set()
        # Stream candidates so a pair that doesn't fit cannot hide later pairs that do.
        for relation, count, asserted, uncertain, negated in session.execute(
            query.execution_options(yield_per=200)
        ):
            edge_key = str(relation.id)
            if edge_key in edges:
                continue
            missing = {str(relation.subject_id), str(relation.object_id)} - nodes.keys()
            if max_edges is not None and len(edges) >= max_edges:
                truncated = True
                break
            if max_nodes is not None and len(nodes) + len(missing) > max_nodes:
                truncated = True
                continue
            candidates = session.scalars(
                select(Entity).where(Entity.id.in_([uuid.UUID(k) for k in sorted(missing)]))
            ).all()
            for entity in candidates:
                add_entity(entity)
                new_frontier.add(entity.id)
            edges[edge_key] = GraphEdge(
                id=edge_key,
                source=str(relation.subject_id),
                target=str(relation.object_id),
                predicate=relation.predicate,
                kind="relation",
                evidence_count=count,
                assertions={"asserted": asserted, "uncertain": uncertain, "negated": negated},
                qualifiers=relation.qualifiers_json,
            )
        frontier = new_frontier
        if not frontier:
            break
    if not entity_id:
        # Isolated mentions remain browsable, after relations have claimed their nodes.
        capacity = None if max_nodes is None else max_nodes - len(nodes)
        selected_ids = [uuid.UUID(k) for k, node in nodes.items() if node.kind == "entity"]
        remaining = session.scalars(
            matching.where(Entity.id.not_in(selected_ids))
            .order_by(Entity.canonical_name, Entity.id)
            .limit(None if capacity is None else capacity + 1)
        ).all()
        truncated = truncated or (capacity is not None and len(remaining) > capacity)
        for entity in remaining[:capacity]:
            add_entity(entity)
    if content_id:
        mentions = session.scalars(
            active_mentions(content_id).where(
                ContentEntityMention.entity_id.in_(
                    [uuid.UUID(k) for k, n in nodes.items() if n.kind == "entity"]
                )
            )
        ).all()
        for mention in mentions:
            edge_key = f"mention:{content_id}:{mention.entity_id}"
            if edge_key in edges:
                edges[edge_key].evidence_count += 1
            elif max_edges is None or len(edges) < max_edges:
                edges[edge_key] = GraphEdge(
                    id=edge_key,
                    source=f"video:{content_id}",
                    target=str(mention.entity_id),
                    predicate="mentions",
                    kind="mention",
                    evidence_count=1,
                )
            else:
                truncated = True
    return GraphData(nodes=list(nodes.values()), edges=list(edges.values()), truncated=truncated)


def timestamp_url(url: str, seconds: float | None) -> str:
    parsed = urlsplit(url)
    if seconds is None or parsed.hostname not in {
        "youtube.com",
        "www.youtube.com",
        "m.youtube.com",
        "youtu.be",
    }:
        return url
    params = [(k, v) for k, v in parse_qsl(parsed.query) if k not in {"t", "start"}]
    params.append(("t", str(int(seconds))))
    return urlunsplit(
        (parsed.scheme, parsed.netloc, parsed.path, urlencode(params), parsed.fragment)
    )


def read_evidence(row: Any) -> GraphEvidenceRead:
    evidence, build, item = row
    return GraphEvidenceRead(
        id=evidence.id,
        relation_id=evidence.relation_id,
        content_item_id=item.id,
        chunk_id=evidence.content_chunk_id,
        title=item.title,
        video_url=timestamp_url(item.canonical_url, evidence.start_seconds),
        quote=evidence.quote,
        start_seconds=evidence.start_seconds,
        end_seconds=evidence.end_seconds,
        assertion=evidence.assertion,
        confidence=evidence.confidence,
        caption_source=evidence.caption_source,
        extractor_version=build.extractor_version,
    )


def relation_evidence(
    session: Session,
    relation_id: uuid.UUID,
    *,
    content_id: uuid.UUID | None = None,
    assertion: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> dict[str, Any]:
    query = active_evidence(content_id, assertion).where(
        VideoGraphEvidence.relation_id == relation_id
    )
    total = session.scalar(select(func.count()).select_from(query.subquery())) or 0
    rows = session.execute(
        query.order_by(ContentItem.id, VideoGraphEvidence.start_seconds, VideoGraphEvidence.id)
        .offset(offset)
        .limit(limit)
    ).all()
    return {
        "items": [read_evidence(row) for row in rows],
        "total": total,
        "limit": limit,
        "offset": offset,
    }


def overview(session: Session) -> dict[str, Any]:
    videos = (
        session.scalar(
            select(func.count())
            .select_from(ContentItem)
            .where(ContentItem.item_type == "video_description")
        )
        or 0
    )
    active_videos = (
        session.scalar(
            select(func.count())
            .select_from(VideoGraphState)
            .where(
                VideoGraphState.active_build_id.is_not(None), VideoGraphState.withdrawn.is_(False)
            )
        )
        or 0
    )
    builds = {
        status: count
        for status, count in session.execute(
            select(VideoGraphBuild.status, func.count()).group_by(VideoGraphBuild.status)
        ).all()
    }
    entity_count = list_entities(session, limit=1)["total"]
    evidence_count = (
        session.scalar(select(func.count()).select_from(active_evidence().subquery())) or 0
    )
    relation_count = (
        session.scalar(select(func.count()).select_from(relation_stats().subquery())) or 0
    )
    current = session.execute(
        select(VideoGraphBuild.status, VideoGraphBuild.output_json)
        .join(VideoGraphState, VideoGraphState.desired_build_id == VideoGraphBuild.id)
        .where(VideoGraphState.withdrawn.is_(False))
    ).all()
    coverage_rows = [
        output["coverage"] for _, output in current
        if output.get("coverage", {}).get("applicable")
    ]
    chunk_coverage = {
        "videos": len(coverage_rows),
        **{
            key: sum(row.get(key, 0) for row in coverage_rows)
            for key in ("total_chunks", "passed_chunks", "skipped_chunks", "pending_chunks")
        },
    }
    return {
        "videos": videos,
        "active_videos": active_videos,
        "entities": entity_count,
        "relations": relation_count,
        "evidence": evidence_count,
        "builds": builds,
        "partial_videos": sum(status == "partial_success" for status, _ in current),
        "chunk_coverage": chunk_coverage,
    }


def video_status(session: Session, content_id: uuid.UUID) -> dict[str, Any] | None:
    material = load_material(session, content_id)
    if material is None:
        return None
    state = session.get(VideoGraphState, content_id)
    active = (
        session.get(VideoGraphBuild, state.active_build_id)
        if state and state.active_build_id
        else None
    )
    desired = (
        session.get(VideoGraphBuild, state.desired_build_id)
        if state and state.desired_build_id
        else None
    )
    desired_output = (desired.output_json or {}) if desired else {}
    active_output = (active.output_json or {}) if active else {}
    return {
        "content_item_id": str(content_id),
        "withdrawn": bool(state and state.withdrawn),
        "status": "withdrawn"
        if state and state.withdrawn
        else desired.status
        if desired
        else "not_queued",
        "active_build_id": str(active.id) if active else None,
        "source_changed": bool(active and active.fingerprint != material.fingerprint),
        "assessment": desired_output.get("assessment") or active_output.get("assessment"),
        "attempts": desired.attempts if desired else 0,
        "error": desired.error_message if desired else None,
        "transcript_chunks": sum(c.evidence_type == "transcript_segment" for c in material.chunks),
        "summary_only": bool(material.extraction_chunks)
        and all(c.evidence_type == "video_summary" for c in material.extraction_chunks),
        "coverage": desired_output.get("coverage"),
        "active_coverage": active_output.get("published_coverage"),
        "skipped_chunks": list(desired_output.get("chunk_errors", {}).values()),
        "projection_activated": desired_output.get("projection_activated", False),
        "retained_active_build_id": desired_output.get("retained_active_build_id"),
    }


def retrieve_context(session: Session, request: GraphRetrieveRequest) -> GraphRetrieveResponse:
    # Explicit alias linking only. A future orchestrator can add vector-linked seed entities.
    query_name = normalized_name(request.query)
    aliases = session.execute(
        select(VideoGraphAlias.normalized_alias, VideoGraphAlias.entity_id)
        .where(
            exists(
                active_mentions(request.content_item_id).where(
                    ContentEntityMention.entity_id == VideoGraphAlias.entity_id
                )
            ),
            func.strpos(query_name, VideoGraphAlias.normalized_alias) > 0,
        )
        .order_by(func.length(VideoGraphAlias.normalized_alias).desc(), VideoGraphAlias.entity_id)
        .limit(20)
    ).all()
    graphs = [
        get_graph(
            session,
            entity_id=entity_id,
            content_id=request.content_item_id,
            hops=1,
            max_nodes=50,
            max_edges=100,
        )
        for entity_id in dict.fromkeys(row.entity_id for row in aliases)
    ]
    nodes: dict[str, GraphNode] = {}
    edges: dict[str, GraphEdge] = {}
    truncated = any(graph.truncated for graph in graphs)
    for graph in graphs:
        for node in graph.nodes:
            if node.id not in nodes and len(nodes) >= 80:
                truncated = True
                continue
            nodes[node.id] = node
        for edge in graph.edges:
            if edge.source in nodes and edge.target in nodes and len(edges) < 160:
                edges[edge.id] = edge
            else:
                truncated = True
    graph = GraphData(nodes=list(nodes.values()), edges=list(edges.values()), truncated=truncated)
    relation_ids = [uuid.UUID(edge.id) for edge in graph.edges if edge.kind == "relation"]
    rows = session.execute(
        active_evidence(request.content_item_id)
        .where(VideoGraphEvidence.relation_id.in_(relation_ids))
        .order_by(VideoGraphEvidence.confidence.desc(), VideoGraphEvidence.id)
        .limit(request.top_k * 5)
    ).all()
    evidence: list[GraphEvidenceRead] = []
    blocks: list[str] = []
    seen: set[tuple[uuid.UUID, str, str, uuid.UUID]] = set()
    used_chars = 0
    for row in rows:
        entry = read_evidence(row)
        key = (entry.content_item_id, entry.quote, entry.assertion, entry.relation_id)
        if key in seen:
            continue
        seen.add(key)
        block = (
            f"[{len(evidence) + 1}] {entry.title or '视频'}\nURL: {entry.video_url}\n"
            f"陈述状态: {entry.assertion}（字幕陈述，未独立事实验证）\n原文: {entry.quote}"
        )
        if used_chars + len(block) + 2 > request.max_chars:
            continue
        evidence.append(entry)
        blocks.append(block)
        used_chars += len(block) + 2
        if len(evidence) >= request.top_k:
            break
    return GraphRetrieveResponse(graph=graph, evidence=evidence, context="\n\n".join(blocks))
