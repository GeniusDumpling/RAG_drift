"""Native section boundaries prevent same-page illustrations from crossing procedures."""

from collections import Counter

from .source import case_source, checked_path


def procedure_layout(bundle, case, root):
    if case["kind"] != "procedure":
        return []
    import pymupdf

    manual, pages = case_source(bundle, case)
    asset = bundle["assets"][manual["pdf_asset_id"]]
    result = []
    with pymupdf.open(checked_path(root, asset["storage_key"])) as pdf:
        for material in pages:
            page = pdf[material["number"] - 1]
            lines = []
            sizes = Counter()
            for block in page.get_text("dict")["blocks"]:
                for row in block.get("lines", []):
                    spans = row["spans"]
                    text = "".join(s["text"] for s in spans).strip()
                    if not text:
                        continue
                    size = round(max(s["size"] for s in spans), 2)
                    sizes[size] += len(text)
                    box = row["bbox"]
                    lines.append(
                        dict(
                            text=text,
                            size=size,
                            x=box[0] / page.rect.width * 1000,
                            y=box[1] / page.rect.height * 1000,
                        )
                    )
            if not sizes:
                continue
            body = sizes.most_common(1)[0][0]
            heads = sorted(
                [
                    line
                    for line in lines
                    if line["size"] > body * 1.05
                    and len(line["text"]) < 80
                    and 50 < line["y"] < 900
                ],
                key=lambda line: line["y"],
            )
            # This guard only establishes boundaries for clearly vertical layouts.
            if any(
                abs(a["y"] - b["y"]) < 8 and abs(a["x"] - b["x"]) > 100
                for i, a in enumerate(heads)
                for b in heads[i + 1 :]
            ):
                continue
            if len(heads) < 2:
                continue
            result.append(
                dict(
                    page=material["number"],
                    document_sha256=manual["sha256"],
                    sections=[
                        dict(
                            title=h["text"],
                            start=round(h["y"], 2),
                            end=round(heads[i + 1]["y"], 2) if i + 1 < len(heads) else 1000,
                        )
                        for i, h in enumerate(heads)
                    ],
                )
            )
    return result


def validate_section_links(evidence, layouts):
    by_page = {p["page"]: p["sections"] for p in layouts}
    for row in evidence:
        sections = by_page.get(row["page"], [])
        text_sections = {
            i
            for line in row["text_rows"]
            for i, s in enumerate(sections)
            if s["start"] <= (line["bbox"][1] + line["bbox"][3]) / 2 < s["end"]
        }
        image_sections = {
            i
            for region in row["regions"]
            for i, s in enumerate(sections)
            if min(region["bbox"][3], s["end"]) - max(region["bbox"][1], s["start"]) > 1
        }
        if text_sections and image_sections and not text_sections & image_sections:
            raise ValueError("操作原文与引用图片属于不同章节")
