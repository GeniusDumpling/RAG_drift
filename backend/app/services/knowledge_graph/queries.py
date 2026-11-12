"""Query only active source revisions, preserving independent assertions and provenance."""

import json

from .schema import Publication, normalized
from .storage import projection


def sources(store, source_type=None):
    return store.read(
        "MATCH (s:KGSource) WHERE $type IS NULL OR s.source_type=$type "
        "OPTIONAL MATCH (b:KGBuild {id:s.active_build_id}) "
        "RETURN s.id AS id,s.source_type AS source_type,s.external_id AS external_id,"
        "s.title AS title,s.url AS url,s.withdrawn AS withdrawn,"
        "s.active_build_id AS active_build_id,b.native_build_id AS native_build_id "
        "ORDER BY s.id",
        type=source_type,
    )


def verify(store, source_id, expected_publication=None):
    rows = store.read(
        "MATCH(s:KGSource {id:$sid}) OPTIONAL MATCH(b:KGBuild {id:s.active_build_id}) "
        "RETURN s,b",
        sid=source_id,
    )
    if not rows or rows[0]["s"].get("withdrawn") or not rows[0].get("b"):
        return dict(verified=False, source_id=source_id, checks={"active": False})
    source, build = rows[0]["s"], rows[0]["b"]
    publication = Publication.model_validate_json(build["payload_json"])
    expected = projection(publication)
    bid = build["id"]
    checks = dict(
        binding=(
            publication.build_id == bid
            and publication.payload_hash == build["payload_hash"]
            and publication.source_id == source_id == build["source_id"]
            and publication.native_build_id == build["native_build_id"]
            and publication.schema_version == build["schema_version"]
            and publication.adapter_version == build["adapter_version"]
            and source["source_type"] == publication.source_type
            and source["external_id"] == publication.external_id
            and source["title"] == publication.title
            and source["url"] == publication.url
            and json.loads(source["metadata_json"]) == publication.metadata
        )
    )
    if expected_publication is not None:
        checks["native_publication"] = expected_publication.payload_hash == publication.payload_hash
    linked = store.read(
        "MATCH(:KGSource {id:$sid})-[:KG_HAS_BUILD]->(b:KGBuild {id:$bid}) RETURN b.id AS id",
        sid=source_id,
        bid=bid,
    )
    checks["source_build_link"] = len(linked) == 1
    entities = store.read(
        "MATCH(:KGBuild {id:$bid})-[:KG_HAS_ENTITY]->(e:KGEntity) RETURN e",
        bid=bid,
    )
    actual_entities = {r["e"]["id"]: dict(r["e"]) for r in entities}
    expected_entities = {
        k: {p: v for p, v in value.items() if p != "name"}
        for k, value in expected["entities"].items()
    }
    checks["entity_names"] = all(
        normalized(v["name"]) == v["normalized_name"] for v in actual_entities.values()
    )
    checks["entities"] = {
        k: {p: v for p, v in value.items() if p != "name"} for k, value in actual_entities.items()
    } == expected_entities
    facts = store.read(
        "MATCH(:KGBuild {id:$bid})-[:KG_HAS_FACT]->(f:KGFact) "
        "OPTIONAL MATCH(f)-[:KG_SUBJECT]->(s:KGEntity) "
        "OPTIONAL MATCH(f)-[:KG_OBJECT]->(o:KGEntity) "
        "RETURN f,collect(DISTINCT s.id) AS subjects,collect(DISTINCT o.id) AS objects",
        bid=bid,
    )
    actual_facts = {}
    for row in facts:
        fact = dict(row["f"])
        fact["subject"] = row["subjects"][0] if len(row["subjects"]) == 1 else row["subjects"]
        fact["object"] = (
            row["objects"][0]
            if len(row["objects"]) == 1
            else (None if not row["objects"] else row["objects"])
        )
        actual_facts[fact["id"]] = fact
    checks["facts_and_endpoints"] = actual_facts == expected["facts"]
    mentions = store.read(
        "MATCH(:KGBuild {id:$bid})-[:KG_HAS_MENTION]->(m:KGMention) "
        "OPTIONAL MATCH(m)-[:KG_REFERS_TO]->(e:KGEntity) "
        "RETURN m,collect(DISTINCT e.id) AS targets",
        bid=bid,
    )
    actual_mentions = {
        r["m"]["id"]: {
            **dict(r["m"]),
            "entity": r["targets"][0] if len(r["targets"]) == 1 else r["targets"],
        }
        for r in mentions
    }
    checks["mentions"] = actual_mentions == expected["mentions"]
    evidence = store.read(
        "MATCH(:KGBuild {id:$bid})-[:KG_HAS_EVIDENCE]->(e:KGEvidence) "
        "OPTIONAL MATCH(e)-[:KG_SUPPORTS]->(f:KGFact) "
        "RETURN e,collect(DISTINCT f.id) AS targets",
        bid=bid,
    )
    actual_evidence = {
        r["e"]["id"]: {
            **dict(r["e"]),
            "fact": r["targets"][0] if len(r["targets"]) == 1 else r["targets"],
        }
        for r in evidence
    }
    checks["evidence"] = actual_evidence == expected["evidence"]
    unchanged = store.read("MATCH(s:KGSource {id:$id}) RETURN s", id=source_id)
    checks["unchanged"] = bool(
        unchanged
        and not unchanged[0]["s"].get("withdrawn")
        and unchanged[0]["s"].get("active_build_id") == bid
    )
    return dict(
        verified=all(checks.values()),
        source_id=source_id,
        build_id=bid,
        checks=checks,
        counts={k: len(v) for k, v in expected.items()},
    )


ACTIVE = (
    "MATCH (src:KGSource)-[:KG_HAS_BUILD]->(b:KGBuild)"
    "-[:KG_HAS_EVIDENCE]->(ev:KGEvidence)-[:KG_SUPPORTS]->(f:KGFact) "
    "WHERE src.active_build_id=b.id AND NOT src.withdrawn "
    "AND ($type IS NULL OR src.source_type=$type) "
    "AND ($sid IS NULL OR src.id=$sid) "
)


def retrieve(
    store,
    query="",
    *,
    source_type=None,
    source_id=None,
    entity_id=None,
    predicate=None,
    limit=20,
    offset=0,
):
    if not 1 <= limit <= 100 or offset < 0:
        raise ValueError("limit 须为 1 到 100，offset 不能为负")
    selected = [
        s
        for s in sources(store, source_type)
        if s["active_build_id"]
        and not s["withdrawn"]
        and (source_id is None or source_id == s["id"])
    ]
    checks = [verify(store, s["id"]) for s in selected]
    if any(not c["verified"] for c in checks):
        raise ValueError("统一图谱回读校验失败，拒绝返回失配证据")
    params = dict(
        type=source_type,
        sid=source_id,
        q=normalized(query),
        eid=entity_id,
        predicate=predicate,
        limit=limit,
        offset=offset,
    )
    rows = store.read(
        ACTIVE + "MATCH(f)-[:KG_SUBJECT]->(s:KGEntity) "
        "OPTIONAL MATCH(f)-[:KG_OBJECT]->(o:KGEntity) "
        "WITH src,b,ev,f,s,o WHERE ($eid IS NULL OR s.id=$eid OR o.id=$eid) "
        "AND ($predicate IS NULL OR f.predicate=$predicate) AND ($q='' OR "
        "s.normalized_name CONTAINS $q OR o.normalized_name CONTAINS $q "
        "OR f.search_text CONTAINS $q OR ev.search_text CONTAINS $q OR EXISTS {"
        "MATCH(b)-[:KG_HAS_MENTION]->(m:KGMention)-[:KG_REFERS_TO]->(s) "
        "WHERE toLower(m.data_json) CONTAINS $q}) "
        "WITH DISTINCT f,s,o ORDER BY f.id SKIP $offset LIMIT $limit "
        "MATCH(src:KGSource)-[:KG_HAS_BUILD]->(b:KGBuild)-[:KG_HAS_EVIDENCE]->(ev:KGEvidence)"
        "-[:KG_SUPPORTS]->(f) WHERE src.active_build_id=b.id AND NOT src.withdrawn "
        "AND ($type IS NULL OR src.source_type=$type) AND ($sid IS NULL OR src.id=$sid) "
        "RETURN f,s,o,src,b.native_build_id AS native_build_id,collect(ev) AS evidence "
        "ORDER BY f.id,src.id",
        **params,
    )
    items = {}
    for row in rows:
        f = row["f"]
        item = items.setdefault(
            f["id"],
            dict(
                id=f["id"],
                **json.loads(f["data_json"]),
                subject_entity=dict(row["s"]),
                object_entity=dict(row["o"]) if row["o"] else None,
                sources=[],
            ),
        )
        src = row["src"]
        item["sources"].append(
            dict(
                id=src["id"],
                source_type=src["source_type"],
                external_id=src["external_id"],
                title=src["title"],
                url=src["url"],
                native_build_id=row["native_build_id"],
                metadata=json.loads(src["metadata_json"]),
                evidence=[dict(id=e["id"], **json.loads(e["data_json"])) for e in row["evidence"]],
            )
        )
    # Detect a concurrent withdraw/republication across every selected source.
    current = {s["id"]: s for s in sources(store, source_type)}
    if {
        s["id"]
        for s in current.values()
        if s["active_build_id"]
        and not s["withdrawn"]
        and (source_id is None or source_id == s["id"])
    } != {s["id"] for s in selected}:
        raise ValueError("查询期间来源集合已变化")
    if any(current[s["id"]]["active_build_id"] != s["active_build_id"] for s in selected):
        raise ValueError("查询期间有效构建已变化")
    return dict(items=list(items.values()), limit=limit, offset=offset)
