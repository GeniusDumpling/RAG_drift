"""Keep source languages and normalize PDF/HTML evidence into document bundles."""

import json
import re
import statistics
from collections import Counter
from datetime import datetime
from functools import lru_cache
from pathlib import Path
from urllib.parse import urljoin, urlsplit

import pymupdf
from bs4 import BeautifulSoup, NavigableString, Tag

from tools.drone_documents.common import (
    Assets,
    digest,
    now,
    progress,
    read_json,
    read_jsonl,
    seal_bundle,
    uid,
    validate_bundle,
    write_json,
    write_jsonl,
)
from tools.drone_documents.extract import _lines, _merge_boxes, _render, rect
from tools.drone_documents.language import language

from . import PIPELINE_VERSION
from .catalog import Catalog, scope_id

IMAGE_PLACEHOLDER = re.compile(r"\[图像[:：]\s*([^\]]*)\]")
PARAMETERS = {
    "weight": "takeoff_weight",
    "maximum flight time": "max_flight_time",
    "max flight time": "max_flight_time",
    "maximum hovering time": "max_hover_time",
}


@lru_cache(maxsize=8192)
def source_language(text: str) -> str:
    detected = language(text).language
    return "zh-Hans" if detected == "zh" else detected


def version_metadata(text: str) -> dict:
    version = re.search(r"\bVersion\s*:\s*([\d.]+)", text, re.I) or re.search(
        r"\b(?:User\s+Guide|User\s+Manual|White\s+Paper)\s+v([\d.]+)", text, re.I
    )
    updated = re.search(r"\bUpdated\s*:\s*([^\n]+)", text, re.I)
    published = re.search(r"\bpublished\s+on\s+([^\n]+)", text, re.I)

    def parse_date(match):
        if not match:
            return None
        normalized = re.sub(r"(\d)(?:st|nd|rd|th)\b", r"\1", match[1].strip())
        for fmt in ["%B %d, %Y", "%B %d %Y", "%Y-%m-%d"]:
            try:
                return datetime.strptime(normalized, fmt).date().isoformat()
            except ValueError:
                pass
        return None

    return {
        "official_version": version[1].rstrip(".") if version else None,
        "updated_at": parse_date(updated),
        "published_at": parse_date(published),
        "version_source": "pdf_front_matter" if version else None,
        "updated_at_source_text": updated[1].strip() if updated else None,
        "published_at_source_text": published[1].strip() if published else None,
    }


def element(doc, kind, text, section, *, regions=None, metadata=None, **extra):
    return {
        "id": "",
        "processing_id": doc["processing_id"],
        "document_version_id": doc["id"],
        "element_type": kind,
        "reading_order": 0,
        "section_path": section,
        "language": source_language(text) if text.strip() else "und",
        "text": text,
        "asset_id": None,
        "table_json": None,
        "regions": regions or [],
        "metadata_json": metadata or {},
        **extra,
    }


def pdf_elements(raw, doc, assets, config):
    pages, elements, links = [], [], []
    with pymupdf.open(stream=raw, filetype="pdf") as pdf:
        instances = list(pdf)
        rotations = [page.rotation for page in instances]
        for page in instances:
            page.set_rotation(0)
        meta = version_metadata("\n".join(p.get_text() for p in instances[:5]))
        doc["official_version"] = meta["official_version"]
        doc["published_at"] = meta["published_at"]
        doc["metadata_json"].update(meta, total_pages=len(pdf))
        toc = pdf.get_toc()
        section_stack = []
        for number, page in enumerate(instances, 1):
            textpage = None
            native = page.get_text()
            method = "native_pdf_text"
            if config["ocr"] == "auto" and len(native.strip()) < 8 and page.get_images():
                textpage = page.get_textpage_ocr(
                    language=config["ocr_languages"], dpi=180, full=True
                )
                native = page.get_text(textpage=textpage)
                method = "tesseract"
            page_id = uid(doc["processing_id"], "page", number)
            pages.append(
                {
                    "id": page_id,
                    "processing_id": doc["processing_id"],
                    "source_page_no": number,
                    "printed_page_label": page.get_label() or str(number),
                    "width": page.rect.width,
                    "height": page.rect.height,
                    "rotation": 0,
                    "language_state": source_language(native),
                    "included": bool(native.strip() or page.get_images() or page.get_drawings()),
                    "inclusion_reason": "source_language_preserved"
                    if native.strip()
                    else "visual_or_blank",
                    "render_asset_id": None,
                    "metadata_json": {
                        "source_rotation": rotations[number - 1],
                        "extraction_method": method,
                        "coordinate_system": "unrotated_pdf_points_top_left",
                    },
                }
            )
            if not pages[-1]["included"]:
                continue
            if config["render_pages"]:
                pages[-1]["render_asset_id"] = _render(page, assets)
            for level, heading, target in toc:
                if target == number:
                    section_stack = section_stack[: level - 1] + [heading.strip()]
            section = " / ".join(section_stack) or doc["title"]
            blocks = _lines(page, textpage)
            median = statistics.median(b["font_size"] for b in blocks) if blocks else 10
            owned, table_boxes = [], []

            def add(
                kind,
                text,
                box,
                *,
                section=section,
                page_id=page_id,
                number=number,
                owned=owned,
                **kwargs,
            ):
                entry = element(
                    doc,
                    kind,
                    text,
                    section,
                    regions=[
                        {
                            "page_id": page_id,
                            "source_page_no": number,
                            "bbox": rect(box),
                        }
                    ],
                    **kwargs,
                )
                entry["id"] = uid(doc["processing_id"], number, kind, len(owned), rect(box))
                owned.append(entry)
                return entry

            paths = page.get_drawings()
            if len(paths) < 4000:
                try:
                    tables = page.find_tables(strategy="lines_strict", paths=paths)
                    for table in tables.tables:
                        matrix = table.extract()
                        if table.row_count < 2 or table.col_count < 2:
                            continue
                        text = "\n".join(" | ".join(str(c or "") for c in row) for row in matrix)
                        if not text.strip(" |\n"):
                            continue
                        add(
                            "table",
                            text,
                            table.bbox,
                            table_json={"rows": matrix},
                            asset_id=_render(page, assets, clip=table.bbox, max_edge=1600),
                        )
                        table_boxes.append(pymupdf.Rect(table.bbox))
                except Exception as exc:
                    pages[-1]["metadata_json"]["table_warning"] = type(exc).__name__
            for block in blocks:
                box = pymupdf.Rect(block["bbox"])
                if any(
                    (box & table).get_area() / max(box.get_area(), 1) > 0.85
                    for table in table_boxes
                ):
                    continue
                text = block["text"].strip()
                if not text:
                    continue
                kind = (
                    "heading"
                    if len(text) < 100 and block["font_size"] > median + 1
                    else "paragraph"
                )
                add(kind, text, box)
            if config["figures"]:
                images = [pymupdf.Rect(info["bbox"]) for info in page.get_image_info()]
                if len(paths) >= 4000:
                    vectors = [page.rect]
                    pages[-1]["metadata_json"]["figure_warning"] = "dense_vectors_page_fallback"
                else:
                    vectors = page.cluster_drawings(drawings=paths)
                for box in _merge_boxes(images + vectors, page.rect):
                    if any(
                        (box & table).get_area() / max(box.get_area(), 1) > 0.7
                        for table in table_boxes
                    ):
                        continue
                    crop = (box + (-9, -9, 9, 9)) & page.rect
                    figure = add(
                        "figure",
                        "",
                        crop,
                        asset_id=_render(page, assets, clip=crop, max_edge=1600),
                        metadata={
                            "status": "pending_description",
                            "extraction": "rendered_vector_and_image_region",
                            "ocr_text": page.get_text(clip=crop),
                        },
                    )
                    context = sorted(
                        [e for e in owned if e["element_type"] in {"paragraph", "heading"}],
                        key=lambda e: abs(e["regions"][0]["bbox"][1] - box.y0),
                    )[:3]
                    figure["metadata_json"]["context_element_ids"] = [e["id"] for e in context]
                    links.extend(
                        {
                            "from_element_id": figure["id"],
                            "to_element_id": e["id"],
                            "relation_type": "nearby_context",
                        }
                        for e in context
                    )
            elements.extend(owned)
            if number % 20 == 0 or number == len(pdf):
                progress("pdf_progress", document=doc["title"], page=number, total=len(pdf))
    return pages, elements, links


def html_blocks(node):
    """Visit semantic blocks once; inline code stays inside prose, pre stays exact."""
    if isinstance(node, NavigableString):
        if str(node).strip():
            yield "paragraph", str(node).strip(), node.parent
        return
    if not isinstance(node, Tag) or node.name in {"script", "style", "nav", "header", "footer"}:
        return
    if node.name in {"h1", "h2", "h3", "h4", "h5", "h6"}:
        yield "heading", node.get_text(" ", strip=True), node
    elif node.name == "pre":
        yield "code", node.get_text(), node
    elif node.name == "table":
        yield "table", "", node
    elif node.name in {"p", "dt", "dd", "li"} and not node.find(
        ["p", "pre", "table", "ul", "ol", "dl", "div"]
    ):
        yield "paragraph", node.get_text(" ", strip=True), node
    else:
        for child in node.children:
            yield from html_blocks(child)


def table_matrix(node):
    """Expand rowspan/colspan so column association survives plain-text retrieval."""
    cells, result = {}, []
    for i, row in enumerate(node.find_all("tr")):
        col = 0
        for cell in row.find_all(["td", "th"], recursive=False):
            while (i, col) in cells:
                col += 1
            rs, cs = int(cell.get("rowspan", 1)), int(cell.get("colspan", 1))
            if not 1 <= rs <= 1000 or not 1 <= cs <= 100:
                raise ValueError("HTML 表格跨度异常")
            for y in range(rs):
                for x in range(cs):
                    cells[i + y, col + x] = cell.get_text(" ", strip=True)
            col += cs
        columns = [c for r, c in cells if r == i]
        result.append([cells.get((i, c), "") for c in range(max(columns, default=-1) + 1)])
    return result


def html_elements(raw, doc):
    soup = BeautifulSoup(raw, "html.parser")
    main = soup.select_one('article[role="main"], article, main, div[role="main"], .content, body')
    if main is None:
        raise ValueError("HTML 没有正文")
    # Archive snapshots contain a source backlink outside main. It is metadata only.
    for p in list(main.find_all("p")):
        if p.get_text(" ", strip=True).startswith("Source:"):
            p.decompose()
    placeholders = IMAGE_PLACEHOLDER.findall(main.get_text())
    doc["metadata_json"].update(
        missing_images=placeholders,
        unarchived_images=[img.get("src", "") for img in main.find_all("img")],
        total_pages=0,
    )
    headings, elements = [], []
    active_anchor = None
    is_spec = (
        "technical-specifications" in doc["source_url"]
        or "technical specifications" in doc["title"].lower()
    )
    for kind, text, node in html_blocks(main):
        if kind != "code":
            text = IMAGE_PLACEHOLDER.sub("", text).strip()
        if kind == "heading":
            level = int(node.name[1])
            headings = [(n, label) for n, label in headings if n < level] + [(level, text)]
            active_anchor = node.get("id") or next(
                (p.get("id") for p in node.parents if p.get("id")), None
            )
        anchor = node.get("id") or next(
            (p.get("id") for p in node.parents if p.get("id")), active_anchor
        )
        metadata = {
            "source_kind": "html",
            "html_anchor": anchor,
            "source_locator": doc["source_url"] + ("#" + anchor if anchor else ""),
            "links": [
                {"text": a.get_text(" ", strip=True), "url": urljoin(doc["source_url"], a["href"])}
                for a in node.find_all("a", href=True)
                if urlsplit(urljoin(doc["source_url"], a["href"])).scheme in {"http", "https"}
            ],
        }
        matrix = None
        if kind == "table":
            matrix = table_matrix(node)
            text = "\n".join(" | ".join(row) for row in matrix)
        if not text.strip():
            continue
        if kind == "paragraph" and is_spec and ":" in text:
            name, value = text.split(":", 1)
            if name.strip() and value.strip():
                kind = "parameter"
                metadata.update(parameter_name=name.strip(), parameter_value=value.strip())
                key = PARAMETERS.get(name.strip().casefold())
                if key:
                    metadata["parameter_key"] = key
        entry = element(
            doc,
            kind,
            text,
            " / ".join(label for _, label in headings) or doc["title"],
            metadata=metadata,
            table_json={"rows": matrix} if matrix else None,
        )
        entry["id"] = uid(doc["processing_id"], "html-element", len(elements))
        elements.append(entry)
    return [], elements, []


def chunk_ranges(text, limit):
    start = 0
    while start < len(text):
        end = min(start + limit, len(text))
        if end < len(text):
            boundary = text.rfind("\n", start + limit // 2, end)
            if boundary >= 0:
                end = boundary + 1
        yield start, end
        start = end


def build_chunks(root: Path):
    manifest = read_json(root / "manifest.json")
    maximum = manifest["config"]["chunk_max_chars"]
    documents = {d["id"]: d for d in read_jsonl(root / "documents.jsonl")}
    chunks, counts, groups = [], Counter(), []
    atomic = {"code", "table", "parameter", "figure_description"}
    for entry in read_jsonl(root / "elements.jsonl"):
        if entry["element_type"] in {"figure", "footnote"} or not entry["text"].strip():
            continue
        if not entry["metadata_json"].get("index_eligible", True):
            continue
        key = (
            entry["document_version_id"],
            entry["section_path"],
            tuple(r["page_id"] for r in entry["regions"]),
            entry["metadata_json"].get("html_anchor"),
        )
        if (
            groups
            and groups[-1][0] == key
            and entry["element_type"] not in atomic
            and groups[-1][1][-1]["element_type"] not in atomic
            and groups[-1][2] + len(entry["text"]) + 2 <= maximum
        ):
            groups[-1][1].append(entry)
            groups[-1][2] += len(entry["text"]) + 2
        else:
            groups.append([key, [entry], len(entry["text"])])
    for _, group, _ in groups:
        entry = group[0]
        doc = documents[entry["document_version_id"]]
        joined = "\n\n".join(e["text"] for e in group)
        for start, end in chunk_ranges(joined, maximum):
            text = joined[start:end]
            if not text.strip():
                continue
            spans, offset = [], 0
            for member in group:
                left, right = max(start, offset), min(end, offset + len(member["text"]))
                if left < right:
                    spans.append(
                        {
                            "element_id": member["id"],
                            "element_start": left - offset,
                            "element_end": right - offset,
                            "chunk_start": left - start,
                            "chunk_end": right - start,
                        }
                    )
                offset += len(member["text"]) + 2
            prefix = (
                f"Manufacturer: Parrot\nArchive scopes: {', '.join(doc['model_slugs'])}\n"
                f"Document: {doc['title']}\nSection: {entry['section_path']}\n"
                f"Applicability: {doc['scope_note']}\n"
            )
            embed = prefix + text
            index = counts[doc["id"]]
            counts[doc["id"]] += 1
            chunks.append(
                {
                    "id": uid(doc["processing_id"], "chunk", index),
                    "processing_id": doc["processing_id"],
                    "document_version_id": doc["id"],
                    "chunk_index": index,
                    "chunk_type": entry["element_type"]
                    if entry["element_type"] in {"table", "parameter", "code", "figure_description"}
                    else "text",
                    "section_path": entry["section_path"],
                    "display_text": text,
                    "embed_text": embed,
                    "content_hash": digest(embed.encode()),
                    "index_language": source_language(text),
                    "element_ids": [s["element_id"] for s in spans],
                    "metadata_json": {
                        **entry["metadata_json"],
                        "manufacturer": "Parrot",
                        "source_pages": sorted(
                            {r["source_page_no"] for e in group for r in e["regions"]}
                        ),
                        "asset_ids": sorted({e["asset_id"] for e in group if e["asset_id"]}),
                        "model_slugs": doc["model_slugs"],
                        "scope_note": doc["scope_note"],
                        "source_kind": doc["metadata_json"]["source_kind"],
                        "source_start": start,
                        "source_end": end,
                        "source_spans": spans,
                    },
                }
            )
    write_jsonl(root / "chunks.jsonl", chunks)
    return chunks


def quality(documents, pages, elements):
    return {
        "documents": len(documents),
        "pdfs": sum(d["metadata_json"]["source_kind"] == "pdf" for d in documents),
        "webpages": sum(d["metadata_json"]["source_kind"] == "html" for d in documents),
        "pages_total": len(pages),
        "pages_included": sum(p["included"] for p in pages),
        "element_types": dict(Counter(e["element_type"] for e in elements)),
        "languages": dict(Counter(e["language"] for e in elements if e["text"].strip())),
        "missing_web_images": sum(
            len(d["metadata_json"].get("missing_images", []))
            + len(d["metadata_json"].get("unarchived_images", []))
            for d in documents
        ),
        "documents_without_text": [
            d["title"]
            for d in documents
            if not any(
                e["processing_id"] == d["processing_id"] and e["text"].strip() for e in elements
            )
        ],
        "limitations": [
            "保留来源语言；未自动翻译正文。代码切片保留原文，长块按偏移拆分。",
            "PDF 版面依靠原生坐标与几何规则；复杂多栏、无边框表格需抽查。",
            "PDF 图文关联为同页几何邻近，不代表语义配图已经确认。",
            "HTML 图片占位符无法恢复原图；缺失图片需另行采集。",
            "SDK scope 表示软件项目，不表示兼容所有机型或版本。",
            "参数保留原文条件；没有自动比较或消解不同配置的数值差异。",
        ],
    }


def prepare(
    input_root: Path,
    output: Path,
    *,
    scopes=None,
    kind="all",
    types=None,
    limit=None,
    ocr="auto",
    figures=True,
    render_pages=True,
    chunk_max_chars=2000,
    resume=False,
):
    if output.exists() and not resume:
        raise ValueError("输出目录已存在；请使用新目录或 --resume 恢复本地解析")
    if ocr not in {"auto", "off"} or chunk_max_chars < 200 or (limit is not None and limit < 1):
        raise ValueError("无效的 OCR、切片长度或 limit 参数")
    catalog = Catalog(input_root)
    selected = catalog.select(scopes, kind, types, limit)
    if not selected:
        raise ValueError("所选范围没有可处理的本地文档；请查看 list-scopes 和采集失败记录")
    # Validate every selected source before creating any output.
    for row in selected:
        catalog.read(row)
    config = {
        "pipeline": PIPELINE_VERSION,
        "pymupdf": pymupdf.__version__,
        "language_policy": "preserve-source-languages",
        "index_languages": [],
        "chunk_max_chars": chunk_max_chars,
        "chunker": "adjacent-source-blocks-v1",
        "ocr": ocr,
        "ocr_languages": "eng+chi_sim",
        "figures": figures,
        "render_pages": render_pages,
    }
    if figures:
        from app.core.config import get_settings

        from tools.drone_documents.caption_quality import QUALITY_VERSION
        from tools.drone_documents.enrich import PROMPT_VERSION, SYSTEM

        config["figure_model"] = get_settings().vlm_model
        config["figure_prompt_hash"] = digest(SYSTEM.replace("DJI", "Parrot").encode())
        config["figure_prompt_version"] = PROMPT_VERSION
        config["caption_quality_version"] = QUALITY_VERSION
    config_hash = digest(json.dumps(config, sort_keys=True).encode())
    state = {
        "config": config,
        "input_root": str(input_root.resolve()),
        "selection": {"scopes": scopes, "kind": kind, "types": types, "limit": limit},
        "sources": selected,
        "failures": catalog.failures,
    }
    if output.exists():
        if (
            not (output / "prepare-state.json").exists()
            or read_json(output / "prepare-state.json") != state
        ):
            raise ValueError("恢复配置或输入索引已变化，请使用新输出目录")
        if (output / "manifest.json").exists():
            existing = read_json(output / "manifest.json")
            if existing.get("files"):
                validate_bundle(output)
                return existing
    else:
        output.mkdir(parents=True)
        write_json(output / "prepare-state.json", state)
    assets = Assets(output)
    documents, pages, elements, links = [], [], [], []
    aliases_scopes = sorted({a["scope"] for row in selected for a in row["aliases"]})
    for row in selected:
        raw = catalog.read(row)
        doc_id = uid(
            "parrot-document",
            row["kind"],
            row["sha256"],
            row["url"] if row["kind"] == "html" else "",
        )
        checkpoint = output / "checkpoints" / (uid(doc_id, config_hash) + ".json")
        if checkpoint.exists():
            cached = read_json(checkpoint)
            for asset in cached["assets"]:
                path = (output / asset["storage_key"]).resolve()
                if (
                    not path.is_relative_to(output.resolve())
                    or digest(path.read_bytes()) != asset["sha256"]
                ):
                    raise ValueError("检查点资源校验失败；请使用新输出目录")
                assets.rows[asset["id"]] = asset
            documents.append(cached["document"])
            pages.extend(cached["pages"])
            elements.extend(cached["elements"])
            links.extend(cached["links"])
            progress("checkpoint_reused", title=row["title"])
            continue
        sdk = row["scope"].startswith("sdk-")
        doc = {
            "id": doc_id,
            "processing_id": uid(doc_id, config_hash),
            "document_key": "parrot:" + row["kind"] + ":" + row["url"],
            "title": row["title"],
            "document_type": row["document_type"],
            "source_url": row["url"],
            "published_at": None,
            "official_version": None,
            "source_asset_id": assets.add(
                raw, "." + row["kind"], "application/pdf" if row["kind"] == "pdf" else "text/html"
            ),
            "model_slugs": sorted({scope_id(a["scope"]) for a in row["aliases"]}),
            "scope_note": (
                "软件项目归档范围，不等于所有无人机均支持；以原文明确的 API、版本和机型为准。"
                if sdk
                else "产品系列归档范围；具体变体、配置、软件版本和操作条件以原文为准。"
            ),
            "config_hash": config_hash,
            "parser_version": PIPELINE_VERSION,
            "metadata_json": {
                "manufacturer": "Parrot",
                "source_kind": row["kind"],
                "source_path": row["local_path"],
                "sha256": row["sha256"],
                "scope_kind": "sdk" if sdk else "product",
                "archive_associations": row["aliases"],
            },
        }
        result = (
            pdf_elements(raw, doc, assets, config)
            if row["kind"] == "pdf"
            else html_elements(raw, doc)
        )
        source_pages, source_elements, source_links = result
        if not any(e["text"].strip() for e in source_elements):
            doc["metadata_json"]["warning"] = "no_extractable_text"
        asset_ids = (
            {doc["source_asset_id"]}
            | {e["asset_id"] for e in source_elements if e["asset_id"]}
            | {p["render_asset_id"] for p in source_pages if p["render_asset_id"]}
        )
        write_json(
            checkpoint,
            {
                "document": doc,
                "pages": source_pages,
                "elements": source_elements,
                "links": source_links,
                "assets": [assets.rows[key] for key in sorted(asset_ids)],
            },
        )
        documents.append(doc)
        pages.extend(source_pages)
        elements.extend(source_elements)
        links.extend(source_links)
        progress(
            "document_prepared", title=doc["title"], kind=row["kind"], elements=len(source_elements)
        )
    for order, entry in enumerate(elements):
        entry["reading_order"] = order
    models = [{"id": uid("model", scope_id(s)), **catalog.scopes[s]} for s in aliases_scopes]
    for name, records in [
        ("documents", documents),
        ("pages", pages),
        ("elements", elements),
        ("links", links),
        ("models", models),
        ("assets", list(assets.rows.values())),
    ]:
        write_jsonl(output / (name + ".jsonl"), records)
    manifest = {
        "format": "drone_documents.v1",
        "manufacturer": "Parrot",
        "created_at": now(),
        "model_slug": scope_id(scopes[0]) if scopes and len(scopes) == 1 else "parrot:all",
        "model_name": "Parrot " + ", ".join(scopes or ["all archived scopes"]),
        "scope_slugs": [scope_id(s) for s in (scopes or aliases_scopes)],
        "pipeline_version": PIPELINE_VERSION,
        "config": config,
        "config_hash": config_hash,
        "input_root": str(input_root.resolve()),
        "stage": "prepared",
        "selection": {"scopes": scopes, "kind": kind, "types": types, "limit": limit},
    }
    write_json(output / "manifest.json", manifest)
    chunks = build_chunks(output)
    write_jsonl(output / "collection_failures.jsonl", catalog.failures)
    seal_bundle(output, manifest)
    summary = quality(documents, pages, elements)
    summary.update(chunks=len(chunks), failed_collection_records=len(catalog.failures))
    write_json(output / "quality_report.json", summary)
    validate_bundle(output)
    return manifest
