"""Native forum leases/checkpoints and shared KG activation in one Neo4j transaction."""

import json
import time
import uuid

from app.services.graph_storage import Neo4jStore
from app.services.knowledge_graph import storage as shared
from app.services.knowledge_graph.schema import digest, encode
from app.services.video_graph.material import LeaseLostError

from .projection import LABELS, compile_output
from .source import Material

LEASE_MS = 300_000
EDGE_TYPES = {"REPLIES_TO", "QUOTES", "HAS_SPAN", "STATES", "IN_CASE", "EVALUATES"}


def now_ms():
    return time.time_ns() // 1_000_000


class ForumGraphStore(Neo4jStore):
    def __init__(self, settings):
        super().__init__(
            settings,
            config_prefix=(
                "unified_graph_neo4j" if settings.unified_graph_enabled else "forum_graph_neo4j"
            ),
        )

    def initialize(self):
        shared.initialize(self.driver, self.database)
        with self.transaction() as tx:
            for label in ("FGTopic", "FGBuild", *LABELS):
                tx.run(
                    f"CREATE CONSTRAINT {label.lower()}_id IF NOT EXISTS "
                    f"FOR (n:{label}) REQUIRE n.id IS UNIQUE"
                ).consume()

    def lock(self, tx, source_id):
        row = tx.run(
            "MATCH(s:FGTopic {id:$id}) " "SET s.lock_version=coalesce(s.lock_version,0)+1 RETURN s",
            id=source_id,
        ).single()
        if row is None:
            raise LeaseLostError("论坛来源不存在")
        return dict(row["s"])

    def enqueue(self, material, policy, *, retry_failed=False, expected_desired_id=...):
        sid = material.source_id
        bid = digest([sid, material.fingerprint, policy])
        with self.transaction() as tx:
            tx.run(
                "MERGE(s:FGTopic {id:$sid}) ON CREATE SET s.withdrawn=false "
                "SET s.content_item_id=$cid,s.external_id=$external,s.forum=$forum",
                sid=sid,
                cid=material.content_item_id,
                external=material.external_id,
                forum=material.forum,
            ).consume()
            state = self.lock(tx, sid)
            if state.get("withdrawn"):
                raise ValueError("主题图谱已撤回，须显式恢复后构建")
            if expected_desired_id is not ... and state.get("desired_build_id") not in {
                expected_desired_id,
                bid,
            }:
                raise LeaseLostError("固定计划建立后目标已改变，拒绝覆盖更新的构建")
            inherited = {}
            previous = state.get("desired_build_id") or state.get("active_build_id")
            if previous:
                row = tx.run(
                    "MATCH(b:FGBuild {id:$id}) RETURN b.output_json AS output", id=previous
                ).single()
                if row:
                    inherited = json.loads(row["output"])
            output = {key: inherited.get(key, {}) for key in ("assessments", "windows", "requests")}
            row = tx.run(
                "MATCH(s:FGTopic {id:$sid}) MERGE(b:FGBuild {id:$bid}) "
                "ON CREATE SET b.source_id=$sid,b.fingerprint=$fingerprint,b.policy=$policy,"
                "b.semantic_hash=$semantic,b.status='pending',b.output_json=$output,"
                "b.created_at=$now,b.attempts=0 "
                "MERGE(s)-[:HAS_BUILD]->(b) RETURN b",
                sid=sid,
                bid=bid,
                fingerprint=material.fingerprint,
                semantic=material.semantic_hash,
                policy=policy,
                output=encode(output),
                now=now_ms(),
            ).single()
            build = dict(row["b"])
            if previous and previous != bid:
                tx.run(
                    "MATCH(b:FGBuild {id:$id}) WHERE b.status IN ['pending','running'] "
                    "SET b.status='obsolete',b.token=null,b.lease_until=null",
                    id=previous,
                ).consume()
            if (retry_failed and build["status"] in {"failed", "deferred"}) or (
                build["status"] == "success" and state.get("active_build_id") != bid
            ):
                tx.run(
                    "MATCH(b:FGBuild {id:$id}) SET b.status='pending',b.error_type=null", id=bid
                ).consume()
            changed = state.get("fingerprint") != material.fingerprint
            tx.run(
                "MATCH(s:FGTopic {id:$sid}) SET s.desired_build_id=$bid,"
                "s.fingerprint=$fingerprint,s.semantic_hash=$semantic,"
                "s.stale=CASE WHEN $changed THEN true ELSE coalesce(s.stale,false) END",
                sid=sid,
                bid=bid,
                fingerprint=material.fingerprint,
                semantic=material.semantic_hash,
                changed=changed,
            ).consume()
            if changed:
                shared.set_visibility(tx, "forum", material.external_id, withdrawn=True)
        return bid

    def claim(self, source_id, build_id):
        with self.transaction() as tx:
            state = self.lock(tx, source_id)
            if state.get("withdrawn") or state.get("desired_build_id") != build_id:
                return None
            token = uuid.uuid4().hex
            row = tx.run(
                "MATCH(b:FGBuild {id:$bid}) WHERE b.status='pending' OR "
                "(b.status='running' AND b.lease_until<$now) "
                "SET b.status='running',b.token=$token,b.lease_until=$until,"
                "b.attempts=b.attempts+1 RETURN b",
                bid=build_id,
                now=now_ms(),
                until=now_ms() + LEASE_MS,
                token=token,
            ).single()
            return (dict(row["b"]), token) if row else None

    def observe_update(self, material):
        """Coalesce rapid changes without resetting the first waiting deadline."""
        with self.transaction() as tx:
            state = self.lock(tx, material.source_id)
            if state.get("withdrawn"):
                return
            if state.get("fingerprint") == material.fingerprint:
                tx.run(
                    "MATCH(s:FGTopic {id:$id}) SET s.update_first_seen=null,"
                    "s.update_last_seen=null,s.observed_fingerprint=null",
                    id=material.source_id,
                ).consume()
            elif state.get("observed_fingerprint") != material.fingerprint:
                tx.run(
                    "MATCH(s:FGTopic {id:$id}) SET s.update_first_seen="
                    "coalesce(s.update_first_seen,$now),s.update_last_seen=$now,"
                    "s.observed_fingerprint=$fingerprint",
                    id=material.source_id,
                    now=now_ms(),
                    fingerprint=material.fingerprint,
                ).consume()

    def lease(self, tx, source_id, build_id, token):
        state = self.lock(tx, source_id)
        if state.get("withdrawn") or state.get("desired_build_id") != build_id:
            raise LeaseLostError("来源已撤回或目标构建已变化")
        row = tx.run(
            "MATCH(b:FGBuild {id:$bid}) WHERE b.status='running' "
            "AND b.token=$token AND b.lease_until>=$now RETURN b",
            bid=build_id,
            token=token,
            now=now_ms(),
        ).single()
        if row is None:
            raise LeaseLostError("论坛构建租约已过期或被接管")
        return dict(row["b"])

    def checkpoint(self, source_id, build_id, token, output):
        with self.transaction() as tx:
            self.lease(tx, source_id, build_id, token)
            tx.run(
                "MATCH(b:FGBuild {id:$id}) SET b.output_json=$output,b.lease_until=$until",
                id=build_id,
                output=encode(output),
                until=now_ms() + LEASE_MS,
            ).consume()

    def finish(self, source_id, build_id, token, output, status, *, material=None, error=None):
        if status not in {"skipped", "deferred", "failed"}:
            raise ValueError("无效终态")
        with self.transaction() as tx:
            self.lease(tx, source_id, build_id, token)
            tx.run(
                "MATCH(b:FGBuild {id:$id}) SET b.status=$status,b.output_json=$output,"
                "b.error_type=$error,b.token=null,b.lease_until=null,b.finished_at=$now",
                id=build_id,
                status=status,
                output=encode(output),
                error=error,
                now=now_ms(),
            ).consume()
            if status == "skipped":
                tx.run(
                    "MATCH(s:FGTopic {id:$id}) SET s.active_build_id=null,s.stale=false,"
                    "s.update_first_seen=null,s.update_last_seen=null,s.observed_fingerprint=null",
                    id=source_id,
                ).consume()
                shared.set_visibility(
                    tx, "forum", material.external_id, withdrawn=True, clear_active=True
                )

    def publish(self, material, windows, policy, build_id, token, output):
        if output.get("material"):
            frozen = Material.model_validate(output["material"])
            if frozen.fingerprint == material.fingerprint:
                material = frozen
        rows, publication = compile_output(material, windows, output, policy, build_id)
        complete = {
            **output,
            "material": material.model_dump(),
            "native_hash": digest(rows),
            "publication_hash": publication.payload_hash,
        }
        with self.transaction() as tx:
            build = self.lease(tx, material.source_id, build_id, token)
            if build["fingerprint"] != material.fingerprint or build["policy"] != policy:
                raise LeaseLostError("发布材料与目标不一致")
            for label in LABELS:
                count = tx.run(
                    f"UNWIND $rows AS row MATCH(b:FGBuild {{id:$bid}}) "
                    f"MERGE(n:{label} {{id:row.id}}) ON CREATE SET n.data_json=row.data "
                    "WITH b,n,row WHERE n.data_json=row.data "
                    "MERGE(b)-[:HAS_NATIVE]->(n) RETURN count(*) AS n",
                    bid=build_id,
                    rows=[dict(id=r["id"], data=encode(r["data"])) for r in rows["nodes"][label]],
                ).single()["n"]
                if count != len(rows["nodes"][label]):
                    raise ValueError("已存在的原生投影内容不一致，拒绝激活")
            for kind in EDGE_TYPES:
                tx.run(
                    "UNWIND $rows AS row MATCH(a {id:row.start}),(z {id:row.end}) "
                    f"MERGE(a)-[r:{kind} {{id:row.id}}]->(z)",
                    rows=[e for e in rows["edges"] if e["kind"] == kind],
                ).consume()
            shared.publish(tx, publication)
            tx.run(
                "MATCH(s:FGTopic {id:$sid}),(b:FGBuild {id:$bid}) "
                "SET b.status='success',b.output_json=$output,b.token=null,b.lease_until=null,"
                "b.finished_at=$now,s.active_build_id=$bid,s.stale=false,"
                "s.update_first_seen=null,s.update_last_seen=null,s.observed_fingerprint=null",
                sid=material.source_id,
                bid=build_id,
                output=encode(complete),
                now=now_ms(),
            ).consume()
        return publication

    def status(self, source_id):
        rows = self.read(
            "MATCH(s:FGTopic {id:$sid}) "
            "OPTIONAL MATCH(b:FGBuild {id:s.desired_build_id}) RETURN s,b",
            sid=source_id,
        )
        return rows[0] if rows else None

    def active(self, source_id):
        rows = self.read(
            "MATCH(s:FGTopic {id:$sid}),(b:FGBuild {id:s.active_build_id}) "
            "WHERE NOT s.withdrawn AND NOT s.stale AND b.status='success' RETURN s,b",
            sid=source_id,
        )
        return rows[0] if rows else None

    def withdraw(self, source_id, *, restore=False):
        with self.transaction() as tx:
            state = self.lock(tx, source_id)
            tx.run(
                "MATCH(s:FGTopic {id:$sid}) SET s.withdrawn=$withdrawn,s.stale=true,"
                "s.active_build_id=null",
                sid=source_id,
                withdrawn=not restore,
            ).consume()
            if state.get("desired_build_id"):
                tx.run(
                    "MATCH(b:FGBuild {id:$id}) WHERE b.status='running' "
                    "SET b.status='pending',b.token=null,b.lease_until=null",
                    id=state["desired_build_id"],
                ).consume()
            shared.set_visibility(
                tx, "forum", state["external_id"], withdrawn=True, clear_active=True
            )

    def known(self):
        return self.read(
            "MATCH(s:FGTopic) OPTIONAL MATCH(b:FGBuild {id:s.desired_build_id}) "
            "RETURN s.id AS id,s.content_item_id AS content_item_id,"
            "s.withdrawn AS withdrawn,s.fingerprint AS fingerprint,"
            "s.desired_build_id AS desired_build_id,s.active_build_id AS active_build_id,"
            "s.forum AS forum,b.status AS status,b.lease_until AS lease_until,"
            "s.update_first_seen AS update_first_seen,s.update_last_seen AS update_last_seen,"
            "s.observed_fingerprint AS observed_fingerprint"
        )

    def native_rows(self, build_id):
        nodes = {}
        for label in LABELS:
            nodes[label] = [
                dict(id=r["n"]["id"], data=json.loads(r["n"]["data_json"]))
                for r in self.read(
                    f"MATCH(:FGBuild {{id:$bid}})-[:HAS_NATIVE]->" f"(n:{label}) RETURN n",
                    bid=build_id,
                )
            ]
        edges = self.read(
            "MATCH(b:FGBuild {id:$bid})-[:HAS_NATIVE]->(a)-[r]->(z) "
            "WHERE (b)-[:HAS_NATIVE]->(z) RETURN r.id AS id,type(r) AS kind,"
            "a.id AS start,z.id AS end",
            bid=build_id,
        )
        return dict(nodes=nodes, edges=edges)

    def source_material(self, source_id):
        row = self.active(source_id)
        if not row:
            return None
        output = json.loads(row["b"]["output_json"])
        return Material.model_validate(output["material"])
