#!/usr/bin/env python3
"""Export a consistent PostgreSQL snapshot, import into an empty Neo4j graph, verify it."""

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))

import app.models  # noqa: E402,F401
from app.core.config import get_settings  # noqa: E402
from app.models.content import ContentItem  # noqa: E402
from app.models.search import ContentEntityMention, Entity  # noqa: E402
from app.models.video_graph import (  # noqa: E402
    VideoGraphAlias,
    VideoGraphBuild,
    VideoGraphEvidence,
    VideoGraphRelation,
    VideoGraphState,
)
from app.services.video_graph import MENTION_METHOD, digest  # noqa: E402
from sqlalchemy import create_engine, select  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402


def row_dict(row):
    result = {}
    for c in row.__table__.columns:
        value = getattr(row, c.name)
        if isinstance(value, datetime):
            value = int(value.timestamp() * 1000)
        elif value is not None and not isinstance(value, str | int | float | bool | dict | list):
            value = str(value)
        result[c.name] = value
    return result


def export_snapshot(settings, path):
    engine = create_engine(settings.sync_database_url, isolation_level="REPEATABLE READ")
    try:
        with Session(engine) as session, session.begin():
            states = session.scalars(select(VideoGraphState)).all()
            builds = session.scalars(select(VideoGraphBuild)).all()
            if any(b.status == "running" for b in builds):
                raise ValueError(
                    "存在 running 构图任务，请先停止构图 worker 并等待完成或租约恢复后再导出"
                )
            videos = session.scalars(
                select(ContentItem).where(ContentItem.id.in_([s.content_item_id for s in states]))
            ).all()
            mentions = session.scalars(
                select(ContentEntityMention).where(
                    ContentEntityMention.extraction_method == MENTION_METHOD
                )
            ).all()
            relations = session.scalars(select(VideoGraphRelation)).all()
            aliases = session.scalars(select(VideoGraphAlias)).all()
            ids = {m.entity_id for m in mentions} | {a.entity_id for a in aliases}
            ids |= {r.subject_id for r in relations} | {r.object_id for r in relations}
            entities = session.scalars(select(Entity).where(Entity.id.in_(ids))).all()
            evidence = session.scalars(select(VideoGraphEvidence)).all()
            result = {
                k: [row_dict(r) for r in rows]
                for k, rows in dict(
                    states=states,
                    builds=builds,
                    videos=videos,
                    mentions=mentions,
                    relations=relations,
                    aliases=aliases,
                    entities=entities,
                    evidence=evidence,
                ).items()
            }
        result = {"format": 1, "tables": result}
        result["snapshot_id"] = digest(result)
        # Exclusive creation protects earlier snapshots against accidental overwrite.
        with path.open("x") as file:
            json.dump(result, file, ensure_ascii=False, sort_keys=True)
        return result
    finally:
        engine.dispose()


def records(snapshot):
    t = snapshot["tables"]
    videos = {v["id"]: v for v in t["videos"]}
    nodes = {
        k: []
        for k in (
            "VGVideo",
            "VGBuild",
            "VGEntity",
            "VGAlias",
            "VGRelation",
            "VGEvidence",
            "VGMention",
        )
    }
    for state in t["states"]:
        video = videos[state["content_item_id"]]
        nodes["VGVideo"].append(
            dict(
                state,
                id=state["content_item_id"],
                title=video.get("title"),
                url=video["canonical_url"],
                deleted=False,
            )
        )
    for build in t["builds"]:
        nodes["VGBuild"].append(
            dict(
                build,
                build_key=digest(
                    [build["content_item_id"], build["fingerprint"], build["extractor_version"]]
                ),
                lease_token=None,
                lease_until=None,
            )
        )
    nodes["VGEntity"] = t["entities"]
    for alias in t["aliases"]:
        # IDs are derived from type/name for future MERGE; retain the original UUID too.
        nodes["VGAlias"].append(
            dict(
                alias,
                legacy_id=alias["id"],
                id=digest([alias["entity_type"], alias["normalized_alias"]]),
            )
        )
    nodes["VGRelation"] = t["relations"]
    nodes["VGEvidence"] = t["evidence"]
    for mention in t["mentions"]:
        meta = mention.get("metadata_json") or {}
        bid = meta.get("video_graph_build_id")
        if not bid:
            raise ValueError("图谱实体提及缺少构图版本，无法安全迁移")
        nodes["VGMention"].append(
            dict(mention, build_id=bid, evidence_type=meta.get("evidence_type"))
        )
    return {
        label: [
            {
                k: json.dumps(v, ensure_ascii=False, sort_keys=True)
                if isinstance(v, dict | list)
                else v
                for k, v in row.items()
                if v is not None
            }
            for row in rows
        ]
        for label, rows in nodes.items()
    }


LINKS = [
    ("VGVideo", "VGBuild", "HAS_BUILD", "id", "content_item_id"),
    ("VGAlias", "VGEntity", "ALIAS_OF", "entity_id", "id"),
    ("VGRelation", "VGEntity", "SUBJECT", "subject_id", "id"),
    ("VGRelation", "VGEntity", "OBJECT", "object_id", "id"),
    ("VGBuild", "VGEvidence", "HAS_EVIDENCE", "id", "build_id"),
    ("VGEvidence", "VGRelation", "SUPPORTS", "relation_id", "id"),
    ("VGBuild", "VGMention", "HAS_MENTION", "id", "build_id"),
    ("VGMention", "VGEntity", "REFERS_TO", "entity_id", "id"),
]


def import_snapshot(store, snapshot):
    expected = records(snapshot)
    store.initialize()
    with store.transaction() as tx:
        # Single lock prevents two importers accepting different snapshots concurrently.
        tx.run(
            'MERGE (m:VGMigration {id:"postgres-import"}) '
            "SET m.lock_version=coalesce(m.lock_version,0)+1"
        ).consume()
        meta = tx.run('MATCH (m:VGMigration {id:"postgres-import"}) RETURN m').single()["m"]
        if meta.get("snapshot_id") not in (None, snapshot["snapshot_id"]):
            raise ValueError("目标图谱已有不同快照；请使用独立的空 Neo4j 数据库")
        if meta.get("complete"):
            return verify(store, snapshot)
        if not meta.get("snapshot_id"):
            for label in expected:
                if tx.run(f"MATCH (n:{label}) RETURN count(n) AS n").single()["n"]:
                    raise ValueError("首次导入要求目标 VG 图谱为空；不会覆盖现有图谱")
        tx.run(
            'MATCH (m:VGMigration {id:"postgres-import"}) SET m.snapshot_id=$id,m.complete=false',
            id=snapshot["snapshot_id"],
        ).consume()
    for label, rows in expected.items():
        for start in range(0, len(rows), 500):
            with store.transaction() as tx:
                tx.run(
                    f"UNWIND $rows AS row MERGE (n:{label} {{id:row.id}}) SET n=row",
                    rows=rows[start : start + 500],
                ).consume()
    with store.transaction() as tx:
        for source, target, relation, left, right in LINKS:
            tx.run(
                f"MATCH (s:{source}),(t:{target}) WHERE s.{left}=t.{right} "
                f"MERGE (s)-[:{relation}]->(t)"
            ).consume()
        tx.run(
            "MATCH (r:VGRelation)-[:SUBJECT]->(s:VGEntity), "
            "(r)-[:OBJECT]->(o:VGEntity) MERGE (s)-[f:VG_FACT {id:r.id}]->(o) "
            "SET f.predicate=r.predicate"
        ).consume()
    result = verify(store, snapshot)
    with store.transaction() as tx:
        tx.run('MATCH (m:VGMigration {id:"postgres-import"}) SET m.complete=true').consume()
    return result


def verify(store, snapshot):
    expected = records(snapshot)
    report = {}
    for label, rows in expected.items():
        actual = store.read(f"MATCH (n:{label}) RETURN properties(n) AS props")
        actual = {r["props"]["id"]: r["props"] for r in actual}
        wanted = {r["id"]: r for r in rows}
        if actual != wanted:
            raise ValueError(f"{label} 数据校验不一致（源 {len(wanted)}，目标 {len(actual)}）")
        report[label] = len(rows)
    for source, target, relation, left, right in LINKS:
        actual = store.read(
            f"MATCH (s:{source})-[r:{relation}]->(t:{target}) "
            f"RETURN s.id AS source,t.id AS target,s.{left}=t.{right} AS valid"
        )
        targets = {}
        for row in expected[target]:
            targets.setdefault(row[right], []).append(row["id"])
        wanted = {
            (row["id"], target_id)
            for row in expected[source]
            for target_id in targets.get(row.get(left), [])
        }
        if (
            len(actual) != len(wanted)
            or {(r["source"], r["target"]) for r in actual} != wanted
            or not all(r["valid"] for r in actual)
        ):
            raise ValueError(f"{relation} 连接校验不一致")
        report[relation] = len(actual)
    facts = store.read(
        "MATCH (s:VGEntity)-[f:VG_FACT]->(o:VGEntity) "
        "RETURN s.id AS s,o.id AS o,f.id AS id,f.predicate AS p"
    )
    if {tuple(r[k] for k in ("id", "s", "o", "p")) for r in facts} != {
        (r["id"], r["subject_id"], r["object_id"], r["predicate"]) for r in expected["VGRelation"]
    }:
        raise ValueError("VG_FACT 关系校验不一致")
    return dict(snapshot_id=snapshot["snapshot_id"], verified=True, counts=report)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["export", "import", "verify"])
    parser.add_argument("--snapshot", type=Path, required=True)
    args = parser.parse_args()
    settings = get_settings()
    if args.command == "export":
        snapshot = export_snapshot(settings, args.snapshot)
        print(
            json.dumps(
                {
                    "snapshot_id": snapshot["snapshot_id"],
                    "counts": {k: len(v) for k, v in snapshot["tables"].items()},
                }
            )
        )
        return
    snapshot = json.loads(args.snapshot.read_text())
    if snapshot.get("format") != 1 or snapshot["snapshot_id"] != digest(
        {k: v for k, v in snapshot.items() if k != "snapshot_id"}
    ):
        raise ValueError("快照格式或校验和不正确")
    from app.services.neo4j_graph.store import GraphStore

    with GraphStore(settings) as store:
        result = (
            import_snapshot(store, snapshot)
            if args.command == "import"
            else verify(store, snapshot)
        )
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
