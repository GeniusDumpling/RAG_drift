"""Extract explicitly headed PDF specification tables with source page coordinates."""

import re

import fitz


def extract_pdf_specs(path, page_marker=r"\bSpecifications\b"):
    groups = []
    with fitz.open(path) as doc:
        for page_index, page in enumerate(doc):
            text = page.get_text()
            if not re.search(page_marker, text, re.I) or re.search(r"\bContents\b|\.{5}", text):
                continue
            for table in page.find_tables().tables:
                rows = table.extract()
                if not rows or any(len(row) != 2 for row in rows):
                    continue
                parameters = []
                heading = "Technical specifications"
                preceding = [
                    b
                    for b in page.get_text("blocks")
                    if b[3] <= table.bbox[1] + 3
                    and table.bbox[1] - b[3] < 45
                    and b[0] < table.bbox[2]
                    and b[2] > table.bbox[0]
                    and len(b[4].strip()) < 70
                ]
                if preceding:
                    heading = max(preceding, key=lambda b: b[3])[4].strip()
                for name, value in rows:
                    name, value = (name or "").strip(), (value or "").strip()
                    if name and not value and not parameters:
                        heading = name
                    elif name and value:
                        parameters.append(
                            {"name": name, "value": value, "source_page": page_index + 1}
                        )
                if parameters:
                    groups.append(
                        {
                            "name": heading,
                            "parameters": parameters,
                            "source_page": page_index + 1,
                            "source_bbox": list(table.bbox),
                        }
                    )
    return groups
