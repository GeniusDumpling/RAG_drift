import json
import re
import statistics
from collections import Counter
from pathlib import Path

import pymupdf
from bs4 import BeautifulSoup, Tag

from . import PIPELINE_VERSION
from .catalog import Catalog, indexed_path
from .common import Assets, digest, now, progress, seal_bundle, uid, write_json, write_jsonl
from .language import (
    FOREIGN_MARKERS,
    HAN,
    MARKERS,
    chinese_dominant,
    keep_line,
    language,
    page_marker,
    page_markers,
    shared_visual_text,
)


def doc_type(title: str) -> str:
    for keyword, kind in [
        ("LUT", "shared_reference"),
        ("用户手册", "user_manual"),
        ("使用手册", "user_manual"),
        ("USER MANUAL", "user_manual"),
        ("快速", "quick_start"),
        ("QUICK START", "quick_start"),
        ("发布", "release_notes"),
        ("RELEASE NOTES", "release_notes"),
        ("安全", "safety_guidelines"),
        ("免责声明", "safety_guidelines"),
        ("SAFETY", "safety_guidelines"),
    ]:
        if keyword in title.upper():
            return kind
    return "reference"


def rect(box) -> list[float]:
    return [round(float(x), 3) for x in box]


def selected_text(text: str, *, dominant: bool, marker: str | None) -> str:
    kept = []
    for line in text.splitlines():
        # Some quick-start labels contain multiple translations on one line.
        pieces = [line] if dominant else re.split(r"\s+/\s+|\s*\|\s*", line)
        for piece in pieces:
            if keep_line(piece, dominant=dominant, marker=marker):
                kept.append(piece.strip())
    return "\n".join(kept)


def _lines(page, textpage=None) -> list[dict]:
    data = page.get_text(
        "dict",
        sort=True,
        flags=pymupdf.TEXTFLAGS_DICT & ~pymupdf.TEXT_PRESERVE_IMAGES,
        textpage=textpage,
    )
    result = []
    for block in data["blocks"]:
        if block["type"] != 0:
            continue
        lines = block["lines"]
        text = "\n".join("".join(span["text"] for span in line["spans"]) for line in lines)
        sizes = [s["size"] for line in lines for s in line["spans"]]
        result.append(
            {"text": text, "bbox": rect(block["bbox"]), "font_size": max(sizes, default=10)}
        )
    return result


def _render(page, assets: Assets, *, clip=None, max_edge=1800) -> str:
    box = pymupdf.Rect(clip) if clip else page.rect
    scale = min(2.5, max_edge / max(box.width, box.height))
    pixmap = page.get_pixmap(matrix=pymupdf.Matrix(scale, scale), clip=box, alpha=False)
    return assets.add(
        pixmap.tobytes("png"), ".png", "image/png", width=pixmap.width, height=pixmap.height
    )


def _merge_boxes(boxes: list, page_rect) -> list:
    merged = []
    for box in boxes:
        box = pymupdf.Rect(box) & page_rect
        if box.width < 25 or box.height < 22 or box.get_area() < 900:
            continue
        for previous in merged:
            intersection = (previous & box).get_area()
            if intersection / max(1, min(previous.get_area(), box.get_area())) > 0.2:
                previous |= box
                break
        else:
            merged.append(box)
    return sorted(merged, key=lambda b: b.get_area(), reverse=True)[:8]


def _section(toc: list, page_no: int, title: str) -> str:
    stack: list[str] = []
    for level, heading, target in toc:
        if target > page_no:
            break
        if HAN.search(heading):
            stack = stack[: level - 1]
            stack.append(heading.strip())
    return " / ".join(stack) or title


DOCUMENT_TYPES = {
    "user_manual",
    "quick_start",
    "release_notes",
    "safety_guidelines",
    "shared_reference",
    "reference",
}


def prepare(
    input_root: Path,
    output: Path,
    model: str,
    *,
    include_specs=True,
    ocr="auto",
    document_types: list[str] | None = None,
) -> dict:
    if output.exists():
        raise ValueError("输出目录已存在；请使用新目录，或继续 enrich/import/index 阶段。")
    if ocr not in {"auto", "off"}:
        raise ValueError("OCR 策略必须为 auto 或 off")
    catalog = Catalog(input_root)
    selected_model = catalog.model(model)
    rows = catalog.sources(model)
    if document_types:
        if not set(document_types) <= DOCUMENT_TYPES:
            raise ValueError("未知的文档类型")
        rows = [r for r in rows if doc_type(r["title"]) in document_types]
    if not rows and not (include_specs and (catalog.spec(model) or {}).get("html_path")):
        raise ValueError(f"型号 {model} 没有可处理的 PDF 或技术参数")
    output.mkdir(parents=True)
    assets = Assets(output)
    documents, pages, elements, links = [], [], [], []
    config = {
        "pipeline": PIPELINE_VERSION,
        "pymupdf": pymupdf.__version__,
        "language_policy": "zh-Hans-and-shared-visuals",
        "chunk_max_chars": 1100,
        "page_max_edge": 1800,
        "figure_max_edge": 1600,
        "ocr": ocr,
        "ocr_languages": "chi_sim+eng",
    }
    config_hash = digest(json.dumps(config, sort_keys=True).encode())
    model_rows = {model: {"id": uid("model", model), **selected_model, "manufacturer": "DJI"}}
    for row in rows:
        source = indexed_path(input_root, row["local_path"])
        data = source.read_bytes()
        sha = digest(data)
        if sha != row["sha256"]:
            raise ValueError(f"原文件哈希与索引不一致: {row['title']}")
        document_id = uid("document-version", sha)
        processing_id = uid(document_id, config_hash)
        associations = catalog.by_hash[sha]
        for association in associations:
            slug = association["slug"]
            model_rows[slug] = {
                "id": uid("model", slug),
                "slug": slug,
                "name": association["name"],
                "manufacturer": "DJI",
            }
        kind = doc_type(row["title"])
        scope_note = (
            "下载索引的机型关联仅用于资料归档；参数、配件和步骤以原文明确的适用型号、"
            "版本及条件为准，不得将某一型号专属内容直接用于其他型号。"
            if kind == "shared_reference" or len({r["slug"] for r in associations}) > 1
            else ""
        )
        document = {
            "id": document_id,
            "processing_id": processing_id,
            "document_key": Path(row["local_path"].replace("\\", "/")).parent.name + ":" + kind,
            "title": row["title"],
            "document_type": kind,
            "source_url": row["url"],
            "published_at": row["date"] or None,
            "official_version": (
                re.search(r"[vV]\s*(\d+(?:\.\d+)*)", row["title"]).group(1)
                if re.search(r"[vV]\s*(\d+(?:\.\d+)*)", row["title"])
                else None
            ),
            "source_asset_id": assets.add(data, ".pdf", "application/pdf"),
            "model_slugs": sorted({r["slug"] for r in associations}),
            "scope_note": scope_note,
            "config_hash": config_hash,
            "parser_version": config["pymupdf"],
            "metadata_json": {"source_path": row["local_path"], "sha256": sha},
        }
        documents.append(document)
        pdf = pymupdf.open(stream=data, filetype="pdf")
        # TextPage objects belong to the exact Page instance that created them.
        # Keep these instances alive and normalize rotation before native/OCR extraction.
        pdf_pages = list(pdf)
        original_rotations = [p.rotation for p in pdf_pages]
        for p in pdf_pages:
            p.set_rotation(0)
        textpages = {}
        texts = []
        for i, p in enumerate(pdf_pages):
            native = p.get_text()
            if ocr == "auto" and len(native.strip()) < 8 and p.get_images():
                # Scanned pages are classified using OCR instead of being discarded
                # for having no selectable Chinese text. Fail explicitly if unavailable.
                textpages[i] = p.get_textpage_ocr(language="chi_sim+eng", dpi=180, full=True)
                native = p.get_text(textpage=textpages[i])
            texts.append(native)
        markers = [page_marker(t) for t in texts]
        for i, text in enumerate(texts):
            if markers[i] is None and len(HAN.findall(text)) > 20:
                if language(text).language == "zh-Hant":
                    markers[i] = "zh-Hant"
        dominant = [
            chinese_dominant(t) or m == "zh-Hans" for t, m in zip(texts, markers, strict=False)
        ]
        default_chinese = sum(dominant) / max(len(pdf), 1) > 0.6
        is_quick_start = kind == "quick_start"
        footer_counts = Counter()
        for i, (page, _text) in enumerate(zip(pdf_pages, texts, strict=False)):
            for block in _lines(page, textpages.get(i)):
                box = block["bbox"]
                if box[1] < page.rect.height * 0.05 or box[3] > page.rect.height * 0.94:
                    footer_counts[re.sub(r"\d+", "#", block["text"]).strip()] += 1
        toc = pdf.get_toc()
        for page_index, page in enumerate(pdf_pages):
            page_no = page_index + 1
            page_id = uid(processing_id, "page", page_no)
            original_rotation = original_rotations[page_index]
            text = texts[page_index]
            marker = markers[page_index]
            dominant_page = dominant[page_index]
            if is_quick_start or len(page_markers(text)) > 1:
                dominant_page = False
            chinese = selected_text(text, dominant=dominant_page, marker=marker)
            has_chinese = bool(HAN.search(chinese))
            # Never treat unmarked foreign prose as a shared visual. Blank pages
            # also do not qualify; technical-label diagrams can be shared.
            shared = (
                (is_quick_start or default_chinese)
                and marker is None
                and not has_chinese
                and shared_visual_text(text)
                and bool(page.get_image_info() or page.get_drawings())
            )
            included = has_chinese or shared
            if marker in FOREIGN_MARKERS:
                included = False
            page_row = {
                "id": page_id,
                "processing_id": processing_id,
                "source_page_no": page_no,
                "printed_page_label": page.get_label() or str(page_no),
                "width": page.rect.width,
                "height": page.rect.height,
                "rotation": 0,
                "language_state": marker
                or ("zh-Hans" if dominant_page else "mixed" if has_chinese else "und"),
                "included": included,
                "inclusion_reason": "chinese_text"
                if has_chinese
                else "shared_technical_visuals"
                if shared
                else "non_chinese_or_blank",
                "render_asset_id": None,
                "metadata_json": {
                    "source_rotation": original_rotation,
                    "extraction_method": "tesseract_chi_sim_eng"
                    if page_index in textpages
                    else "native_pdf_text",
                    "han_count": len(HAN.findall(text)),
                    "coordinate_system": "unrotated_pdf_points_top_left",
                },
            }
            pages.append(page_row)
            if not included:
                continue
            page_row["render_asset_id"] = _render(page, assets)
            section = _section(toc, page_no, document["title"])
            blocks = _lines(page, textpages.get(page_index))
            median_font = statistics.median([b["font_size"] for b in blocks]) if blocks else 10
            page_elements = []

            def add(
                kind,
                text,
                box,
                *,
                processing_id=processing_id,
                page_no=page_no,
                document_id=document_id,
                section=section,
                page_id=page_id,
                page_elements=page_elements,
                **kwargs,
            ):
                element = {
                    "id": uid(processing_id, page_no, kind, rect(box)),
                    "processing_id": processing_id,
                    "document_version_id": document_id,
                    "element_type": kind,
                    "reading_order": len(elements),
                    "section_path": section,
                    "language": "und" if kind == "figure" else "zh-Hans",
                    "text": text,
                    "asset_id": None,
                    "table_json": None,
                    "metadata_json": {},
                    "regions": [{"page_id": page_id, "source_page_no": page_no, "bbox": rect(box)}],
                    **kwargs,
                }
                elements.append(element)
                page_elements.append(element)
                return element

            paths = page.get_drawings()
            table_boxes = []
            if len(paths) < 4000 and marker not in FOREIGN_MARKERS:
                try:
                    tables = page.find_tables(strategy="lines_strict", paths=paths)
                    for table in tables.tables:
                        matrix = table.extract()
                        table_text = "\n".join(" | ".join(str(c or "") for c in r) for r in matrix)
                        if (
                            table.row_count < 2
                            or table.col_count < 2
                            or not HAN.search(table_text)
                            or (not dominant_page and language(table_text).language == "zh-Hant")
                        ):
                            continue
                        # In multilingual tables, filter row text before indexing.
                        if not dominant_page:
                            matrix = [
                                r
                                for r in matrix
                                if HAN.search(" ".join(str(c or "") for c in r))
                                and language(" ".join(str(c or "") for c in r)).language
                                in {"zh", "zh-Hans"}
                            ]
                        if not matrix:
                            continue
                        table_text = "\n".join(" | ".join(str(c or "") for c in r) for r in matrix)
                        add(
                            "table",
                            table_text,
                            table.bbox,
                            table_json={"rows": matrix},
                            asset_id=_render(page, assets, clip=table.bbox, max_edge=1600),
                        )
                        table_boxes.append(pymupdf.Rect(table.bbox))
                except Exception as exc:
                    page_row["metadata_json"]["table_warning"] = type(exc).__name__
            for block in blocks:
                box = pymupdf.Rect(block["bbox"])
                normalized = re.sub(r"\d+", "#", block["text"]).strip()
                furniture = (
                    box.y1 > page.rect.height * 0.94 or box.y0 < page.rect.height * 0.05
                ) and (
                    footer_counts[normalized] >= 3
                    or re.search(r"©|Copyright|版权所有|^\s*\d+\s*$", block["text"], re.I)
                )
                if furniture:
                    continue
                # Keep native text as a fallback when table reconstruction is uncertain.
                if any((box & t).get_area() / max(box.get_area(), 1) > 0.85 for t in table_boxes):
                    continue
                cleaned = selected_text(block["text"], dominant=dominant_page, marker=marker)
                if not cleaned or cleaned.upper() in MARKERS:
                    continue
                kind = (
                    "heading"
                    if (len(cleaned) < 70 and block["font_size"] > median_font + 1)
                    else "paragraph"
                )
                add(kind, cleaned, box)

            image_boxes = [pymupdf.Rect(info["bbox"]) for info in page.get_image_info()]
            vector_boxes = page.cluster_drawings(drawings=paths)
            figure_boxes = _merge_boxes(image_boxes + vector_boxes, page.rect)
            for box in figure_boxes:
                if any((box & t).get_area() / max(box.get_area(), 1) > 0.7 for t in table_boxes):
                    continue
                if box.get_area() > page.rect.get_area() * 0.9 and len(chinese) > 1000:
                    continue
                if box.y0 > page.rect.height * 0.95 or box.y1 < page.rect.height * 0.04:
                    continue
                crop = (box + (-9, -9, 9, 9)) & page.rect
                figure = add(
                    "figure",
                    "",
                    crop,
                    asset_id=_render(page, assets, clip=crop, max_edge=1600),
                    metadata_json={
                        "status": "pending_description",
                        "extraction": "rendered_vector_and_image_region",
                        "ocr_text": selected_text(
                            page.get_text(clip=crop), dominant=dominant_page, marker=marker
                        ),
                        "native_drawings": len(paths),
                    },
                )
                contextual = [
                    e for e in page_elements if e["element_type"] in {"paragraph", "heading"}
                ]
                contextual.sort(
                    key=lambda e: abs(pymupdf.Rect(e["regions"][0]["bbox"]).y0 - box.y0)
                )
                for element in contextual[:3]:
                    links.append(
                        {
                            "from_element_id": figure["id"],
                            "to_element_id": element["id"],
                            "relation_type": "nearby_context",
                        }
                    )
                figure["metadata_json"]["context_element_ids"] = [e["id"] for e in contextual[:3]]
            progress(
                "page_extracted",
                document=document["title"],
                page=page_no,
                total_pages=len(pdf),
                elements=len(page_elements),
            )
        document["metadata_json"]["total_pages"] = len(pdf)
        pdf.close()
    if include_specs:
        _specifications(input_root, model, config_hash, assets, documents, elements)
    for name, data in [
        ("documents", documents),
        ("models", list(model_rows.values())),
        ("pages", pages),
        ("elements", elements),
        ("links", links),
        ("assets", list(assets.rows.values())),
    ]:
        write_jsonl(output / f"{name}.jsonl", data)
    manifest = {
        "format": "drone_documents.v1",
        "created_at": now(),
        "model_slug": model,
        "model_name": selected_model["name"],
        "pipeline_version": PIPELINE_VERSION,
        "config": config,
        "config_hash": config_hash,
        "input_root": str(input_root.resolve()),
        "selection": {"document_types": document_types, "include_specs": include_specs},
        "stage": "prepared",
    }
    annotate_software_sections(output)
    build_chunks(output)
    seal_bundle(output, manifest)
    write_json(output / "quality_report.json", quality(documents, pages, elements))
    progress("prepared", **quality(documents, pages, elements))
    return manifest


def _specifications(input_root, model, config_hash, assets, documents, elements):
    row = Catalog(input_root).spec(model)
    if not row:
        return
    if not row.get("html_path"):
        return
    path = indexed_path(input_root, row["html_path"])
    raw = path.read_bytes()
    if row.get("sha256") and digest(raw) != row["sha256"]:
        raise ValueError(f"技术参数文件哈希与索引不一致: {model}")
    parameters = parse_specifications(raw)
    expected = int(row["parameter_count"]) if row.get("parameter_count") else None
    if expected is not None and len(parameters) != expected:
        raise ValueError(
            f"技术参数数量与索引不一致: {model}；预期 {expected}，实际 {len(parameters)}"
        )
    document_id = uid("document-version", digest(raw))
    processing_id = uid(document_id, config_hash)
    document = {
        "id": document_id,
        "processing_id": processing_id,
        "document_key": model + ":technical_specifications",
        "title": row["name"] + " 技术参数",
        "document_type": "technical_specifications",
        "source_url": row["source_url"],
        "source_asset_id": assets.add(raw, ".html", "text/html"),
        "model_slugs": [model],
        "scope_note": row.get("error", ""),
        "published_at": None,
        "official_version": None,
        "config_hash": config_hash,
        "parser_version": "html-native",
        "metadata_json": {
            "total_pages": 0,
            "collected_at": row.get("collected_at"),
            "source_sha256": digest(raw),
            "collection_status": row.get("status", "unknown"),
            "parameter_count": len(parameters),
            "empty_parameter_values": [p["name"] for p in parameters if not p["value"]],
        },
    }
    documents.append(document)
    for i, parameter in enumerate(parameters):
        name, value, section = (parameter[key] for key in ["name", "value", "section"])
        elements.append(
            {
                "id": uid(processing_id, i),
                "processing_id": processing_id,
                "document_version_id": document_id,
                "element_type": "parameter",
                "reading_order": len(elements),
                "section_path": section,
                "language": "zh-Hans",
                "text": name + "：" + value,
                "asset_id": None,
                "table_json": None,
                "regions": [],
                "metadata_json": {
                    "parameter_name": name,
                    "parameter_value": value,
                    "html_anchor": section,
                },
            }
        )


def parse_specifications(html: str | bytes) -> list[dict]:
    """Current support pages, legacy h5 rows and multi-variant specification pages."""
    soup = BeautifulSoup(html, "html.parser")
    for element in soup(["script", "style"]):
        element.decompose()
    variant, section = "", "技术参数"
    parameters = []
    for node in soup.find_all(["h2", "h3", "strong"]):
        label = node.get_text(" ", strip=True)
        if node.name == "h3":
            variant, section = label, "技术参数"
        elif node.name == "h2":
            if label and label != "技术参数":
                section = label
        else:
            container = node.parent
            if isinstance(container, Tag) and container.name == "h5":
                container = container.parent.parent
            if not isinstance(container, Tag):
                continue
            name = node.get_text("\n", strip=True)
            value = container.get_text("\n", strip=True).removeprefix(name).strip()
            parameters.append(
                {
                    "name": label or "说明",
                    "value": value,
                    "section": " / ".join(filter(None, [variant, section])),
                }
            )
    return parameters


def annotate_software_sections(root: Path) -> None:
    """Keep mutually exclusive application workflows apart inside shared LUT guides."""
    from .common import read_jsonl

    documents = read_jsonl(root / "documents.jsonl")
    pages = read_jsonl(root / "pages.jsonl")
    elements = read_jsonl(root / "elements.jsonl")
    assets = {a["id"]: a for a in read_jsonl(root / "assets.jsonl")}
    headings = {
        "使用blackmagicdavinciresolve": "davinci_resolve",
        "adobepremiereprocc": "premiere_pro",
        "使用applefinalcutprox": "final_cut_pro",
    }
    lookup = {}
    for doc in documents:
        if doc["document_type"] != "shared_reference":
            continue
        active = None
        with pymupdf.open(root / assets[doc["source_asset_id"]]["storage_key"]) as pdf:
            for page_no, page in enumerate(pdf, 1):
                for line in page.get_text().splitlines():
                    key = re.sub(r"\s+", "", line).lower()
                    if key in headings:
                        active = headings[key]
                lookup[(doc["processing_id"], page_no)] = active
    for page in pages:
        value = lookup.get((page["processing_id"], page["source_page_no"]))
        if value:
            page["metadata_json"]["software_context"] = value
    for element in elements:
        values = {
            lookup.get((element["processing_id"], r["source_page_no"])) for r in element["regions"]
        } - {None}
        if len(values) == 1:
            element["metadata_json"]["software_context"] = values.pop()
    write_jsonl(root / "pages.jsonl", pages)
    write_jsonl(root / "elements.jsonl", elements)


def build_chunks(root: Path) -> list[dict]:
    from .common import read_jsonl

    documents = read_jsonl(root / "documents.jsonl")
    elements = read_jsonl(root / "elements.jsonl")
    chunks = []
    for document in documents:
        owned = [e for e in elements if e["processing_id"] == document["processing_id"]]
        text_elements = [e for e in owned if e["text"].strip() and e["element_type"] != "figure"]
        # Never join unrelated pages. Keep the full element IDs for source highlighting.
        groups = []
        for element in text_elements:
            key = (element["section_path"], tuple(r["page_id"] for r in element["regions"]))
            atomic = {"parameter", "figure_description", "table"}
            if (
                groups
                and groups[-1][0] == key
                and element["element_type"] not in atomic
                and all(e["element_type"] not in atomic for e in groups[-1][1])
                and sum(len(e["text"]) for e in groups[-1][1]) + len(element["text"]) < 1100
            ):
                groups[-1][1].append(element)
            else:
                groups.append((key, [element]))
        index = 0
        for _, group in groups:
            text = "\n\n".join(e["text"] for e in group)
            # Split long native blocks at line/sentence boundaries; retain a short
            # preceding context when an operation continues into another chunk.
            parts, current = [], ""
            for line in re.split(r"(?<=[。！？])\s*|\n", text):
                if current and len(current) + len(line) > 1100:
                    parts.append(current)
                    current = current[-120:] + "\n" + line
                else:
                    current += ("\n" if current else "") + line
            if current.strip():
                parts.append(current.strip())
            for part in parts:
                types = {e["element_type"] for e in group}
                if not HAN.search(part) and not types & {"parameter", "table"}:
                    # Numeric step markers and shared app labels remain source
                    # elements, but do not become standalone Chinese evidence.
                    continue
                kind = (
                    "figure_description"
                    if types == {"figure_description"}
                    else (
                        "parameter"
                        if "parameter" in types
                        else "table"
                        if "table" in types
                        else "text"
                    )
                )
                section = group[0]["section_path"]
                embed = (
                    f"资料关联机型：{', '.join(document['model_slugs'])}\n"
                    f"文档：{document['title']}\n章节：{section}\n"
                    + (
                        f"适用范围说明：{document['scope_note']}\n"
                        if document["scope_note"]
                        else ""
                    )
                    + part
                )
                chunks.append(
                    {
                        "id": uid(document["processing_id"], "chunk", index),
                        "processing_id": document["processing_id"],
                        "document_version_id": document["id"],
                        "chunk_index": index,
                        "chunk_type": kind,
                        "section_path": section,
                        "display_text": part,
                        "embed_text": embed,
                        "content_hash": digest(embed.encode()),
                        "index_language": "zh-Hans",
                        "element_ids": [e["id"] for e in group],
                        "metadata_json": {
                            "source_pages": sorted(
                                {r["source_page_no"] for e in group for r in e["regions"]}
                            ),
                            "asset_ids": sorted(
                                {e["asset_id"] for e in group if e.get("asset_id")}
                            ),
                            "model_slugs": document["model_slugs"],
                            "software_context": group[0]["metadata_json"].get("software_context"),
                            "scope_note": document["scope_note"],
                        },
                    }
                )
                index += 1
    write_jsonl(root / "chunks.jsonl", chunks)
    return chunks


def quality(documents, pages, elements):
    return {
        "documents": len(documents),
        "pdfs": sum(d["document_type"] != "technical_specifications" for d in documents),
        "pages_total": len(pages),
        "pages_included": sum(p["included"] for p in pages),
        "pages_excluded": sum(not p["included"] for p in pages),
        "element_types": dict(Counter(e["element_type"] for e in elements)),
        "table_warnings": sum("table_warning" in p["metadata_json"] for p in pages),
        "specification_warnings": [
            {
                "title": d["title"],
                "status": d["metadata_json"].get("collection_status"),
                "note": d["scope_note"],
                "empty_values": d["metadata_json"].get("empty_parameter_values", []),
            }
            for d in documents
            if d["document_type"] == "technical_specifications"
            and (d["scope_note"] or d["metadata_json"].get("empty_parameter_values"))
        ],
        "pdfs_without_chinese_pages": [
            d["title"]
            for d in documents
            if d["document_type"] != "technical_specifications"
            and not any(p["included"] and p["processing_id"] == d["processing_id"] for p in pages)
        ],
        "limitations": [
            "版面恢复使用原生坐标与几何规则，复杂多栏和无边框表格需要抽查。",
            "图文关联是同页几何邻近关系，不等同于已确认的语义配图。",
            "中文阅读副本保留整页；混排页的外文仅从检索文本中排除。",
            "中文安全说明与原文脚注保留；仅提取不代表操作适用性审核。",
        ],
    }
