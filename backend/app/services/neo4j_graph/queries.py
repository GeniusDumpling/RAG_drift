"""Query only active projections; evidence keeps contradictory assertions separate."""

import uuid

from sqlalchemy import func, select

from app.models.content import ContentItem
from app.schemas.video_graph import (
    GraphData,
    GraphEdge,
    GraphEvidenceRead,
    GraphNode,
    GraphRetrieveResponse,
)
from app.services.neo4j_graph.store import decode
from app.services.video_graph import load_material, normalized_name
from app.services.video_graph_queries import timestamp_url

ACTIVE = (
    "MATCH (v:VGVideo)-[:HAS_BUILD]->(b:VGBuild) "
    "WHERE v.active_build_id=b.id AND NOT v.withdrawn AND NOT coalesce(v.deleted,false) "
    "AND ($content_id IS NULL OR v.id=$content_id) "
)
MENTIONS = ACTIVE + "MATCH (b)-[:HAS_MENTION]->(m:VGMention)-[:REFERS_TO]->(e:VGEntity) "
EVIDENCE = ACTIVE + "MATCH (b)-[:HAS_EVIDENCE]->(ev:VGEvidence)-[:SUPPORTS]->(r:VGRelation) "


class GraphQueries:
    def __init__(self, store):
        self.store = store

    def matching(self, q="", entity_type=None, content_id=None, entity_id=None):
        return self.store.read(
            MENTIONS + "WHERE ($kind IS NULL OR e.entity_type=$kind) "
            'AND ($eid IS NULL OR e.id=$eid) AND ($q="" OR EXISTS { '
            "MATCH (a:VGAlias)-[:ALIAS_OF]->(e) WHERE a.normalized_alias CONTAINS $q }) "
            "RETURN DISTINCT e ORDER BY e.canonical_name,e.id",
            content_id=str(content_id) if content_id else None,
            kind=entity_type,
            eid=str(entity_id) if entity_id else None,
            q=normalized_name(q),
        )

    def list_entities(
        self, session, *, q="", entity_type=None, content_id=None, limit=50, offset=0
    ):
        params = dict(
            content_id=str(content_id) if content_id else None,
            kind=entity_type,
            q=normalized_name(q),
        )
        base = (
            MENTIONS + "WHERE ($kind IS NULL OR e.entity_type=$kind) "
            'AND ($q="" OR EXISTS { MATCH (a:VGAlias)-[:ALIAS_OF]->(e) '
            "WHERE a.normalized_alias CONTAINS $q }) "
        )
        total = self.store.read(base + "RETURN count(DISTINCT e) AS total", **params)[0]["total"]
        rows = self.store.read(
            base + "RETURN DISTINCT e ORDER BY e.canonical_name,e.id " "SKIP $offset LIMIT $limit",
            **params,
            offset=offset,
            limit=limit,
        )
        return dict(
            items=[
                dict(
                    id=r["e"]["id"],
                    name=r["e"].get("canonical_name") or r["e"]["name"],
                    entity_type=r["e"]["entity_type"],
                )
                for r in rows
            ],
            total=total,
            limit=limit,
            offset=offset,
        )

    def get_graph(
        self,
        session,
        *,
        entity_id=None,
        content_id=None,
        q="",
        entity_type=None,
        assertion=None,
        hops=1,
        max_nodes=80,
        max_edges=160,
    ):
        nodes, edges, truncated = {}, {}, False
        cid = str(content_id) if content_id else None
        if cid:
            rows = self.store.read(ACTIVE + "RETURN v", content_id=cid)
            if not rows:
                return GraphData(nodes=[], edges=[])
            nodes["video:" + cid] = GraphNode(
                id="video:" + cid,
                label=rows[0]["v"].get("title") or "视频",
                kind="video",
                content_item_id=content_id,
            )

        def add(e):
            nodes[e["id"]] = GraphNode(
                id=e["id"],
                label=e.get("canonical_name") or e["name"],
                kind="entity",
                entity_type=e["entity_type"],
            )

        matches = self.matching(
            q=q if not entity_id else "",
            entity_type=entity_type,
            content_id=content_id,
            entity_id=entity_id,
        )
        if entity_id:
            if not matches:
                return GraphData(nodes=list(nodes.values()), edges=[])
            add(matches[0]["e"])
        frontier = [row["e"]["id"] for row in matches]
        for _ in range(hops if entity_id or q.strip() else 1):
            if not frontier:
                break
            # Query relation pairs and aggregate only evidence from active builds.
            rows = self.store.read(
                EVIDENCE + "WHERE ($assertion IS NULL OR ev.assertion=$assertion) "
                "MATCH (r)-[:SUBJECT]->(s:VGEntity),(r)-[:OBJECT]->(o:VGEntity) "
                "WHERE (s.id IN $frontier OR o.id IN $frontier) "
                "AND ($kind IS NULL OR (s.entity_type=$kind AND o.entity_type=$kind)) "
                "RETURN r,s,o,count(ev) AS count, "
                'sum(CASE WHEN ev.assertion="asserted" THEN 1 ELSE 0 END) AS asserted, '
                'sum(CASE WHEN ev.assertion="uncertain" THEN 1 ELSE 0 END) AS uncertain, '
                'sum(CASE WHEN ev.assertion="negated" THEN 1 ELSE 0 END) AS negated '
                "ORDER BY count DESC,r.id",
                content_id=cid,
                assertion=assertion,
                frontier=frontier,
                kind=entity_type,
            )
            new_frontier = set()
            for row in rows:
                r, s, o = row["r"], row["s"], row["o"]
                if r["id"] in edges:
                    continue
                missing = {s["id"], o["id"]} - nodes.keys()
                if len(edges) >= max_edges:
                    truncated = True
                    break
                if len(nodes) + len(missing) > max_nodes:
                    truncated = True
                    continue
                add(s)
                add(o)
                new_frontier.update(missing)
                edges[r["id"]] = GraphEdge(
                    id=r["id"],
                    source=s["id"],
                    target=o["id"],
                    predicate=r["predicate"],
                    kind="relation",
                    evidence_count=row["count"],
                    assertions={k: row[k] for k in ("asserted", "uncertain", "negated")},
                    qualifiers=decode(r.get("qualifiers_json")),
                )
            frontier = list(new_frontier)
        if not entity_id:
            remaining = [row["e"] for row in matches if row["e"]["id"] not in nodes]
            capacity = max_nodes - len(nodes)
            truncated |= len(remaining) > capacity
            for entity in remaining[:capacity]:
                add(entity)
        if cid:
            rows = self.store.read(
                MENTIONS + "WHERE e.id IN $ids RETURN e.id AS id,count(m) AS count",
                content_id=cid,
                ids=list(nodes),
            )
            for row in rows:
                if len(edges) >= max_edges:
                    truncated = True
                    break
                key = f"mention:{cid}:{row['id']}"
                edges[key] = GraphEdge(
                    id=key,
                    source="video:" + cid,
                    target=row["id"],
                    predicate="mentions",
                    kind="mention",
                    evidence_count=row["count"],
                )
        return GraphData(
            nodes=list(nodes.values()), edges=list(edges.values()), truncated=truncated
        )

    @staticmethod
    def evidence_row(row):
        ev, b, v, r = row["ev"], row["b"], row["v"], row["r"]
        return GraphEvidenceRead(
            id=ev["id"],
            relation_id=r["id"],
            content_item_id=v["id"],
            chunk_id=ev.get("content_chunk_id"),
            title=v.get("title"),
            video_url=timestamp_url(v.get("url", ""), ev.get("start_seconds")),
            quote=ev["quote"],
            start_seconds=ev.get("start_seconds"),
            end_seconds=ev.get("end_seconds"),
            assertion=ev["assertion"],
            confidence=ev["confidence"],
            caption_source=ev.get("caption_source"),
            extractor_version=b["extractor_version"],
        )

    def relation_evidence(
        self, session, relation_id, *, content_id=None, assertion=None, limit=50, offset=0
    ):
        params = dict(
            content_id=str(content_id) if content_id else None,
            rid=str(relation_id),
            assertion=assertion,
        )
        base = EVIDENCE + "WHERE r.id=$rid AND ($assertion IS NULL OR ev.assertion=$assertion) "
        total = self.store.read(base + "RETURN count(ev) AS total", **params)[0]["total"]
        rows = self.store.read(
            base + "RETURN ev,b,v,r ORDER BY v.id,ev.start_seconds,ev.id "
            "SKIP $offset LIMIT $limit",
            **params,
            offset=offset,
            limit=limit,
        )
        return dict(
            items=[self.evidence_row(r) for r in rows], total=total, limit=limit, offset=offset
        )

    def overview(self, session):
        videos = (
            session.scalar(
                select(func.count())
                .select_from(ContentItem)
                .where(ContentItem.item_type == "video_description")
            )
            or 0
        )
        counts = self.store.read(ACTIVE + "RETURN count(v) AS n", content_id=None)[0]["n"]
        entities = self.store.read(MENTIONS + "RETURN count(DISTINCT e) AS n", content_id=None)[0][
            "n"
        ]
        stats = self.store.read(
            EVIDENCE + "RETURN count(ev) AS evidence,count(DISTINCT r) AS relations",
            content_id=None,
        )[0]
        builds = {
            r["status"]: r["n"]
            for r in self.store.read("MATCH (b:VGBuild) RETURN b.status AS status,count(b) AS n")
        }
        current = self.store.read(
            "MATCH (v:VGVideo)-[:HAS_BUILD]->(b:VGBuild) "
            "WHERE v.desired_build_id=b.id AND NOT v.withdrawn "
            "AND NOT coalesce(v.deleted,false) RETURN b.status AS status,b.output_json AS output"
        )
        coverage = [decode(row["output"]).get("coverage", {}) for row in current]
        coverage = [c for c in coverage if c.get("applicable")]
        return dict(
            videos=videos,
            active_videos=counts,
            entities=entities,
            **stats,
            builds=builds,
            partial_videos=sum(r["status"] == "partial_success" for r in current),
            chunk_coverage={
                "videos": len(coverage),
                **{
                    k: sum(c.get(k, 0) for c in coverage)
                    for k in ("total_chunks", "passed_chunks", "skipped_chunks", "pending_chunks")
                },
            },
        )

    def video_status(self, session, content_id):
        material = load_material(session, content_id)
        if material is None:
            return None
        rows = self.store.read(
            "MATCH (v:VGVideo {id:$id}) "
            "OPTIONAL MATCH (a:VGBuild {id:v.active_build_id}) "
            "OPTIONAL MATCH (d:VGBuild {id:v.desired_build_id}) RETURN v,a,d",
            id=str(content_id),
        )
        v, a, d = (rows[0]["v"], rows[0]["a"] or {}, rows[0]["d"] or {}) if rows else ({}, {}, {})
        active, desired = decode(a.get("output_json")), decode(d.get("output_json"))
        return dict(
            content_item_id=str(content_id),
            withdrawn=bool(v.get("withdrawn")),
            status="withdrawn" if v.get("withdrawn") else d.get("status", "not_queued"),
            active_build_id=a.get("id"),
            source_changed=bool(a and a["fingerprint"] != material.fingerprint),
            assessment=desired.get("assessment") or active.get("assessment"),
            attempts=d.get("attempts", 0),
            error=d.get("error_message"),
            transcript_chunks=sum(c.evidence_type == "transcript_segment" for c in material.chunks),
            summary_only=bool(material.extraction_chunks)
            and all(c.evidence_type == "video_summary" for c in material.extraction_chunks),
            coverage=desired.get("coverage"),
            active_coverage=active.get("published_coverage"),
            skipped_chunks=list(desired.get("chunk_errors", {}).values()),
            projection_activated=desired.get("projection_activated", False),
            retained_active_build_id=desired.get("retained_active_build_id"),
        )

    def retrieve_context(self, session, request):
        cid = str(request.content_item_id) if request.content_item_id else None
        aliases = self.store.read(
            MENTIONS + "MATCH (a:VGAlias)-[:ALIAS_OF]->(e) "
            "WHERE $query CONTAINS a.normalized_alias "
            "RETURN DISTINCT a.normalized_alias AS alias,e.id AS id "
            "ORDER BY size(alias) DESC,id LIMIT 20",
            content_id=cid,
            query=normalized_name(request.query),
        )
        nodes, edges, truncated = {}, {}, False
        for eid in dict.fromkeys(row["id"] for row in aliases):
            graph = self.get_graph(
                session,
                entity_id=uuid.UUID(eid),
                content_id=request.content_item_id,
                hops=1,
                max_nodes=50,
                max_edges=100,
            )
            truncated |= graph.truncated
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
        rows = self.store.read(
            EVIDENCE + "WHERE r.id IN $ids RETURN ev,b,v,r "
            "ORDER BY ev.confidence DESC,ev.id LIMIT $limit",
            content_id=cid,
            ids=[e.id for e in edges.values() if e.kind == "relation"],
            limit=request.top_k * 5,
        )
        evidence, blocks, seen, used = [], [], set(), 0
        for row in rows:
            entry = self.evidence_row(row)
            key = (entry.content_item_id, entry.quote, entry.assertion, entry.relation_id)
            if key in seen:
                continue
            seen.add(key)
            block = (
                f"[{len(evidence)+1}] {entry.title or '视频'}\nURL: {entry.video_url}\n"
                f"陈述状态: {entry.assertion}（字幕陈述，未独立事实验证）\n原文: {entry.quote}"
            )
            if used + len(block) + 2 > request.max_chars:
                continue
            evidence.append(entry)
            blocks.append(block)
            used += len(block) + 2
            if len(evidence) >= request.top_k:
                break
        return GraphRetrieveResponse(
            graph=GraphData(
                nodes=list(nodes.values()), edges=list(edges.values()), truncated=truncated
            ),
            evidence=evidence,
            context="\n\n".join(blocks),
        )
