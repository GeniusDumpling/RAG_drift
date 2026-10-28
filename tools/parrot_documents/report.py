"""Escaped, local-only evidence preview. Never execute source HTML or code."""

from collections import defaultdict
from html import escape
from pathlib import Path

from tools.drone_documents.common import read_jsonl, validate_bundle, write_json

from .prepare import quality


def report(root: Path):
    manifest = validate_bundle(root)
    documents = read_jsonl(root / "documents.jsonl")
    pages = read_jsonl(root / "pages.jsonl")
    elements = read_jsonl(root / "elements.jsonl")
    assets = {a["id"]: a for a in read_jsonl(root / "assets.jsonl")}
    by_doc = defaultdict(list)
    for entry in elements:
        by_doc[entry["document_version_id"]].append(entry)
    summary = quality(documents, pages, elements)
    summary.update(
        chunks=len(read_jsonl(root / "chunks.jsonl")),
        stage=manifest["stage"],
        failed_collection_records=len(read_jsonl(root / "collection_failures.jsonl")),
    )
    write_json(root / "quality_report.json", summary)
    title = "Parrot 文档处理预览"
    html = [
        '<!doctype html><html lang="zh-CN"><meta charset="utf-8">',
        '<meta name="viewport" content="width=device-width, initial-scale=1">',
        f"<title>{title}</title>",
        "<style>body{max-width:1100px;margin:24px auto;padding:0 20px;font:16px/1.6 sans-serif}"
        "pre{white-space:pre-wrap;overflow-wrap:anywhere;background:#f5f6f8;padding:12px}"
        "img{max-width:100%;height:auto}details{border:1px solid #ccc;padding:12px;margin:12px 0}"
        ".entry{border-top:1px solid #eee;padding:12px 0}.meta{color:#555;font-size:13px}"
        "input{padding:10px;width:90%}summary{cursor:pointer}</style>",
        f"<h1>{title}</h1><p>阶段：{escape(manifest['stage'])}；"
        f"PDF {summary['pdfs']}；网页 {summary['webpages']}；切片 {summary['chunks']}</p>",
        "<p>正文保留来源语言；图示说明是模型派生内容。SDK 归档范围不代表机型兼容性。</p>",
        '<label>筛选文档标题 <input id="search" type="search"></label>',
    ]
    for doc in documents:
        source = assets[doc["source_asset_id"]]["storage_key"]
        missing = len(doc["metadata_json"].get("missing_images", [])) + len(
            doc["metadata_json"].get("unarchived_images", [])
        )
        html.extend(
            [
                f'<details data-title="{escape(doc["title"].casefold(), quote=True)}">',
                f'<summary>{escape(doc["title"])}</summary>',
                f'<p><a href="{escape(doc["source_url"], quote=True)}" '
                'rel="noreferrer">官方来源</a></p>',
                f'<p>类型：{escape(doc["document_type"])}；版本：'
                f'{escape(doc["official_version"] or "未识别")}；缺失网页图片：{missing}</p>',
                f'<p>{escape(doc["scope_note"])}</p>',
            ]
        )
        # Raw HTML assets are archived for audit, but never linked as executable previews.
        if doc["metadata_json"]["source_kind"] == "pdf":
            html.append(f'<a href="{escape(source, quote=True)}">本地原始 PDF</a>')
        for entry in sorted(by_doc[doc["id"]], key=lambda e: e["reading_order"]):
            numbers = sorted({r["source_page_no"] for r in entry["regions"]})
            location = (
                ("PDF 原页 " + ", ".join(map(str, numbers)))
                if numbers
                else ("HTML #" + (entry["metadata_json"].get("html_anchor") or "无锚点"))
            )
            html.append(
                f'<div class="entry"><div class="meta">'
                f'{escape(entry["section_path"])} · {escape(location)} · '
                f'{escape(entry["element_type"])}</div>'
            )
            if numbers:
                html.append(
                    f'<a href="{escape(source, quote=True)}#page={numbers[0]}">查看原页</a>'
                )
            locator = entry["metadata_json"].get("source_locator")
            if locator:
                html.append(
                    f'<a href="{escape(locator, quote=True)}" rel="noreferrer">查看网页章节</a>'
                )
            if entry["asset_id"]:
                asset = assets[entry["asset_id"]]
                html.append(
                    f'<img loading="lazy" src="{escape(asset["storage_key"], quote=True)}"'
                    ' alt="原始图示裁剪">'
                )
            if entry["text"]:
                html.append("<pre>" + escape(entry["text"]) + "</pre>")
            html.append("</div>")
        html.append("</details>")
    html.append(
        "<script>document.getElementById('search').addEventListener('input', e => {"
        "const q=e.target.value.toLowerCase();document.querySelectorAll('details')"
        ".forEach(d=>{d.hidden=!d.dataset.title.includes(q)});});</script></html>"
    )
    (root / "preview.html").write_text("\n".join(html), encoding="utf-8")
    lines = [
        "# Parrot 文档处理报告",
        "",
        f"阶段：{manifest['stage']}",
        "",
        f"PDF：{summary['pdfs']}；网页：{summary['webpages']}；原始 PDF 页：{len(pages)}；"
        f"切片：{summary['chunks']}。",
        "",
        f"采集失败记录：{summary['failed_collection_records']}；"
        f"缺失网页图片：{summary['missing_web_images']}。",
        "",
        "## 文档",
        "",
    ]
    lines.extend(f"- {d['title']}（{d['document_type']}）" for d in documents)
    lines.extend(["", "## 已知限制", "", *["- " + s for s in summary["limitations"]]])
    (root / "REPORT.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return summary
