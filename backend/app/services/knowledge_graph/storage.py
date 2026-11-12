"""Shared graph projection. Native publishers call publish inside their own transaction."""

from app.services.graph_storage import Neo4jStore

from .schema import Publication, digest, encode, normalized, source_key

LABELS = ("KGSource", "KGBuild", "KGEntity", "KGFact", "KGEvidence", "KGMention")


def initialize(driver, database):
    with driver.session(database=database) as session:
        for label in LABELS:
            session.run(
                f"CREATE CONSTRAINT {label.lower()}_id IF NOT EXISTS "
                f"FOR (n:{label}) REQUIRE n.id IS UNIQUE"
            ).consume()


def projection(publication):
    """Deterministic immutable rows used for writing and independent graph readback."""
    p = publication
    entities, facts, mentions, evidence = {}, {}, {}, {}
    for entity in p.entities:
        entities[entity.id] = dict(
            id=entity.id,
            name=entity.name,
            normalized_name=normalized(entity.name),
            entity_type=entity.entity_type,
            scope=entity.scope,
        )
        for index, item in enumerate(entity.mentions):
            mid = digest([p.build_id, entity.id, index, item.model_dump()])
            mentions[mid] = dict(id=mid, entity=entity.id, data_json=encode(item.model_dump()))
    for index, fact in enumerate(p.facts):
        props = fact.model_dump(exclude={"evidence"})
        facts[fact.id] = dict(
            id=fact.id,
            subject=fact.subject,
            object=fact.object,
            predicate=fact.predicate,
            kind=fact.kind,
            assertion=fact.assertion,
            data_json=encode(props),
            search_text=normalized(encode(props)),
        )
        for number, item in enumerate(fact.evidence):
            eid = digest([p.build_id, index, fact.id, number, item.model_dump()])
            evidence[eid] = dict(
                id=eid,
                fact=fact.id,
                data_json=encode(item.model_dump()),
                search_text=normalized(item.quote),
            )
    return dict(entities=entities, facts=facts, mentions=mentions, evidence=evidence)


def publish(tx, publication):
    # Revalidate nested model instances too: a caller may have mutated a list
    # or used model_copy, which deliberately bypasses Pydantic field validation.
    raw = publication.model_dump() if isinstance(publication, Publication) else publication
    p = Publication.model_validate(raw)
    rows = projection(p)
    tx.run(
        "MERGE (s:KGSource {id:$id}) ON CREATE SET s.withdrawn=false "
        "SET s.lock_version=coalesce(s.lock_version,0)+1, s.source_type=$type,"
        "s.external_id=$external,s.title=$title,s.url=$url,s.metadata_json=$metadata",
        id=p.source_id,
        type=p.source_type,
        external=p.external_id,
        title=p.title,
        url=p.url,
        metadata=encode(p.metadata),
    ).consume()
    tx.run(
        "MATCH (s:KGSource {id:$sid}) MERGE (b:KGBuild {id:$id}) "
        "ON CREATE SET b.source_id=$sid,b.native_build_id=$native,b.payload_hash=$hash,"
        "b.payload_json=$payload,b.schema_version=$schema,b.adapter_version=$adapter "
        "MERGE (s)-[:KG_HAS_BUILD]->(b)",
        sid=p.source_id,
        id=p.build_id,
        native=p.native_build_id,
        hash=p.payload_hash,
        payload=encode(p.model_dump()),
        schema=p.schema_version,
        adapter=p.adapter_version,
    ).consume()
    count = tx.run(
        "UNWIND $rows AS row MERGE (e:KGEntity {id:row.id}) ON CREATE SET e.name=row.name,"
        "e.normalized_name=row.normalized_name,e.entity_type=row.entity_type,e.scope=row.scope "
        "WITH e,row WHERE e.normalized_name=row.normalized_name AND "
        "e.entity_type=row.entity_type AND e.scope=row.scope "
        "MATCH (b:KGBuild {id:$bid}) MERGE (b)-[:KG_HAS_ENTITY]->(e) RETURN count(*) AS n",
        rows=list(rows["entities"].values()),
        bid=p.build_id,
    ).single()["n"]
    if count != len(rows["entities"]):
        raise ValueError("统一实体 ID 冲突，拒绝发布")
    tx.run(
        "UNWIND $rows AS row MERGE (f:KGFact {id:row.id}) ON CREATE SET f.data_json=row.data_json,"
        "f.predicate=row.predicate,f.kind=row.kind,f.assertion=row.assertion,"
        "f.search_text=row.search_text "
        "WITH f,row MATCH (b:KGBuild {id:$bid}),(s:KGEntity {id:row.subject}) "
        "MERGE (b)-[:KG_HAS_FACT]->(f) MERGE (f)-[:KG_SUBJECT]->(s) "
        "WITH f,row OPTIONAL MATCH (o:KGEntity {id:row.object}) "
        "FOREACH (_ IN CASE WHEN o IS NULL THEN [] ELSE [1] END | MERGE (f)-[:KG_OBJECT]->(o))",
        rows=list(rows["facts"].values()),
        bid=p.build_id,
    ).consume()
    tx.run(
        "UNWIND $rows AS row MATCH (b:KGBuild {id:$bid}),(e:KGEntity {id:row.entity}) "
        "MERGE (m:KGMention {id:row.id}) ON CREATE SET m.data_json=row.data_json "
        "MERGE (b)-[:KG_HAS_MENTION]->(m) MERGE (m)-[:KG_REFERS_TO]->(e)",
        rows=list(rows["mentions"].values()),
        bid=p.build_id,
    ).consume()
    tx.run(
        "UNWIND $rows AS row MATCH (b:KGBuild {id:$bid}),(f:KGFact {id:row.fact}) "
        "MERGE (e:KGEvidence {id:row.id}) ON CREATE SET e.data_json=row.data_json,"
        "e.search_text=row.search_text MERGE (b)-[:KG_HAS_EVIDENCE]->(e) "
        "MERGE (e)-[:KG_SUPPORTS]->(f)",
        rows=list(rows["evidence"].values()),
        bid=p.build_id,
    ).consume()
    tx.run(
        "MATCH (s:KGSource {id:$sid}) SET s.active_build_id=$bid,s.withdrawn=false",
        sid=p.source_id,
        bid=p.build_id,
    ).consume()
    return p.build_id


def set_visibility(tx, source_type, external_id, *, withdrawn, clear_active=False):
    tx.run(
        "MATCH (s:KGSource {id:$id}) SET s.withdrawn=$withdrawn, "
        "s.active_build_id=CASE WHEN $clear THEN null ELSE s.active_build_id END",
        id=source_key(source_type, str(external_id)),
        withdrawn=withdrawn,
        clear=clear_active,
    ).consume()


class UnifiedGraphStore(Neo4jStore):
    def __init__(self, settings):
        super().__init__(settings, config_prefix="unified_graph_neo4j")

    def initialize(self):
        initialize(self.driver, self.database)
