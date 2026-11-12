#!/usr/bin/env python3
"""Verify all active shared projections and HTTP pages; export a complete human review table."""

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
from app.services.document_graph.source import save_json, sha256  # noqa: E402
from app.services.knowledge_graph import queries  # noqa: E402
from app.services.knowledge_graph.storage import UnifiedGraphStore  # noqa: E402


def write_html(report, output, api_url, document_source):
    esc = lambda value: html.escape(str(value))  # noqa: E731
    parts = [
        '<!doctype html><meta charset="utf-8"><title>统一图谱全量复核</title>',
        "<style>body{font:15px/1.6 sans-serif;margin:24px}table{border-collapse:collapse;"
        "width:100%}td,th{border:1px solid #ccc;padding:8px;text-align:left;vertical-align:top}"
        "pre{white-space:pre-wrap;max-width:650px}img{max-width:350px;max-height:280px}"
        "details{margin:8px 0}</style><h1>视频与技术文档统一图谱</h1>",
        "<p>从有效构建回读全部实体和事实。结构校验通过不代表所有事实语义均正确。</p>",
        "<pre>" + esc(json.dumps(report["counts"], ensure_ascii=False, indent=2)) + "</pre>",
        "<h2>实体</h2><table><tr><th>名称</th><th>类型</th><th>作用域</th></tr>",
    ]
    for e in report["entities"]:
        parts.append(
            f'<tr><td>{esc(e["name"])}</td><td>{esc(e["entity_type"])}</td>'
            f'<td>{esc(e["scope"])}</td></tr>'
        )
    parts.append(
        "</table><h2>事实与来源证据</h2><table><tr><th>主体</th><th>关系</th>"
        "<th>客体 / 值</th><th>断言与条件</th><th>各来源证据</th></tr>"
    )
    for f in report["items"]:
        value = f["object_entity"]["name"] if f["object_entity"] else f["value"]
        conditions = dict(qualifiers=f["qualifiers"], details=f["details"])
        parts.append(
            f'<tr><td>{esc(f["subject_entity"]["name"])}</td>'
            f'<td>{esc(f["predicate"])}<br>{esc(f["attribute"] or "")}</td>'
            f'<td>{esc(value)}</td><td>{esc(f["assertion"])}<pre>'
            f'{esc(json.dumps(conditions, ensure_ascii=False))}'
            '</pre></td><td>'
        )
        for s in f["sources"]:
            parts.append(
                f'<p><b>{esc(s["source_type"])}</b> · '
                f'<a href="{esc(s["url"])}">{esc(s["title"])}</a></p>'
            )
            for ev in s["evidence"]:
                loc = ev["locator"]
                parts.append(
                    "<pre>" + esc(ev["quote"]) + "</pre><details><summary>定位与审核信息</summary>"
                    "<pre>" + esc(json.dumps(ev, ensure_ascii=False, indent=2)) + "</pre></details>"
                )
                for region in loc.get("regions", []):
                    asset_id = region["asset_id"]
                    image = f'{api_url}/document-graph/assets/{s["external_id"]}/{asset_id}'
                    if document_source:
                        path = document_source / "assets" / f"{asset_id}.png"
                        if path.is_file():
                            image = os.path.relpath(path, output.parent)
                    parts.append(
                        f'<a href="{esc(image)}"><img loading="lazy" '
                        f'src="{esc(image)}" alt="原始手册图片"></a>'
                    )
        parts.append("</td></tr>")
    parts.append("</table>")
    output.write_text("".join(parts), encoding="utf-8")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-file", type=Path, required=True)
    parser.add_argument("--api-url", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--document-source", type=Path)
    args = parser.parse_args()
    if not args.env_file.is_file():
        raise ValueError("统一图谱配置文件不存在")
    settings = Settings(_env_file=(ROOT / ".env", args.env_file))
    with (
        UnifiedGraphStore(settings) as store,
        httpx.Client(base_url=args.api_url, trust_env=False, timeout=120) as client,
    ):
        sources = queries.sources(store)
        active = [s for s in sources if not s["withdrawn"] and s["active_build_id"]]
        checks = {s["id"]: queries.verify(store, s["id"]) for s in active}
        if not checks or not all(c["verified"] for c in checks.values()):
            raise ValueError("存在无效投影或没有有效来源")
        expected = store.read(
            "MATCH(s:KGSource)-[:KG_HAS_BUILD]->(b:KGBuild)-[:KG_HAS_FACT]->(f:KGFact) "
            "WHERE s.active_build_id=b.id AND NOT s.withdrawn RETURN DISTINCT f.id AS id"
        )
        entities = store.read(
            "MATCH(s:KGSource)-[:KG_HAS_BUILD]->(b:KGBuild)-[:KG_HAS_ENTITY]->(e:KGEntity) "
            "WHERE s.active_build_id=b.id AND NOT s.withdrawn RETURN DISTINCT e ORDER BY e.name"
        )
        items = []
        while True:
            response = client.get(
                "/knowledge-graph/retrieve", params=dict(limit=100, offset=len(items))
            )
            response.raise_for_status()
            batch = response.json()["items"]
            items.extend(batch)
            if len(batch) < 100:
                break
        if len(items) != len({f["id"] for f in items}) or {f["id"] for f in items} != {
            r["id"] for r in expected
        }:
            raise ValueError("HTTP 分页未完整覆盖实际图谱事实")
        images = {}
        for f in items:
            for s in f["sources"]:
                for ev in s["evidence"]:
                    for region in ev["locator"].get("regions", []):
                        url = f'/document-graph/assets/{s["external_id"]}/{region["asset_id"]}'
                        images[url] = region["asset_sha256"]
        for url, digest in images.items():
            response = client.get(url)
            response.raise_for_status()
            if sha256(response.content) != digest or response.headers.get("etag") != f'"{digest}"':
                raise ValueError("实际图片资源与共享证据不符")
        merged = store.read(
            "MATCH(s:KGSource)-[:KG_HAS_BUILD]->(b:KGBuild)-[:KG_HAS_ENTITY]->(e:KGEntity) "
            "WHERE s.active_build_id=b.id AND NOT s.withdrawn "
            "WITH e,collect(DISTINCT s.source_type) AS types,collect(DISTINCT s.id) AS sources "
            "WHERE size(types)>1 RETURN e.id AS id,e.name AS name,types,sources ORDER BY e.name"
        )
        report = dict(
            status="success",
            sources=sources,
            verification=checks,
            counts=dict(
                active_sources=len(active),
                entities=len(entities),
                facts=len(items),
                relations=sum(f["kind"] == "relation" for f in items),
                images=len(images),
                cross_source_entities=len(merged),
            ),
            cross_source_entities=merged,
            entities=[r["e"] for r in entities],
            items=items,
        )
    save_json(args.output, report)
    write_html(report, args.output.with_suffix(".html"), args.api_url, args.document_source)
    print(json.dumps(report["counts"], ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
