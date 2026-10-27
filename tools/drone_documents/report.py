import json
from collections import Counter
from pathlib import Path

import pymupdf

from .common import model_name, read_json, read_jsonl, validate_bundle, write_json


def report(root: Path, *, export_pdf=True) -> dict:
    manifest = validate_bundle(root)
    name = model_name(root, manifest)
    documents = read_jsonl(root / "documents.jsonl")
    pages = read_jsonl(root / "pages.jsonl")
    elements = read_jsonl(root / "elements.jsonl")
    chunks = read_jsonl(root / "chunks.jsonl")
    assets = {a["id"]: a for a in read_jsonl(root / "assets.jsonl")}
    differences = (
        read_jsonl(root / "source_differences.jsonl")
        if (root / "source_differences.jsonl").exists()
        else []
    )
    caption_audit = (
        read_json(root / "caption-quality-report.json")
        if (root / "caption-quality-report.json").exists()
        else None
    )
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
        if selected and export_pdf:
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
    recovered = [
        e
        for e in elements
        if e["element_type"] == "table"
        and e["metadata_json"].get("table_kind") == "led_battery_states"
    ]
    if recovered or differences or caption_audit:
        overview += ["", "## 证据质量检查", ""]
        for table in recovered:
            overview += [
                "### 原图符号恢复",
                "",
                f"原 PDF 第 {table['regions'][0]['source_page_no']} 页；"
                "依据矢量符号和原文图例，未按电量区间猜测灯态。",
                "",
                "| " + " | ".join(table["table_json"]["rows"][0]) + " |",
                "|" + "---|" * len(table["table_json"]["rows"][0]),
            ]
            overview += ["| " + " | ".join(row) + " |" for row in table["table_json"]["rows"][1:]]
        if caption_audit:
            overview += [
                "",
                "### 图片描述检查",
                "",
                f"通过 {caption_audit['accepted']} 张，隔离 {caption_audit['quarantined']} 张；"
                f"其中 {caption_audit['vision_reviewed']} 张通过额外视觉核验。",
                "结构检查与模型复核不能保证完全消除幻觉。隔离说明不进入索引，原图继续保留。",
                "明细：[caption-quality-report.json](caption-quality-report.json)",
                "",
            ]
        if differences:
            overview += [
                "### 参数来源差异",
                "",
                "差异保留两边来源、版本与脚注，未自动认定某个来源正确。",
                "",
            ]
            for difference in differences:
                overview += [
                    f"- {difference['parameter_key']} / {difference['dimension']}；"
                    f"状态：{difference['status']}"
                ]
                for claim in difference["claims"]:
                    source = claim["source"]
                    overview += [
                        f"  - {source['title']}；版本 {source['official_version'] or '未标注'}；"
                        f"原 PDF 页码 {source['source_pages']}；数值 {claim['value']}；"
                        f"测试速度 {claim['conditions'].get('test_speed_kmh')} km/h。"
                    ]
            overview += ["", "明细：[source_differences.jsonl](source_differences.jsonl)", ""]
    if (root / "retrieval-report.json").exists():
        overview += ["", "## 检索验证", ""]
        for test in read_json(root / "retrieval-report.json")["queries"]:
            overview += ["### " + test["question"], ""]
            if test.get("answer_zh"):
                overview += [test["answer_zh"], "", "以下为检索证据：", ""]
                if test.get("answer_verification"):
                    audit = test["answer_verification"]
                    overview += [
                        f"关键条件与引用核验：{audit['status']}；"
                        f"生成/修订 {len(audit['history'])} 次。",
                        "",
                    ]
                if test.get("baseline_comparison"):
                    old_checks = test["baseline_comparison"]["critical_condition_check"]
                    overview += [
                        f"旧答案同项条件检查：{'通过' if old_checks['passed'] else '存在遗漏'}。",
                        "",
                    ]
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
        "source_differences": differences,
        "caption_audit": caption_audit,
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
