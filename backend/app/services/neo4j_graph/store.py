"""Transactional graph and job storage. All mutations lock the owning video first."""

import json
import uuid
from contextlib import contextmanager
from datetime import UTC, datetime

from neo4j import GraphDatabase

from app.core.config import Settings
from app.schemas.video_graph import ChunkExtraction
from app.services.video_graph import (
    ENTITY_NAMESPACE,
    LEASE_SECONDS,
    LeaseLostError,
    canonical_name,
    chunk_fingerprint,
    digest,
    locate_quote,
    normalized_name,
    update_coverage,
    validate_extraction,
)

LABELS = ("VGVideo", "VGBuild", "VGEntity", "VGAlias", "VGRelation", "VGEvidence", "VGMention")


def encode(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)


def decode(value):
    return json.loads(value or "{}")


def now_ms():
    return int(datetime.now(UTC).timestamp() * 1000)


class GraphStore:
    def __init__(self, settings: Settings):
        if not settings.neo4j_password:
            raise ValueError("请配置 NEO4J_PASSWORD")
        self.driver = GraphDatabase.driver(
            settings.neo4j_uri,
            auth=(settings.neo4j_user, settings.neo4j_password),
            connection_timeout=10,
            max_transaction_retry_time=15,
        )
        self.database = settings.neo4j_database

    def close(self):
        self.driver.close()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()

    @contextmanager
    def transaction(self):
        with self.driver.session(database=self.database) as session:
            with session.begin_transaction(timeout=120) as tx:
                yield tx
                tx.commit()

    def read(self, cypher, **params):
        with self.driver.session(database=self.database) as session:
            return session.execute_read(lambda tx: tx.run(cypher, parameters=params).data())

    def ensure_ready(self):
        rows = self.read('MATCH (m:VGMigration {id:"postgres-import"}) RETURN m.complete AS ready')
        if rows and not rows[0]["ready"]:
            raise ValueError("Neo4j 历史导入尚未完成，请先完成导入和校验")

    def initialize(self):
        with self.driver.session(database=self.database) as session:
            for label in (*LABELS, "VGMigration"):
                session.run(
                    f"CREATE CONSTRAINT {label.lower()}_id IF NOT EXISTS "
                    f"FOR (n:{label}) REQUIRE n.id IS UNIQUE"
                ).consume()
            session.run(
                "CREATE CONSTRAINT vgbuild_key IF NOT EXISTS "
                "FOR (n:VGBuild) REQUIRE n.build_key IS UNIQUE"
            ).consume()
            session.run(
                "CREATE INDEX vgbuild_queue IF NOT EXISTS "
                "FOR (n:VGBuild) ON (n.status, n.lease_until)"
            ).consume()

    @staticmethod
    def lock(tx, video_id):
        # Dependent SET takes a write lock before reading state. All workers use this order.
        row = tx.run(
            "MATCH (v:VGVideo {id:$id}) "
            "SET v.lock_version=coalesce(v.lock_version,0)+1 RETURN v",
            id=str(video_id),
        ).single()
        if row is None:
            raise LeaseLostError("视频或任务已删除")
        return dict(row["v"])

    @staticmethod
    def owned(tx, build_id, token):
        row = tx.run(
            "MATCH (b:VGBuild {id:$id}) "
            'WHERE b.status="running" AND b.lease_token=$token RETURN b',
            id=str(build_id),
            token=str(token),
        ).single()
        if row is None:
            raise LeaseLostError("任务租约已被其他 worker 接管")
        return dict(row["b"])

    def enqueue(self, material, version, retry_failed=False, retry_partial=False):
        vid = str(material.id)
        key = digest([vid, material.fingerprint, version])
        with self.transaction() as tx:
            tx.run("MERGE (v:VGVideo {id:$id}) ON CREATE SET v.withdrawn=false", id=vid).consume()
            state = self.lock(tx, vid)
            tx.run(
                "MATCH (v:VGVideo {id:$id}) SET v.title=$title, v.url=$url, "
                "v.source_key=$source, v.deleted=false",
                id=vid,
                title=material.title,
                url=material.url,
                source=material.source_key,
            ).consume()
            if state.get("withdrawn"):
                return "withdrawn"
            row = tx.run(
                "MATCH (v:VGVideo {id:$vid}) "
                "MERGE (b:VGBuild {build_key:$key}) "
                "ON CREATE SET b.id=$id, b.content_item_id=$vid, b.fingerprint=$fp, "
                'b.extractor_version=$version, b.status="pending", b.attempts=0, '
                'b.output_json="{}", b.created_at=$now '
                "MERGE (v)-[:HAS_BUILD]->(b) RETURN b",
                vid=vid,
                key=key,
                id=str(uuid.uuid4()),
                fp=material.fingerprint,
                version=version,
                now=now_ms(),
            ).single()
            build = dict(row["b"])
            if state.get("desired_build_id") != build["id"]:
                tx.run(
                    'MATCH (b:VGBuild {id:$id}) WHERE b.status IN ["pending","running"] '
                    'SET b.status="obsolete", b.lease_token=null, b.lease_until=null',
                    id=state.get("desired_build_id"),
                ).consume()
            status = build["status"]
            if (
                status == "obsolete"
                or (retry_failed and status in ("failed", "partial_success"))
                or (retry_partial and status == "partial_success")
                or (status == "partial_success" and not state.get("active_build_id"))
                or (status == "success" and state.get("active_build_id") != build["id"])
            ):
                status = "pending"
            output = decode(build.get("output_json"))
            if status == "pending" and state.get("active_build_id") != build["id"]:
                active = tx.run(
                    "MATCH (b:VGBuild {id:$id}) RETURN b", id=state.get("active_build_id")
                ).single()
                if active and active["b"]["extractor_version"] == version:
                    previous = decode(active["b"].get("output_json"))
                    signatures = {
                        sig: cid
                        for cid, sig in previous.get("chunk_fingerprints", {}).items()
                        if cid in previous.get("chunks", {})
                    }
                    for chunk in material.extraction_chunks:
                        cid, sig = str(chunk.id), chunk_fingerprint(chunk)
                        old = signatures.get(sig)
                        if old is None or cid in output.get("chunks", {}):
                            continue
                        try:
                            result = validate_extraction(
                                chunk, ChunkExtraction.model_validate(previous["chunks"][old])
                            )
                        except ValueError:
                            continue
                        output.setdefault("chunks", {})[cid] = result.model_dump()
                        output.setdefault("chunk_fingerprints", {})[cid] = sig
                        output.setdefault("reused_chunks", {})[cid] = {
                            "build_id": state["active_build_id"],
                            "chunk_id": old,
                        }
            tx.run(
                "MATCH (v:VGVideo {id:$vid}), (b:VGBuild {id:$bid}) "
                "SET v.desired_build_id=b.id, b.status=$status, b.output_json=$output",
                vid=vid,
                bid=build["id"],
                status=status,
                output=encode(output),
            ).consume()
            if status == "pending":
                tx.run(
                    "MATCH (b:VGBuild {id:$id}) SET b.error_message=null, "
                    "b.lease_token=null, b.lease_until=null",
                    id=build["id"],
                ).consume()
            return "queued" if status == "pending" else "unchanged"

    def claim(self, version, content_ids=None):
        ids = [str(v) for v in content_ids] if content_ids else None
        candidates = self.read(
            "MATCH (v:VGVideo)-[:HAS_BUILD]->(b:VGBuild) "
            "WHERE v.desired_build_id=b.id AND NOT v.withdrawn AND NOT coalesce(v.deleted,false) "
            "AND b.extractor_version=$version AND ($ids IS NULL OR v.id IN $ids) "
            'AND (b.status="pending" OR (b.status="running" AND b.lease_until<$now)) '
            "RETURN b.id AS id, v.id AS vid ORDER BY b.created_at,b.id",
            version=version,
            ids=ids,
            now=now_ms(),
        )
        for candidate in candidates:
            with self.transaction() as tx:
                state = self.lock(tx, candidate["vid"])
                row = tx.run(
                    "MATCH (b:VGBuild {id:$id}) WHERE "
                    '(b.status="pending" OR (b.status="running" AND b.lease_until<$now)) '
                    "RETURN b",
                    id=candidate["id"],
                    now=now_ms(),
                ).single()
                if (
                    not row
                    or state.get("withdrawn")
                    or state.get("deleted")
                    or state.get("desired_build_id") != candidate["id"]
                ):
                    continue
                token = str(uuid.uuid4())
                tx.run(
                    'MATCH (b:VGBuild {id:$id}) SET b.status="running", '
                    "b.lease_token=$token,b.lease_until=$until, "
                    "b.attempts=b.attempts+1,b.error_message=null",
                    id=candidate["id"],
                    token=token,
                    until=now_ms() + LEASE_SECONDS * 1000,
                ).consume()
                return dict(row["b"]), token
        return None

    def checkpoint(self, build_id, video_id, token, output):
        with self.transaction() as tx:
            self.lock(tx, video_id)
            self.owned(tx, build_id, token)
            tx.run(
                "MATCH (b:VGBuild {id:$id}) SET b.output_json=$output,b.lease_until=$until",
                id=str(build_id),
                output=encode(output),
                until=now_ms() + LEASE_SECONDS * 1000,
            ).consume()

    def fail(self, build_id, video_id, token, detail, summary):
        with self.transaction() as tx:
            self.lock(tx, video_id)
            build = self.owned(tx, build_id, token)
            output = decode(build.get("output_json"))
            output["last_error"] = detail
            tx.run(
                'MATCH (b:VGBuild {id:$id}) SET b.status="failed", b.output_json=$output, '
                "b.error_message=$summary,b.lease_token=null,b.lease_until=null",
                id=str(build_id),
                output=encode(output),
                summary=summary,
            ).consume()

    def withdraw(self, video_id, restore=False, deleted=False):
        with self.transaction() as tx:
            tx.run(
                "MERGE (v:VGVideo {id:$id}) ON CREATE SET v.withdrawn=false", id=str(video_id)
            ).consume()
            state = self.lock(tx, video_id)
            tx.run(
                "MATCH (v:VGVideo {id:$id}) SET v.withdrawn=$withdrawn, v.deleted=$deleted",
                id=str(video_id),
                withdrawn=not restore,
                deleted=deleted,
            ).consume()
            if not restore:
                tx.run(
                    "MATCH (v:VGVideo {id:$id}) SET v.active_build_id=null, "
                    "v.desired_build_id=null",
                    id=str(video_id),
                ).consume()
                tx.run(
                    'MATCH (b:VGBuild {id:$id}) WHERE b.status IN ["running","pending"] '
                    'SET b.status="obsolete",b.lease_token=null,b.lease_until=null',
                    id=state.get("desired_build_id"),
                ).consume()

    def activate(self, material, build_id, token, output):
        with self.transaction() as tx:
            state = self.lock(tx, material.id)
            build = self.owned(tx, build_id, token)
            if (
                state.get("withdrawn")
                or state.get("deleted")
                or state.get("desired_build_id") != str(build_id)
                or build["fingerprint"] != material.fingerprint
            ):
                tx.run(
                    'MATCH (b:VGBuild {id:$id}) SET b.status="obsolete", '
                    "b.lease_token=null,b.lease_until=null",
                    id=str(build_id),
                ).consume()
                return "obsolete"
            coverage = update_coverage(output, material)
            if coverage["pending_chunks"]:
                raise ValueError("仍有未处理的切片，不能激活图谱")
            passed = {
                str(c.id)
                for c in material.extraction_chunks
                if coverage["applicable"] and str(c.id) in output.get("chunks", {})
            }
            status = "partial_success" if coverage["skipped_chunks"] else "success"
            message = None
            if coverage["applicable"] and not passed:
                status = "failed"
                message = "所有切片均未通过校验，未生成新图谱"
                output["last_error"] = {
                    "stage": "validation",
                    "error_type": "ChunkValidationError",
                    "code": "all_chunks_invalid",
                    "message": message,
                }
            preserve = (
                status == "partial_success"
                and state.get("active_build_id")
                and (
                    state["active_build_id"] != str(build_id)
                    or not set(output.get("published_chunk_ids", [])).issubset(passed)
                )
            )
            output["projection_activated"] = status != "failed" and not bool(preserve)
            if status != "failed":
                output.pop("last_error", None)
            if status == "partial_success":
                message = (
                    f"部分完成：通过 {coverage['passed_chunks']}/{coverage['total_chunks']} 个切片"
                )
            if preserve:
                output["retained_active_build_id"] = state["active_build_id"]
            elif status != "failed":
                output.pop("retained_active_build_id", None)
                self.publish(tx, material, str(build_id), output)
                output["published_chunk_ids"] = sorted(passed)
                output["published_coverage"] = dict(coverage)
                tx.run(
                    "MATCH (v:VGVideo {id:$id}) SET v.active_build_id=$bid",
                    id=str(material.id),
                    bid=str(build_id),
                ).consume()
            tx.run(
                "MATCH (b:VGBuild {id:$id}) SET b.status=$status, b.output_json=$output, "
                "b.error_message=$message,b.lease_token=null,b.lease_until=null",
                id=str(build_id),
                status=status,
                output=encode(output),
                message=message,
            ).consume()
            return status

    @staticmethod
    def publish(tx, material, build_id, output):
        # Delete only this build's projection. Other videos and historical builds survive.
        tx.run(
            "MATCH (b:VGBuild {id:$id})-[:HAS_MENTION|HAS_EVIDENCE]->(n) " "DETACH DELETE n",
            id=build_id,
        ).consume()
        if not output.get("assessment", {}).get("relevant"):
            return
        for chunk in material.extraction_chunks:
            raw = output.get("chunks", {}).get(str(chunk.id))
            if raw is None:
                continue
            extraction = validate_extraction(chunk, ChunkExtraction.model_validate(raw))
            entity_ids = {}
            for entity in extraction.entities:
                alias_id = digest([entity.entity_type, normalized_name(entity.surface_text)])
                existing = tx.run(
                    "MATCH (a:VGAlias {id:$id})-[:ALIAS_OF]->(e:VGEntity) " "RETURN e.id AS id",
                    id=alias_id,
                ).single()
                name = canonical_name(entity.surface_text, entity.entity_type)
                eid = (
                    existing["id"]
                    if existing
                    else str(
                        uuid.uuid5(
                            ENTITY_NAMESPACE, f"{entity.entity_type}:{normalized_name(name)}"
                        )
                    )
                )
                entity_ids[entity.key] = eid
                tx.run(
                    "MERGE (e:VGEntity {id:$id}) ON CREATE SET e.name=$name, "
                    "e.canonical_name=$name,e.entity_type=$kind "
                    "MERGE (a:VGAlias {id:$aid}) ON CREATE SET a.surface_text=$surface, "
                    "a.normalized_alias=$normalized,a.entity_type=$kind "
                    "MERGE (a)-[:ALIAS_OF]->(e)",
                    id=eid,
                    name=name,
                    kind=entity.entity_type,
                    aid=alias_id,
                    surface=entity.surface_text,
                    normalized=normalized_name(entity.surface_text),
                ).consume()
                start, end = locate_quote(chunk.text, entity.surface_text)
                mid = digest([build_id, str(chunk.id), eid, start, end])
                tx.run(
                    "MATCH (b:VGBuild {id:$bid}),(e:VGEntity {id:$eid}) "
                    "MERGE (m:VGMention {id:$id}) SET m += $props "
                    "MERGE (b)-[:HAS_MENTION]->(m) MERGE (m)-[:REFERS_TO]->(e)",
                    bid=build_id,
                    eid=eid,
                    id=mid,
                    props={
                        "content_item_id": str(material.id),
                        "content_chunk_id": str(chunk.id),
                        "surface_text": entity.surface_text,
                        "start_char": start,
                        "end_char": end,
                        "confidence": min(entity.confidence, 0.5)
                        if chunk.evidence_type == "video_summary"
                        else entity.confidence,
                        "evidence_type": chunk.evidence_type,
                    },
                ).consume()
            for relation in extraction.relations:
                subject, obj = entity_ids[relation.subject], entity_ids[relation.object]
                if subject == obj:
                    continue
                key = digest([subject, relation.predicate, obj, relation.qualifiers])
                rid = str(uuid.uuid5(ENTITY_NAMESPACE, key))
                start, end = locate_quote(chunk.text, relation.quote)
                quote = chunk.text[start:end]
                evid = digest([build_id, rid, str(chunk.id), digest(quote), relation.assertion])
                # Evidence IDs remain UUIDs for the existing API contract.
                evid = str(uuid.uuid5(ENTITY_NAMESPACE, evid))
                tx.run(
                    "MATCH (s:VGEntity {id:$subject}),(o:VGEntity {id:$object}) "
                    "MERGE (r:VGRelation {id:$id}) SET r.predicate=$predicate, "
                    "r.relation_key=$key,r.qualifiers_json=$qualifiers "
                    "MERGE (r)-[:SUBJECT]->(s) MERGE (r)-[:OBJECT]->(o) "
                    "MERGE (s)-[f:VG_FACT {id:$id}]->(o) SET f.predicate=$predicate",
                    subject=subject,
                    object=obj,
                    id=rid,
                    predicate=relation.predicate,
                    key=key,
                    qualifiers=encode(relation.qualifiers),
                ).consume()
                tx.run(
                    "MATCH (b:VGBuild {id:$bid}),(r:VGRelation {id:$rid}) "
                    "MERGE (e:VGEvidence {id:$id}) SET e += $props "
                    "MERGE (b)-[:HAS_EVIDENCE]->(e) MERGE (e)-[:SUPPORTS]->(r)",
                    bid=build_id,
                    rid=rid,
                    id=evid,
                    props={
                        "content_chunk_id": str(chunk.id),
                        "quote": quote,
                        "quote_hash": digest(quote),
                        "char_start": start,
                        "char_end": end,
                        "start_seconds": chunk.start_seconds,
                        "end_seconds": chunk.end_seconds,
                        "assertion": relation.assertion,
                        "confidence": relation.confidence,
                        "caption_source": material.caption_source,
                    },
                ).consume()
