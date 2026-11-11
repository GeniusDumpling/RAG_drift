"""Active-only graph readback and retrieval with native text and original image links."""

import json

from app.services.video_graph.identity import stable_hash
from app.services.video_graph.semantics import normalized_name

from .grounding import validate_result
from .source import checked_path, load_bundle
from .storage import fact_id


def active_build(store, source_id):
    state = store.status(source_id)
    if not state or state["s"].get("withdrawn") or not state.get("b"):
        return None
    build = dict(state["b"])
    bundle = load_bundle(checked_path(store.artifacts_root, build["bundle_key"]))
    if build["source_hash"] != bundle["source_hash"]:
        raise ValueError("有效文档图谱与磁盘来源包不符")
    output = json.loads(build["output_json"])
    expected = {c["id"] for c in bundle["cases"] if c["source_id"] == source_id}
    if set(output.get("cases", {})) != expected or build["status"] != "success":
        raise ValueError("有效构建不完整")
    for case in bundle["cases"]:
        if case["id"] in expected:
            validate_result(
                bundle,
                case,
                output["cases"][case["id"]],
                build["policy"],
                root=checked_path(store.artifacts_root, build["bundle_key"]),
            )
    return build, bundle, output


def expected_projection(build_id, bundle, source_id, output):
    manual = next(m for m in bundle["manuals"] if m["id"] == source_id)
    entities, mentions, facts, evidence = {}, {}, {}, {}
    for cid, result in output["cases"].items():
        by_key = {e["key"]: e for e in result["entities"]}
        for e in result["entities"]:
            entities[e["id"]] = (
                normalized_name(e["canonical_name"]),
                e["entity_type"],
                e["scope_key"],
            )
            source = e.get("source", [])
            mentions[stable_hash([build_id, cid, e["key"]])] = (
                e["id"],
                e["key"],
                stable_hash(source),
                stable_hash(e.get("proposal")),
                result["review"]["result_hash"],
                tuple(
                    sorted(
                        {stable_hash([build_id, r["id"]]) for ev in source for r in ev["text_rows"]}
                    )
                ),
                tuple(
                    sorted(
                        {stable_hash([build_id, r["id"]]) for ev in source for r in ev["regions"]}
                    )
                ),
            )
        for f in result["facts"]:
            fid = fact_id(build_id, cid, f)
            obj = f["proposal"].get("object")
            facts[fid] = (
                cid,
                f["index"],
                f["proposal"]["predicate"],
                stable_hash(f["proposal"]),
                stable_hash(f["source"]),
                result["review"]["result_hash"],
                by_key[f["proposal"]["subject"]]["id"],
                by_key[obj]["id"] if obj else None,
            )
            for index, ev in enumerate(f["source"]):
                evidence[stable_hash([fid, index])] = (
                    fid,
                    stable_hash(ev),
                    ev["document_sha256"],
                    ev["manual_version"],
                    ev["page"],
                    ev["quote"],
                    stable_hash([build_id, ev["page_id"]]),
                    tuple(sorted(stable_hash([build_id, r["id"]]) for r in ev["text_rows"])),
                    tuple(sorted(stable_hash([build_id, r["id"]]) for r in ev["regions"])),
                )
    lines, regions, pages = {}, {}, {}
    for page in manual["pages"].values():
        pid = stable_hash([build_id, page["id"]])
        pages[pid] = (page["id"], page["number"], page["asset_id"], page["asset_id"])
        for r in page["lines"]:
            lines[stable_hash([build_id, r["id"]])] = (pid, r["id"], r["text"], tuple(r["bbox"]))
        for r in page["regions"]:
            regions[stable_hash([build_id, r["id"]])] = (
                pid,
                r["id"],
                tuple(r["bbox"]),
                r["asset_id"],
                r["asset_id"],
            )
    return dict(
        entities=entities,
        mentions=mentions,
        facts=facts,
        evidence=evidence,
        lines=lines,
        regions=regions,
        pages=pages,
    )


def read_projection(store, build_id):
    cases = store.read("MATCH (b:DGBuild {id:$bid})-[:HAS_CASE]->(c:DGCase) RETURN c", bid=build_id)
    entities = store.read(
        "MATCH (b:DGBuild {id:$bid})-[:HAS_CASE]->(:DGCase)-[:MENTIONS]->(e:VGEntity) "
        "RETURN DISTINCT e",
        bid=build_id,
    )
    facts = store.read(
        "MATCH (b:DGBuild {id:$bid})-[:HAS_CASE]->(:DGCase)-[:HAS_FACT]->(f:DGFact)"
        "-[:SUBJECT]->(s:VGEntity) OPTIONAL MATCH (f)-[:OBJECT]->(o:VGEntity) RETURN f,s,o",
        bid=build_id,
    )
    evidence = store.read(
        "MATCH (b:DGBuild {id:$bid})-[:HAS_CASE]->(:DGCase)-[:HAS_FACT]->(f:DGFact)"
        "<-[:SUPPORTS]-(ev:DGEvidence)-[:ON_PAGE]->(p:DGPage) "
        "OPTIONAL MATCH (ev)-[:CITES_TEXT]->(l:DGLine) "
        "OPTIONAL MATCH (ev)-[:CITES_IMAGE]->(r:DGRegion) "
        "RETURN f.id AS fid,ev,p.id AS pid,collect(DISTINCT l.id) AS lines,collect(D"
        "ISTINCT r.id) AS regions",
        bid=build_id,
    )
    pages = store.read("MATCH (b:DGBuild {id:$bid})-[:HAS_PAGE]->(p:DGPage) RETURN p", bid=build_id)
    lines = store.read(
        "MATCH (b:DGBuild {id:$bid})-[:HAS_PAGE]->(p:DGPage)-[:HAS_LINE]->(l:DGLine) "
        "RETURN p.id AS pid,l",
        bid=build_id,
    )
    regions = store.read(
        "MATCH (b:DGBuild {id:$bid})-[:HAS_PAGE]->(p:DGPage)-[:HAS_REGION]->(r:DGRegion) "
        "RETURN p.id AS pid,r",
        bid=build_id,
    )
    mentions = store.read(
        "MATCH (b:DGBuild {id:$bid})-[:HAS_CASE]->(:DGCase)-[:HAS_MENTION]->(m:DGMention)"
        "-[:REFERS_TO]->(e:VGEntity) OPTIONAL MATCH (m)-[:CITES_TEXT]->(l:DGLine) "
        "OPTIONAL MATCH (m)-[:CITES_IMAGE]->(r:DGRegion) "
        "RETURN m,e.id AS eid,collect(DISTINCT l.id) AS lines,collect(DISTINCT r.id) AS regions",
        bid=build_id,
    )
    for rows, field in (
        (cases, "c"),
        (facts, "f"),
        (evidence, "ev"),
        (mentions, "m"),
        (pages, "p"),
        (lines, "l"),
        (regions, "r"),
    ):
        if len({r[field]["id"] for r in rows}) != len(rows):
            raise ValueError("图谱证据存在重复或多重端点连接")
    projection = dict(
        cases={r["c"]["case_id"]: r["c"]["result_hash"] for r in cases},
        entities={
            r["e"]["id"]: (
                normalized_name(r["e"].get("canonical_name", "")),
                r["e"].get("entity_type"),
                r["e"].get("scope_key"),
            )
            for r in entities
        },
        mentions={
            r["m"]["id"]: (
                r["eid"],
                r["m"]["entity_key"],
                stable_hash(json.loads(r["m"]["source_json"])),
                stable_hash(json.loads(r["m"]["proposal_json"])),
                r["m"]["review_hash"],
                tuple(sorted(r["lines"])),
                tuple(sorted(r["regions"])),
            )
            for r in mentions
        },
        facts={
            r["f"]["id"]: (
                r["f"]["case_id"],
                r["f"]["index"],
                r["f"]["predicate"],
                stable_hash(json.loads(r["f"]["proposal_json"])),
                stable_hash(json.loads(r["f"]["source_json"])),
                r["f"]["review_hash"],
                r["s"]["id"],
                r["o"]["id"] if r["o"] else None,
            )
            for r in facts
        },
        evidence={
            r["ev"]["id"]: (
                r["fid"],
                stable_hash(json.loads(r["ev"]["source_json"])),
                r["ev"]["document_sha256"],
                r["ev"]["manual_version"],
                r["ev"]["page"],
                r["ev"]["quote"],
                r["pid"],
                tuple(sorted(r["lines"])),
                tuple(sorted(r["regions"])),
            )
            for r in evidence
        },
        pages={
            r["p"]["id"]: (
                r["p"]["source_page_id"],
                r["p"]["number"],
                r["p"]["asset_id"],
                r["p"]["asset_sha256"],
            )
            for r in pages
        },
        lines={
            r["l"]["id"]: (r["pid"], r["l"]["source_id"], r["l"]["text"], tuple(r["l"]["bbox"]))
            for r in lines
        },
        regions={
            r["r"]["id"]: (
                r["pid"],
                r["r"]["source_id"],
                tuple(r["r"]["bbox"]),
                r["r"]["asset_id"],
                r["r"]["asset_sha256"],
            )
            for r in regions
        },
    )
    return projection


def verify(store, source_id, expected_build_id=None):
    active = active_build(store, source_id)
    if not active:
        return dict(verified=False, source_id=source_id, checks={"active": False})
    build, bundle, output = active
    expected = expected_projection(build["id"], bundle, source_id, output)
    expected["cases"] = {cid: r["review"]["result_hash"] for cid, r in output["cases"].items()}
    actual = read_projection(store, build["id"])
    checks = {key: actual[key] == expected[key] for key in expected}
    checks["target_build"] = expected_build_id is None or build["id"] == expected_build_id
    latest = store.status(source_id)
    checks["unchanged"] = bool(
        latest
        and not latest["s"].get("withdrawn")
        and latest["s"].get("active_build_id") == build["id"]
    )
    if expected_build_id is not None:
        checks["desired_build"] = bool(
            latest and latest["s"].get("desired_build_id") == expected_build_id
        )
    return dict(
        verified=all(checks.values()),
        source_id=source_id,
        build_id=build["id"],
        checks=checks,
        counts={key: len(value) for key, value in actual.items()},
    )


def retrieve(store, query, *, source_id=None, limit=10):
    if not 1 <= limit <= 100:
        raise ValueError("limit须为1到100")
    sources = (
        [source_id]
        if source_id
        else [
            r["id"]
            for r in store.read(
                "MATCH (s:DGManual) WHERE s.active_build_id IS NOT NULL AND NOT s.withdrawn "
                "RETURN s.id AS id ORDER BY id"
            )
        ]
    )
    items = []
    for sid in sources:
        active = active_build(store, sid)
        if not active:
            continue
        build, bundle, output = active
        verified = verify(store, sid)
        if not verified["verified"]:
            raise ValueError("图谱文本/图片证据回读不一致，拒绝返回")
        if verified.get("build_id") != build["id"]:
            raise ValueError("查询期间有效构建已变化")
        manual = next(m for m in bundle["manuals"] if m["id"] == sid)
        expected = expected_projection(build["id"], bundle, sid, output)
        for cid, result in output["cases"].items():
            entities = {e["key"]: e for e in result["entities"]}
            for fact in result["facts"]:
                p = fact["proposal"]
                s = entities[p["subject"]]
                o = entities[p["object"]] if p.get("object") else None
                text = " ".join(
                    [
                        s["canonical_name"],
                        o["canonical_name"] if o else "",
                        p.get("value", "") or "",
                        p.get("attribute", "") or "",
                        p["predicate"],
                    ]
                )
                if query.casefold() not in text.casefold():
                    continue
                fid = fact_id(build["id"], cid, fact)
                if fid not in expected["facts"]:
                    raise ValueError("返回事实不属于有效投影")
                evidence = []
                for row in fact["source"]:
                    evidence.append(
                        dict(
                            page=row["page"],
                            quote=row["quote"],
                            text_rows=row["text_rows"],
                            visual_label=row["visual_label"],
                            images=[
                                dict(
                                    region_id=r["id"],
                                    bbox=r["bbox"],
                                    asset_id=r["asset_id"],
                                    sha256=r["asset_sha256"],
                                    url=f"/document-graph/assets/{sid}/{r['asset_id']}",
                                )
                                for r in row["regions"]
                            ],
                        )
                    )
                items.append(
                    dict(
                        id=fid,
                        source_id=sid,
                        case_id=cid,
                        title=manual["title"],
                        manual_version=manual["manual_version"],
                        document_sha256=manual["sha256"],
                        source_url=manual["source_url"],
                        subject=s["canonical_name"],
                        entity_evidence={
                            "subject": s.get("source", []),
                            "object": o.get("source", []) if o else [],
                        },
                        object=o["canonical_name"] if o else None,
                        **{
                            k: v for k, v in p.items() if k not in {"subject", "object", "evidence"}
                        },
                        evidence=evidence,
                    )
                )
                if len(items) >= limit:
                    _unchanged(store, sid, build["id"])
                    return dict(items=items, limit=limit)
        _unchanged(store, sid, build["id"])
    return dict(items=items, limit=limit)


def _unchanged(store, source_id, build_id):
    state = store.status(source_id)
    if not state or state["s"].get("withdrawn") or state["s"].get("active_build_id") != build_id:
        raise ValueError("查询期间文档已撤回或切换版本")


def asset(store, source_id, asset_id):
    active = active_build(store, source_id)
    if not active:
        raise LookupError("来源不可见")
    build, bundle, _ = active
    if asset_id not in bundle["assets"]:
        raise LookupError("资源不属于有效来源")
    manual = next(m for m in bundle["manuals"] if m["id"] == source_id)
    allowed = {manual["pdf_asset_id"]}
    for page in manual["pages"].values():
        allowed.add(page["asset_id"])
        allowed.update(r["asset_id"] for r in page["regions"])
    if asset_id not in allowed:
        raise LookupError("不能跨手册读取资源")
    value = bundle["assets"][asset_id]
    return checked_path(
        checked_path(store.artifacts_root, build["bundle_key"]), value["storage_key"]
    ), value
