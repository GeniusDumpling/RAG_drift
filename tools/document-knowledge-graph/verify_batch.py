#!/usr/bin/env python3
"""Read back a document batch, verify its HTTP evidence, and export all facts for review.

Read-only: does not call models, publish builds, or change database contents.
"""

import argparse
import html
import json
import os
import sys
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "backend"), str(ROOT)]

from app.core.config import Settings  # noqa: E402
from app.services.document_graph.queries import verify  # noqa: E402
from app.services.document_graph.source import (  # noqa: E402
    ensure_report_path,
    load_bundle,
    save_json,
    sha256,
)
from app.services.document_graph.storage import DocumentGraphStore  # noqa: E402


def check_http(client, manual, facts):
    sid = manual["id"]
    response = client.get("/document-graph/status", params={"source_id": sid})
    response.raise_for_status()
    if not response.json()["verification"]["verified"]:
        raise ValueError("HTTP 状态检查未通过")
    returned = {}
    # This is a bounded batch verifier. Refuse to claim complete coverage if a
    # predicate's results exceed the endpoint's 100-item limit.
    predicates = sorted({f["proposal"]["predicate"] for f in facts})
    for predicate in predicates:
        response = client.get(
            "/document-graph/retrieve",
            params=dict(source_id=sid, q=predicate, limit=100),
        )
        response.raise_for_status()
        for item in response.json()["items"]:
            if item["source_id"] != sid or item["manual_version"] != manual["manual_version"]:
                raise ValueError("HTTP 检索来源或版本不符")
            returned[item["id"]] = item
    if set(returned) != {f["id"] for f in facts}:
        raise ValueError("HTTP 未覆盖全部图谱事实；请减小验证批次或扩展分页接口")
    images = {}
    for item in returned.values():
        for evidence in item["evidence"]:
            for image in evidence["images"]:
                images[image["url"]] = image["sha256"]
    for url, digest in images.items():
        response = client.get(url)
        response.raise_for_status()
        if sha256(response.content) != digest or response.headers.get("etag") != f'"{digest}"':
            raise ValueError("HTTP 图片内容或 ETag 与图谱不符")
    return dict(facts=len(returned), images=len(images), predicates=predicates, verified=True)


def write_html(report, bundle, source_root, output):
    esc = lambda value: html.escape(str(value))  # noqa: E731
    parts = [
        '<!doctype html><meta charset="utf-8"><title>文档图谱全量复核</title>',
        "<style>body{font:15px/1.6 sans-serif;margin:24px}table{border-collapse:collapse;"
        "width:100%}td,th{border:1px solid #ccc;padding:8px;text-align:left;vertical-align:top}"
        "pre{white-space:pre-wrap;max-width:650px}img{max-width:350px;max-height:280px}"
        "details{margin:8px 0}</style>",
        "<h1>本批次文档图谱全量复核</h1><p>从独立 Neo4j 有效构建回读。"
        "此表覆盖全部已入库实体与事实，完整性校验通过不等于所有抽取均为正确。</p>",
    ]
    for manual in report["manuals"]:
        parts.append(f'<h2>{esc(manual["model"])}</h2>')
        parts.append("<h3>实体</h3><table><tr><th>名称</th><th>类别</th><th>作用域</th></tr>")
        for entity in manual["entities"]:
            parts.append(
                f'<tr><td>{esc(entity["canonical_name"])}</td>'
                f'<td>{esc(entity["entity_type"])}</td><td>{esc(entity["scope_key"])}</td></tr>'
            )
        parts.append(
            "</table><h3>事实</h3><table><tr><th>来源片段</th><th>主体</th>"
            "<th>关系 / 参数 / 操作</th><th>客体 / 值</th><th>原文与原图</th></tr>"
        )
        for fact in manual["facts"]:
            p = fact["proposal"]
            parts.append(
                f'<tr><td>{esc(fact["case_id"])}</td><td>{esc(fact["subject"])}</td>'
                f'<td>{esc(p["predicate"])}<br>{esc(p.get("attribute") or "")}</td>'
                f'<td>{esc(fact["object"] or p.get("value") or "")}'
                '<details><summary>完整事实字段</summary><pre>'
                f'{esc(json.dumps(p, ensure_ascii=False, indent=2))}</pre></details></td><td>'
            )
            for evidence in fact["evidence"]:
                parts.append(
                    f'<p>PDF 第 {evidence["page"]} 页</p><pre>{esc(evidence["quote"])}</pre>'
                )
                for region in evidence["regions"]:
                    asset = bundle["assets"][region["asset_id"]]
                    relative = os.path.relpath(source_root / asset["storage_key"], output.parent)
                    parts.append(
                        f'<a href="{esc(relative)}"><img loading="lazy" '
                        f'src="{esc(relative)}" alt="{esc(region["id"])}"></a>'
                    )
            parts.append("</td></tr>")
        parts.append("</table>")
    output.write_text("".join(parts), encoding="utf-8")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-file", type=Path, required=True)
    parser.add_argument("--source-bundle", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--api-url", help="可选：同时核对已运行的文档 API 和全部引用图片")
    args = parser.parse_args()
    bundle = load_bundle(args.source_bundle)
    ensure_report_path(args.source_bundle, args.output)
    ensure_report_path(args.source_bundle, args.output.with_suffix(".html"))
    settings = Settings(_env_file=(ROOT / ".env", args.env_file))
    report = dict(source_hash=bundle["source_hash"], status="success", manuals=[])
    with (
        DocumentGraphStore(settings) as store,
        httpx.Client(
            base_url=args.api_url or "http://localhost", timeout=120, trust_env=False
        ) as client,
    ):
        for manual in bundle["manuals"]:
            check = verify(store, manual["id"])
            if not check["verified"]:
                raise ValueError(f'{manual["model"]} 图谱回读校验未通过')
            bid = check["build_id"]
            entities = store.read(
                "MATCH (:DGBuild {id:$bid})-[:HAS_CASE]->(:DGCase)-[:MENTIONS]->(e:VGEntity) "
                "RETURN DISTINCT e ORDER BY e.canonical_name",
                bid=bid,
            )
            rows = store.read(
                "MATCH (:DGBuild {id:$bid})-[:HAS_CASE]->(:DGCase)-[:HAS_FACT]->(f:DGFact) "
                "MATCH (f)-[:SUBJECT]->(s:VGEntity) OPTIONAL MATCH (f)-[:OBJECT]->(o:VGEntity) "
                "RETURN f, s.canonical_name AS subject,o.canonical_name AS object "
                "ORDER BY f.case_id,f.index",
                bid=bid,
            )
            facts = [
                dict(
                    id=r["f"]["id"],
                    case_id=r["f"]["case_id"],
                    subject=r["subject"],
                    object=r["object"],
                    proposal=json.loads(r["f"]["proposal_json"]),
                    evidence=json.loads(r["f"]["source_json"]),
                )
                for r in rows
            ]
            row = dict(
                source_id=manual["id"],
                model=manual["model"],
                verification=check,
                entities=[r["e"] for r in entities],
                facts=facts,
            )
            if args.api_url:
                row["http"] = check_http(client, manual, facts)
            report["manuals"].append(row)
    save_json(args.output, report)
    write_html(report, bundle, args.source_bundle, args.output.with_suffix(".html"))
    print(
        json.dumps(
            dict(
                status=report["status"],
                manuals=[
                    dict(
                        model=r["model"],
                        entities=len(r["entities"]),
                        facts=len(r["facts"]),
                        http=r.get("http"),
                    )
                    for r in report["manuals"]
                ],
            ),
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
