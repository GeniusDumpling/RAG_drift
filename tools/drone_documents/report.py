import json
from collections import Counter
from pathlib import Path

import pymupdf

from .common import model_name, read_json, read_jsonl, validate_bundle, write_json


def report(root: Path) -> dict:
    manifest = validate_bundle(root)
    name = model_name(root, manifest)
    documents = read_jsonl(root / "documents.jsonl")
    pages = read_jsonl(root / "pages.jsonl")
    elements = read_jsonl(root / "elements.jsonl")
    chunks = read_jsonl(root / "chunks.jsonl")
    assets = {a["id"]: a for a in read_jsonl(root / "assets.jsonl")}
    folder = root / "documents"
    folder.mkdir(exist_ok=True)
    summaries = []
    for doc in documents:
        selected = [
            p for p in pages if p["processing_id"] == doc["processing_id"] and p["included"]
        ]
        owned = [e for e in elements if e["processing_id"] == doc["processing_id"]]
        path = folder / (doc["id"] + ".md")
        lines = [
            "# " + doc["title"],
            "",
            "官方来源：" + doc["source_url"],
            "",
            "仅中文检索内容；英文型号、界面标识和单位按上下文保留。",
            "",
        ]
        if doc["scope_note"]:
            lines += ["> 适用范围：" + doc["scope_note"], ""]
        if selected:
            with pymupdf.open(root / assets[doc["source_asset_id"]]["storage_key"]) as source:
                subset = pymupdf.open()
                for page in selected:
                    subset.insert_pdf(
                        source,
                        from_page=page["source_page_no"] - 1,
                        to_page=page["source_page_no"] - 1,
                    )
                subset.save(folder / (doc["id"] + "-zh-pages.pdf"), garbage=3, deflate=True)
                subset.close()
        # The Markdown is a reader-friendly derivative; JSONL retains exact elements and links.
        for page in selected or [None]:
            if page:
                lines += [f"## 原 PDF 第 {page['source_page_no']} 页", ""]
                page_elements = [
                    e for e in owned if any(r["page_id"] == page["id"] for r in e["regions"])
                ]
            else:
                page_elements = owned
            for element in page_elements:
                if element["element_type"] == "figure":
                    asset = assets[element["asset_id"]]
                    lines += [f"![原始图示](../{asset['storage_key']})", ""]
                elif element["text"].strip():
                    if element["metadata_json"].get("model_generated"):
                        lines += ["> 以下为模型生成的图示说明，原图为核验依据。", ""]
                    lines += [element["text"], ""]
        path.write_text("\n".join(lines), encoding="utf-8")
        summaries.append(
            {
                "title": doc["title"],
                "id": doc["id"],
                "total_pages": doc["metadata_json"].get("total_pages", 0),
                "selected_pages": [p["source_page_no"] for p in selected],
                "elements": dict(Counter(e["element_type"] for e in owned)),
                "chunks": sum(c["processing_id"] == doc["processing_id"] for c in chunks),
            }
        )
    overview = [
        f"# {name} 文档处理结果",
        "",
        f"处理版本：{manifest['pipeline_version']}",
        "",
        "| 文档 | 原始页数 | 保留页数 | 切片数 |",
        "|---|---:|---:|---:|",
    ]
    for doc in summaries:
        overview.append(
            f"| [{doc['title']}](documents/{doc['id']}.md) | {doc['total_pages']} | "
            f"{len(doc['selected_pages'])} | {doc['chunks']} |"
        )
    overview += [
        "",
        "交互预览：[preview.html](preview.html)",
        "",
        "中文保留页码和逐元素坐标见 pages.jsonl / elements.jsonl。",
        "图片描述是模型生成的派生信息，需通过关联原图核验。",
        "混排页的阅读副本保留整页，只有中文内容进入文本索引。",
        "同页图文采用几何邻近关联；复杂多栏、无边框表格及关键操作图仍需抽查。",
    ]
    if (root / "retrieval-report.json").exists():
        overview += ["", "## 检索验证", ""]
        for test in read_json(root / "retrieval-report.json")["queries"]:
            overview += ["### " + test["question"], ""]
            if test.get("answer_zh"):
                overview += [test["answer_zh"], "", "以下为检索证据：", ""]
            for index, evidence in enumerate(test["evidence"], 1):
                overview += [
                    f"- [{index}] {evidence['title']}；原 PDF 页码：{evidence['source_pages']}；"
                    f"相似度：{evidence['score']:.3f}",
                    "",
                    evidence["text"][:1000],
                    "",
                ]
    (root / "REPORT.md").write_text("\n".join(overview), encoding="utf-8")
    data = {
        "model_slug": manifest["model_slug"],
        "model_name": name,
        "documents": documents,
        "pages": pages,
        "elements": elements,
        "chunks": chunks,
        "assets": assets,
        "quality": read_json(root / "quality_report.json"),
        "retrieval": read_json(root / "retrieval-report.json")
        if (root / "retrieval-report.json").exists()
        else None,
    }
    packed = json.dumps(data, ensure_ascii=False).replace("<", "\\u003c")
    preview = PREVIEW.replace("__DATA__", packed)
    (root / "preview.html").write_text(preview, encoding="utf-8")
    result = {
        "model_slug": manifest["model_slug"],
        "model_name": name,
        "preview": str(root / "preview.html"),
        "report": str(root / "REPORT.md"),
        "documents": summaries,
    }
    write_json(root / "report-summary.json", result)
    return result


PREVIEW = Path(__file__).with_name("preview_template.html").read_text(encoding="utf-8")
