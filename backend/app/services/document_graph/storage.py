"""Atomic document graph projections with leases and immutable text/pixel evidence."""

import json
import uuid
from pathlib import Path

from app.services.graph_storage import Neo4jStore
from app.services.video_graph.identity import stable_hash
from app.services.video_graph.material import LeaseLostError

from .grounding import validate_result
from .source import checked_path, load_bundle


def encode(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def now_ms():
    import time

    return int(time.time() * 1000)


def fact_id(build_id, case_id, fact):
    return stable_hash([build_id, case_id, fact["index"], fact["proposal"]])


class DocumentGraphStore(Neo4jStore):
    def __init__(self, settings):
        super().__init__(settings)
        self.artifacts_root = Path(settings.document_graph_artifacts_dir).resolve()

    def initialize(self):
        with self.driver.session(database=self.database) as session:
            for label in [
                "DGManual",
                "DGBuild",
                "DGCase",
                "DGMention",
                "DGFact",
                "DGEvidence",
                "DGPage",
                "DGRegion",
                "DGLine",
                "VGEntity",
            ]:
                session.run(
                    f"CREATE CONSTRAINT {label.lower()}_id IF NOT EXISTS "
                    f"FOR (n:{label}) REQUIRE n.id IS UNIQUE"
                ).consume()

    @staticmethod
    def lock(tx, source_id):
        row = tx.run(
            "MATCH (s:DGManual {id:$id}) SET s.lock_version=coalesce(s.lock_version,0)+1 "
            "RETURN s",
            id=source_id,
        ).single()
        if not row:
            raise LeaseLostError("文档来源已移除")
        return dict(row["s"])

    @staticmethod
    def owned(tx, source_id, build_id, token):
        state = DocumentGraphStore.lock(tx, source_id)
        row = tx.run(
            "MATCH (b:DGBuild {id:$id}) WHERE b.status='running' AND b.token=$token " "RETURN b",
            id=build_id,
            token=token,
        ).single()
        if not row or state.get("desired_build_id") != build_id or state.get("withdrawn"):
            raise LeaseLostError("文档构建租约或目标版本已变化")
        return dict(row["b"])

    def enqueue(self, manual, bundle, root, policy, *, retry_failed=False):
        root = Path(root).resolve()
        if not root.is_relative_to(self.artifacts_root):
            raise ValueError("来源包须位于 DOCUMENT_GRAPH_ARTIFACTS_DIR 内")
        key = stable_hash([manual["id"], bundle["source_hash"], policy])
        with self.transaction() as tx:
            tx.run(
                "MERGE (s:DGManual {id:$id}) ON CREATE SET s.withdrawn=false "
                "SET s.title=$title,s.model=$model,s.manual_version=$version,s.source_url=$url",
                id=manual["id"],
                title=manual["title"],
                model=manual["model"],
                version=manual["manual_version"],
                url=manual["source_url"],
            ).consume()
            state = self.lock(tx, manual["id"])
            if state.get("withdrawn"):
                raise ValueError("文档已撤回，需显式恢复后构建")
            row = tx.run(
                "MERGE (b:DGBuild {id:$id}) ON CREATE SET b.status='pending',"
                "b.source_id=$sid,b.source_hash=$hash,b.policy=$policy,b.output_json='{}',"
                "b.bundle_key=$bundle,b.created_at=$now "
                "WITH b MATCH (s:DGManual {id:$sid}) MERGE (s)-[:HAS_BUILD]->(b) RETURN b",
                id=key,
                sid=manual["id"],
                hash=bundle["source_hash"],
                policy=policy,
                bundle=str(root.relative_to(self.artifacts_root)),
                now=now_ms(),
            ).single()
            build = dict(row["b"])
            old = state.get("desired_build_id")
            if old and old != key:
                tx.run(
                    "MATCH (b:DGBuild {id:$id}) WHERE b.status IN ['pending','running'] "
                    "SET b.status='obsolete',b.token=null,b.lease_until=null",
                    id=old,
                ).consume()
            if build["status"] == "obsolete" or (retry_failed and build["status"] == "failed"):
                tx.run(
                    "MATCH (b:DGBuild {id:$id}) SET b.status='pending',b.error=null", id=key
                ).consume()
            tx.run(
                "MATCH (s:DGManual {id:$id}) SET s.desired_build_id=$bid", id=manual["id"], bid=key
            ).consume()
        return key

    def claim(self, source_id, build_id):
        with self.transaction() as tx:
            state = self.lock(tx, source_id)
            if state.get("withdrawn") or state.get("desired_build_id") != build_id:
                return None
            row = tx.run(
                "MATCH (b:DGBuild {id:$id}) WHERE b.status='pending' OR "
                "(b.status='running' AND b.lease_until<$now) RETURN b",
                id=build_id,
                now=now_ms(),
            ).single()
            if not row:
                return None
            token = str(uuid.uuid4())
            tx.run(
                "MATCH (b:DGBuild {id:$id}) SET b.status='running',b.token=$token,"
                "b.lease_until=$lease,b.attempts=coalesce(b.attempts,0)+1",
                id=build_id,
                token=token,
                lease=now_ms() + 300000,
            ).consume()
            return dict(row["b"]), token

    def checkpoint(self, source_id, build_id, token, output):
        with self.transaction() as tx:
            self.owned(tx, source_id, build_id, token)
            tx.run(
                "MATCH (b:DGBuild {id:$id}) SET b.output_json=$output,b.lease_until=$lease",
                id=build_id,
                output=encode(output),
                lease=now_ms() + 300000,
            ).consume()

    def fail(self, source_id, build_id, token, output, error_type):
        with self.transaction() as tx:
            self.owned(tx, source_id, build_id, token)
            tx.run(
                "MATCH (b:DGBuild {id:$id}) SET b.status='failed',b.error=$error,"
                "b.output_json=$output,b.token=null,b.lease_until=null",
                id=build_id,
                output=encode(output),
                error=error_type,
            ).consume()

    def status(self, source_id):
        rows = self.read(
            "MATCH (s:DGManual {id:$id}) OPTIONAL MATCH (b:DGBuild {id:s.active_build_id}) "
            "OPTIONAL MATCH (d:DGBuild {id:s.desired_build_id}) RETURN s,b,d",
            id=source_id,
        )
        return rows[0] if rows else None

    def publish(self, source_id, build_id, token, bundle, output):
        manual = next(m for m in bundle["manuals"] if m["id"] == source_id)
        expected = {c["id"] for c in bundle["cases"] if c["source_id"] == source_id}
        with self.transaction() as tx:
            build = self.owned(tx, source_id, build_id, token)
            if build["source_hash"] != bundle["source_hash"] or set(output["cases"]) != expected:
                raise ValueError("文档来源或构建覆盖范围不一致，拒绝发布")
            disk = load_bundle(checked_path(self.artifacts_root, build["bundle_key"]))
            if disk["source_hash"] != bundle["source_hash"]:
                raise ValueError("发布前来源包已变化")
            for case in bundle["cases"]:
                if case["source_id"] == source_id:
                    validate_result(
                        bundle,
                        case,
                        output["cases"][case["id"]],
                        build["policy"],
                        root=checked_path(self.artifacts_root, build["bundle_key"]),
                    )
            # Failed transactions retain the old active pointer.
            # All projection writes roll back together.
            for page in manual["pages"].values():
                self._source_page(tx, build_id, page, bundle)
            for cid, result in output["cases"].items():
                self._case(tx, build_id, cid, result, bundle)
            tx.run(
                "MATCH (s:DGManual {id:$sid}),(b:DGBuild {id:$bid}) "
                "SET s.active_build_id=$bid,b.status='success',b.output_json=$output,"
                "b.token=null,b.lease_until=null,b.error=null",
                sid=source_id,
                bid=build_id,
                output=encode(output),
            ).consume()

    @staticmethod
    def _source_page(tx, build_id, page, bundle):
        pid = stable_hash([build_id, page["id"]])
        tx.run(
            "MATCH (b:DGBuild {id:$bid}) MERGE (p:DGPage {id:$id}) "
            "SET p.source_page_id=$source,p.number=$number,p.asset_id=$asset,p.asset_sha256=$sha "
            "MERGE (b)-[:HAS_PAGE]->(p)",
            bid=build_id,
            id=pid,
            source=page["id"],
            number=page["number"],
            asset=page["asset_id"],
            sha=bundle["assets"][page["asset_id"]]["sha256"],
        ).consume()
        for line in page["lines"]:
            tx.run(
                "MATCH (p:DGPage {id:$pid}) MERGE (l:DGLine {id:$id}) "
                "SET l.source_id=$source,l.text=$text,l.bbox=$bbox MERGE (p)-[:HAS_LINE]->(l)",
                pid=pid,
                id=stable_hash([build_id, line["id"]]),
                source=line["id"],
                text=line["text"],
                bbox=line["bbox"],
            ).consume()
        for region in page["regions"]:
            tx.run(
                "MATCH (p:DGPage {id:$pid}) MERGE (r:DGRegion {id:$id}) "
                "SET r.source_id=$source,r.bbox=$bbox,r.asset_id=$asset,r.asset_sha256=$sha "
                "MERGE (p)-[:HAS_REGION]->(r)",
                pid=pid,
                id=stable_hash([build_id, region["id"]]),
                source=region["id"],
                bbox=region["bbox"],
                asset=region["asset_id"],
                sha=bundle["assets"][region["asset_id"]]["sha256"],
            ).consume()

    @staticmethod
    def _case(tx, build_id, case_id, result, bundle):
        cid = stable_hash([build_id, case_id])
        tx.run(
            "MATCH (b:DGBuild {id:$bid}) MERGE (c:DGCase {id:$id}) SET c.case_id=$case,"
            "c.result_hash=$hash MERGE (b)-[:HAS_CASE]->(c)",
            bid=build_id,
            id=cid,
            case=case_id,
            hash=result["review"]["result_hash"],
        ).consume()
        by_key = {e["key"]: e for e in result["entities"]}
        for e in result["entities"]:
            tx.run(
                "MATCH (c:DGCase {id:$cid}) MERGE (e:VGEntity {id:$id}) "
                "ON CREATE SET e.name=$name,e.canonical_name=$name,e.entity_type=$kind,e.sco"
                "pe_key=$scope "
                "MERGE (c)-[:MENTIONS]->(e)",
                cid=cid,
                id=e["id"],
                name=e["canonical_name"],
                kind=e["entity_type"],
                scope=e["scope_key"],
            ).consume()
            mid = stable_hash([build_id, case_id, e["key"]])
            source = e.get("source", [])
            tx.run(
                "MATCH (c:DGCase {id:$cid}),(e:VGEntity {id:$eid}) "
                "MERGE (m:DGMention {id:$id}) SET m.entity_key=$key,m.source_json=$source,"
                "m.proposal_json=$proposal,m.review_hash=$review "
                "MERGE (c)-[:HAS_MENTION]->(m) MERGE (m)-[:REFERS_TO]->(e)",
                cid=cid,
                eid=e["id"],
                id=mid,
                key=e["key"],
                source=encode(source),
                proposal=encode(e.get("proposal")),
                review=result["review"]["result_hash"],
            ).consume()
            for source_row in source:
                for line in source_row["text_rows"]:
                    tx.run(
                        "MATCH (m:DGMention {id:$id}),(l:DGLine {id:$lid}) "
                        "MERGE (m)-[:CITES_TEXT]->(l)",
                        id=mid,
                        lid=stable_hash([build_id, line["id"]]),
                    ).consume()
                for region in source_row["regions"]:
                    tx.run(
                        "MATCH (m:DGMention {id:$id}),(r:DGRegion {id:$rid}) "
                        "MERGE (m)-[:CITES_IMAGE]->(r)",
                        id=mid,
                        rid=stable_hash([build_id, region["id"]]),
                    ).consume()
        for fact in result["facts"]:
            proposal = fact["proposal"]
            fid = fact_id(build_id, case_id, fact)
            oid = by_key[proposal["object"]]["id"] if proposal.get("object") else None
            tx.run(
                "MATCH (c:DGCase {id:$cid}),(s:VGEntity {id:$sid}) MERGE (f:DGFact {id:$id}) "
                "SET f.case_id=$case,f.index=$index,f.predicate=$predicate,f.proposal_json=$"
                "proposal,"
                "f.source_json=$source,f.review_hash=$review MERGE (c)-[:HAS_FACT]->(f) "
                "MERGE (f)-[:SUBJECT]->(s)",
                cid=cid,
                sid=by_key[proposal["subject"]]["id"],
                id=fid,
                case=case_id,
                index=fact["index"],
                predicate=proposal["predicate"],
                proposal=encode(proposal),
                source=encode(fact["source"]),
                review=result["review"]["result_hash"],
            ).consume()
            if oid:
                tx.run(
                    "MATCH (f:DGFact {id:$id}),(o:VGEntity {id:$oid}) MERGE (f)-[:OBJECT]->(o)",
                    id=fid,
                    oid=oid,
                ).consume()
            for i, evidence in enumerate(fact["source"]):
                evid = stable_hash([fid, i])
                pid = stable_hash([build_id, evidence["page_id"]])
                tx.run(
                    "MATCH (f:DGFact {id:$fid}),(p:DGPage {id:$pid}) MERGE (ev:DGEvidence {id:$i"
                    "d}) "
                    "SET ev.source_json=$source,ev.document_sha256=$sha,ev.manual_version=$version,"
                    "ev.page=$page,ev.quote=$quote MERGE (ev)-[:SUPPORTS]->(f) "
                    "MERGE (ev)-[:ON_PAGE]->(p)",
                    fid=fid,
                    pid=pid,
                    id=evid,
                    source=encode(evidence),
                    sha=evidence["document_sha256"],
                    version=evidence["manual_version"],
                    page=evidence["page"],
                    quote=evidence["quote"],
                ).consume()
                for line in evidence["text_rows"]:
                    tx.run(
                        "MATCH (ev:DGEvidence {id:$id}),(l:DGLine {id:$lid}) "
                        "MERGE (ev)-[:CITES_TEXT]->(l)",
                        id=evid,
                        lid=stable_hash([build_id, line["id"]]),
                    ).consume()
                for region in evidence["regions"]:
                    tx.run(
                        "MATCH (ev:DGEvidence {id:$id}),(r:DGRegion {id:$rid}) "
                        "MERGE (ev)-[:CITES_IMAGE]->(r)",
                        id=evid,
                        rid=stable_hash([build_id, region["id"]]),
                    ).consume()

    def withdraw(self, source_id, *, restore=False):
        with self.transaction() as tx:
            self.lock(tx, source_id)
            tx.run(
                "MATCH (s:DGManual {id:$id}) SET s.withdrawn=$withdrawn",
                id=source_id,
                withdrawn=not restore,
            ).consume()
