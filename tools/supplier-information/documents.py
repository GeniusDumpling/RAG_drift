"""Extract source text and tables from HTML and PDF, preserving PDF page numbers."""

import hashlib
import io
import json
import logging
import re
from pathlib import Path

import requests
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)


def publication_date(text):
    """Read explicit document dates, never PDF creation metadata/copyright or fetch time."""
    for line in text[:5000].splitlines():
        if re.search(r"copyright|©|all rights reserved", line, re.I):
            continue
        if re.search(r"(?:updated|published|released)(?:\s+on)?\s*:?", line, re.I):
            if re.search(r"\b(?:19|20)\d{2}\b", line):
                return line.strip()
        if re.fullmatch(
            r"\s*(?:January|February|March|April|May|June|July|August|September|October|November|December)"
            r"\s+\d{1,2}(?:st|nd|rd|th)?,?\s+(?:19|20)\d{2}\s*",
            line,
            re.I,
        ):
            return line.strip()
    return ""


def markdown_table(rows):
    rows = [
        [re.sub(r"\s+", " ", str(cell or "")).strip().replace("|", "\\|") for cell in row]
        for row in rows
        if row
    ]
    if not rows:
        return ""
    width = max(map(len, rows))
    rows = [row + [""] * (width - len(row)) for row in rows]

    def render(row):
        return "| " + " | ".join(row) + " |"

    return "\n".join([render(rows[0]), render(["---"] * width), *(render(row) for row in rows[1:])])


def html_table_rows(table):
    """Expand rowspan/colspan so each rendered row retains its column relationships."""
    grid = {}
    for row_index, tr in enumerate(table.find_all("tr")):
        col = 0
        for cell in tr.find_all(["th", "td"], recursive=False):
            while (row_index, col) in grid:
                col += 1
            rowspan = min(100, max(1, int(cell.get("rowspan", 1))))
            colspan = min(100, max(1, int(cell.get("colspan", 1))))
            value = cell.get_text(" ", strip=True)
            for dy in range(rowspan):
                for dx in range(colspan):
                    grid[row_index + dy, col + dx] = value
            col += colspan
    if not grid:
        return []
    return [
        [grid.get((y, x), "") for x in range(max(k[1] for k in grid) + 1)]
        for y in range(max(k[0] for k in grid) + 1)
    ]


def parse_pdf(content, url):
    import pymupdf

    pages = []
    with pymupdf.open(stream=io.BytesIO(content), filetype="pdf") as doc:
        if doc.needs_pass:
            raise ValueError("PDF requires a password")
        for page in doc:
            text = page.get_text(sort=True).strip()
            tables = []
            try:
                tables = [table.extract() for table in page.find_tables().tables]
            except Exception as exc:
                logger.warning("PDF table extraction failed on page %d: %s", page.number + 1, exc)
            pages.append({"page": page.number + 1, "text": text, "tables": tables})
    if not any(p["text"] or p["tables"] for p in pages):
        raise ValueError("PDF has no extractable text; scanned PDFs require OCR")
    text = "\n\n".join(
        f"### PDF page {page['page']}\n{page['text']}\n"
        + "\n\n".join(markdown_table(t) for t in page["tables"])
        for page in pages
    )
    return {
        "url": url,
        "format": "pdf",
        "text": text,
        "pages": pages,
        "table_count": sum(len(p["tables"]) for p in pages),
        "published_at": publication_date(pages[0]["text"]) if pages else "",
    }


def parse_html(content, url):
    import trafilatura

    soup = BeautifulSoup(content, "html.parser")
    tables = [html_table_rows(t) for t in soup.find_all("table")]
    text = trafilatura.extract(content, include_tables=False) or ""
    date = soup.find("meta", attrs={"property": "article:published_time"})
    if not date:
        date = soup.find("meta", attrs={"name": "date"})
    published_at = date.get("content", "") if date else ""
    text += "\n\n" + "\n\n".join(markdown_table(t) for t in tables)
    return {
        "url": url,
        "format": "html",
        "text": text.strip(),
        "pages": [],
        "table_count": len(tables),
        "published_at": published_at,
    }


def fetch_document(url, *, cache_dir=None, timeout=30, max_bytes=30 * 1024 * 1024):
    """Detect PDF using MIME type and magic bytes, including extensionless PDF URLs."""
    with requests.get(
        url, headers={"User-Agent": "Mozilla/5.0"}, timeout=timeout, stream=True
    ) as response:
        response.raise_for_status()
        body = bytearray()
        for chunk in response.iter_content(65536):
            body.extend(chunk)
            if len(body) > max_bytes:
                raise ValueError(f"Document exceeds {max_bytes} bytes")
        raw = bytes(body)
        is_pdf = (
            raw.lstrip().startswith(b"%PDF-")
            or "application/pdf" in response.headers.get("Content-Type", "").lower()
        )
        if is_pdf:
            document = parse_pdf(raw, url)
        else:
            encoding = response.encoding
            if not encoding or encoding.lower() == "iso-8859-1":
                encoding = response.apparent_encoding or "utf-8"
            document = parse_html(raw.decode(encoding, errors="replace"), url)
        document["resolved_url"] = response.url
    if cache_dir is not None:
        cache_dir = Path(cache_dir)
        cache_dir.mkdir(parents=True, exist_ok=True)
        key = hashlib.sha256(url.encode()).hexdigest()
        (cache_dir / f"{key}.json").write_text(
            json.dumps(document, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        if is_pdf:
            (cache_dir / f"{key}.pdf").write_bytes(raw)
    logger.info(
        "Parsed %s: %d pages, %d tables, %d characters: %s",
        document["format"],
        len(document["pages"]),
        document["table_count"],
        len(document["text"]),
        url,
    )
    return document
