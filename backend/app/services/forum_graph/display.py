"""Forum canvas adapter over verified shared facts, retaining per-floor evidence."""

from collections import Counter, defaultdict

from app.schemas.video_graph import GraphData, GraphEdge, GraphNode
from app.services.knowledge_graph.schema import Publication, digest, normalized

from .queries import verify


class ForumDisplay:
    def __init__(self, store, session):
        self.store, self.session = store, session
        self.known = store.known()
        self.entities, self.facts = {}, {}
        self.active_topics = self.unavailable_topics = 0
        rows = store.read(
            "MATCH(s:KGSource {source_type:'forum'}),(b:KGBuild {id:s.active_build_id}) "
            "WHERE NOT s.withdrawn RETURN s.id AS sid,b.payload_json AS payload"
        )
        for row in rows:
            try:
                p = Publication.model_validate_json(row["payload"])
                check = verify(store, session, row["sid"])
                if not check["verified"] or check.get("build_id") != p.native_build_id:
                    raise ValueError("source changed")
            except ValueError:
                self.unavailable_topics += 1
                continue
            self.active_topics += 1
            for entity in p.entities:
                self.entities[entity.id] = entity
            for fact in p.facts:
                item = self.facts.setdefault(fact.id, dict(fact=fact, evidence=[]))
                for index, proof in enumerate(fact.evidence):
                    loc = proof.locator
                    item["evidence"].append(
                        dict(
                            id=digest([p.build_id, fact.id, index]),
                            fact_id=fact.id,
                            content_item_id=p.metadata["content_item_id"],
                            title=p.title,
                            quote=proof.quote,
                            post_url=loc["url"],
                            post_id=loc["post_id"],
                            post_number=loc["post_number"],
                            author=loc["author"],
                            role=loc.get("source_role", "claim"),
                            act=loc.get("act"),
                            assertion=fact.assertion,
                            qualifiers=fact.qualifiers,
                            evaluations=loc.get("evaluations", []),
                            accepted_answer=loc.get("accepted_answer", False),
                        )
                    )

    def overview(self):
        return dict(
            topics=len(self.known),
            active_topics=self.active_topics,
            unavailable_topics=self.unavailable_topics,
            entities=len(self.entities),
            facts=len(self.facts),
            evidence=sum(len(f["evidence"]) for f in self.facts.values()),
            builds=dict(Counter(r["status"] for r in self.known)),
        )

    def list_entities(self, q="", entity_type=None, limit=50, offset=0):
        selected = sorted(
            (
                e
                for e in self.entities.values()
                if (not q or normalized(q) in normalized(e.name))
                and (not entity_type or e.entity_type == entity_type)
            ),
            key=lambda e: (normalized(e.name), e.id),
        )
        return dict(
            items=[
                dict(id=e.id, name=e.name, entity_type=e.entity_type)
                for e in selected[offset : offset + limit]
            ],
            total=len(selected),
            limit=limit,
            offset=offset,
        )

    def graph(
        self,
        *,
        q="",
        entity_id=None,
        entity_type=None,
        hops=1,
        max_nodes=300,
        max_edges=500,
        full=False,
        min_component_size=0,
    ):
        nodes = {
            e.id: GraphNode(id=e.id, label=e.name, kind="entity", entity_type=e.entity_type)
            for e in self.entities.values()
        }
        edges = []
        for fid, item in sorted(self.facts.items()):
            fact = item["fact"]
            target = fact.object
            if target is None:
                target = "fact:" + fid
                nodes[target] = GraphNode(
                    id=target, label=fact.value or "", kind="fact", entity_type=fact.kind
                )
            edges.append(
                GraphEdge(
                    id=fid,
                    source=fact.subject,
                    target=target,
                    predicate=fact.predicate,
                    kind="relation",
                    evidence_count=len(item["evidence"]),
                    assertions={fact.assertion: len(item["evidence"])},
                    qualifiers={k: str(v) for k, v in fact.qualifiers.items()},
                )
            )
        if q or entity_type:
            selected = {
                n.id
                for n in nodes.values()
                if (not q or normalized(q) in normalized(n.label))
                and (not entity_type or n.entity_type == entity_type)
            }
            edges = [e for e in edges if e.source in selected or e.target in selected]
            retained = selected | {n for e in edges for n in (e.source, e.target)}
            nodes = {k: v for k, v in nodes.items() if k in retained}
        if entity_id:
            selected, frontier = {entity_id}, {entity_id}
            for _ in range(hops):
                frontier = {
                    n
                    for e in edges
                    if e.source in frontier or e.target in frontier
                    for n in (e.source, e.target)
                } - selected
                selected |= frontier
            nodes = {k: v for k, v in nodes.items() if k in selected}
            edges = [e for e in edges if e.source in nodes and e.target in nodes]
        if min_component_size:
            neighbors = defaultdict(set)
            for e in edges:
                neighbors[e.source].add(e.target)
                neighbors[e.target].add(e.source)
            remaining, retained = set(nodes), set()
            while remaining:
                component, frontier = set(), {remaining.pop()}
                while frontier:
                    component |= frontier
                    frontier = {n for k in frontier for n in neighbors[k]} - component
                remaining -= component
                if len(component) >= min_component_size:
                    retained |= component
            nodes = {k: v for k, v in nodes.items() if k in retained}
            edges = [e for e in edges if e.source in nodes and e.target in nodes]
        total_nodes, total_edges = len(nodes), len(edges)
        if not full:
            selected, bounded = {}, []
            for edge in edges:
                ids = {edge.source, edge.target}
                if len(selected.keys() | ids) <= max_nodes and len(bounded) < max_edges:
                    selected.update({k: nodes[k] for k in ids})
                    bounded.append(edge)
            for key in sorted(nodes):
                if len(selected) >= max_nodes:
                    break
                selected.setdefault(key, nodes[key])
            nodes, edges = selected, bounded
        return GraphData(
            nodes=list(nodes.values()),
            edges=edges,
            truncated=len(nodes) < total_nodes or len(edges) < total_edges,
        )

    def evidence(self, fact_id, *, limit=20, offset=0):
        rows = self.facts.get(fact_id, {}).get("evidence", [])
        return dict(
            items=rows[offset : offset + limit], total=len(rows), limit=limit, offset=offset
        )
