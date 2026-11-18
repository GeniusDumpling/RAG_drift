import html
import json
import re
from urllib.parse import urljoin, urlsplit

from bs4 import BeautifulSoup

from tools.autel_documents.collector import digest, now, write_json
from tools.drone_collection.archive import SUCCESS, Archive, write_catalog
from tools.drone_collection.pdf_specs import extract_pdf_specs

BASE = "https://support.skydio.com"
CATEGORIES = BASE + "/api/v2/help_center/en-us/categories.json?per_page=100"
PRODUCTS = [
    ("x10", "Skydio X10", "https://www.skydio.com/x10/technical-specs"),
    ("x10d", "Skydio X10D", "https://www.skydio.com/x10d/technical-specs"),
    ("r10", "Skydio R10", "https://www.skydio.com/r10/technical-specs"),
]


def document_language(anchor):
    languages = {
        "english": "en",
        "japanese": "ja",
        "spanish": "es",
        "español": "es",
        "ukrainian": "uk",
        "日本語": "ja",
    }
    title = anchor.get_text(" ", strip=True).casefold()
    for name, code in languages.items():
        if name in title:
            return code
    for previous in anchor.find_all_previous(["p", "h2", "h3", "h4"]):
        text = previous.get_text(" ", strip=True).casefold().strip(":")
        if text in languages:
            return languages[text]
        if previous.name.startswith("h"):
            break
    return "en"


def scope(title):
    for pattern, slug in [
        (r"\bX10D\b", "x10d"),
        (r"\bX10\b", "x10"),
        (r"\bR10\b", "r10"),
        (r"\bX2D\b", "x2d"),
        (r"\bX2(?:E| Enterprise)?\b", "x2e"),
        (r"\bR1\b", "r1"),
        (r"Skydio 2(?:/2\+|\+)?", "skydio-2-series"),
    ]:
        if re.search(pattern, title, re.I):
            return slug
    return "shared-support"


def collect(archive: Archive):
    root = archive.fetch(CATEGORIES, kind="json", title="Skydio 官方支持分类")
    if root["status"] not in SUCCESS:
        raise ValueError("Skydio category API is unavailable; see failure record")
    categories = json.loads(archive.read(root))["categories"]
    for category in categories:
        url = (
            BASE
            + f"/api/v2/help_center/en-us/categories/{category['id']}/articles.json?per_page=100"
        )
        visited = set()
        while url and url not in visited:
            visited.add(url)
            page = archive.fetch(url, kind="json", title=category["name"] + " — 文章列表")
            if page["status"] not in SUCCESS:
                archive.state.setdefault("incomplete_api_pages", []).append(url)
                break
            data = json.loads(archive.read(page))
            for article in data["articles"]:
                if article.get("draft"):
                    continue
                slug = scope(article["title"])
                if slug == "shared-support":
                    slug = scope(category["name"])
                source_url = article["html_url"]
                body = (
                    "<!doctype html><meta charset='utf-8'><h1>"
                    + html.escape(article["title"])
                    + "</h1>"
                    + (article.get("body") or "")
                )
                previous = archive.state["pages"].get(source_url)
                if not previous or previous.get("api_updated_at") != article["updated_at"]:
                    row = archive.store(
                        source_url,
                        body.encode(),
                        kind="html",
                        title=article["title"],
                        slug=slug,
                        api_source_url=page["url"],
                        api_source_sha256=page["sha256"],
                        api_updated_at=article["updated_at"],
                        capture_kind="help_center_api_body",
                    )
                    archive.state["pages"][source_url] = row
                soup = BeautifulSoup(article.get("body") or "", "html.parser")
                for a in soup.select("a[href]"):
                    href = urljoin(source_url, a["href"])
                    title = a.get_text(" ", strip=True)
                    is_pdf = urlsplit(href).path.lower().endswith(".pdf")
                    attachment = "/hc/article_attachments/" in href and re.search(
                        r"manual|guide|checklist|settings card|\.pdf",
                        title,
                        re.I,
                    )
                    if is_pdf or attachment:
                        archive.add_document(
                            href,
                            slug,
                            category["name"],
                            title or article["title"],
                            source_url,
                            updated_at=article["updated_at"],
                            language=document_language(a),
                        )
            url = data.get("next_page")
            archive.checkpoint()
    for slug, name, url in PRODUCTS:
        row = archive.fetch(url, title=name + " technical specifications", slug=slug)
        if row["status"] in SUCCESS:
            soup = BeautifulSoup(archive.read(row), "html.parser")
            for a in soup.select('a[href*=".pdf"]'):
                archive.add_document(
                    a["href"], slug, name, a.get_text(" ", strip=True) or "Product document", url
                )
    archive.checkpoint()
    archive.export()


def extract_tables(source):
    soup = BeautifulSoup(source, "html.parser")
    groups = []
    for table in soup.select(".basic-tables__table"):
        title = table.select_one("thead")
        params = []
        for tr in table.select("tbody tr"):
            cells = tr.find_all(["th", "td"], recursive=False)
            if len(cells) == 2:
                params.append(
                    {
                        "name": cells[0].get_text("\n", strip=True),
                        "value": cells[1].get_text("\n", strip=True),
                    }
                )
        if params:
            groups.append(
                {
                    "name": title.get_text(" ", strip=True)
                    if title
                    else "Technical specifications",
                    "parameters": params,
                }
            )
    # Conditions outside tables belong to the same official specification module.
    for container in soup.select("article.basic-tables"):
        for table in container.select("table"):
            table.decompose()
        for heading in container.select("header"):
            heading.decompose()
        notes = container.get_text("\n", strip=True)
        if notes:
            groups.append(
                {
                    "name": "Test conditions and notes",
                    "parameters": [{"name": "Official notes", "value": notes}],
                }
            )
    return groups


def build(archive):
    models = []
    for slug, name, url in PRODUCTS:
        page = archive.state["pages"].get(url)
        if not page or page["status"] not in SUCCESS:
            continue
        source = archive.read(page)
        if digest(source) != page["sha256"]:
            raise ValueError("Specification snapshot hash mismatch")
        groups = extract_tables(source)
        if not groups:
            continue
        models.append(
            {
                "slug": slug,
                "name": name,
                "groups": groups,
                "source_url": url,
                "collected_at": page["collected_at"],
                "category": "行业应用",
                "source_sha256": page["sha256"],
                "source_local_path": page["local_path"],
            }
        )
    manual_profiles = [
        ("skydio-2", "Skydio 2", "Skydio 2/2+ Operator Manual", r"Skydio 2 Specifications"),
        ("skydio-2-plus", "Skydio 2+", "Skydio 2/2+ Operator Manual", r"Skydio 2\+ Specifications"),
        ("x2e", "Skydio X2E", "Skydio X2E Operator Manual", r"\bSpecifications\b"),
        (
            "x2d-intl",
            "Skydio X2D (INTL)",
            "Skydio X2D INTL Operator Manual 24.19.39",
            r"\bSpecifications\b",
        ),
        (
            "x2d-ato",
            "Skydio X2D (ATO)",
            "Skydio X2D ATO Operator Manual 24.19.39",
            r"\bSpecifications\b",
        ),
    ]
    missing = []
    for slug, name, title, marker in manual_profiles:
        row = next(
            (
                r
                for r in archive.state["documents"].values()
                if r["title"] == title and r.get("status") in SUCCESS
            ),
            None,
        )
        if not row:
            missing.append({"slug": slug, "reason": "Official operator manual unavailable"})
            continue
        if digest(archive.read(row)) != row["sha256"]:
            raise ValueError("Manual source hash mismatch")
        groups = extract_pdf_specs(archive.root / row["local_path"], marker)
        if groups:
            models.append(
                {
                    "slug": slug,
                    "name": name,
                    "groups": groups,
                    "category": "消费航拍" if slug.startswith("skydio-2") else "行业应用",
                    "source_url": row["page_url"],
                    "collected_at": row["collected_at"],
                    "source_sha256": row["sha256"],
                    "source_local_path": row["local_path"],
                }
            )
    r1_questions = [
        "How high can R1 fly?",
        "How long can I fly Skydio R1?",
        "Can R1 take still photos as well as video?",
        "Flying your R1 in the cold",
        "How does R1 use GPS?",
    ]
    parameters = []
    for question in r1_questions:
        row = next(
            (
                r
                for r in archive.state["pages"].values()
                if r.get("title") == question and r.get("status") in SUCCESS
            ),
            None,
        )
        if row:
            if digest(archive.read(row)) != row["sha256"]:
                raise ValueError("R1 support snapshot hash mismatch")
            soup = BeautifulSoup(archive.read(row), "html.parser")
            heading = soup.find("h1")
            if heading:
                heading.decompose()
            text = soup.get_text("\n", strip=True)
            parameters.append(
                {
                    "name": question,
                    "value": text,
                    "source_url": row["url"],
                    "source_sha256": row["sha256"],
                }
            )
    if parameters:
        first = next(
            r for r in archive.state["pages"].values() if r.get("title") == r1_questions[0]
        )
        models.append(
            {
                "slug": "r1",
                "name": "Skydio R1",
                "groups": [{"name": "Flight and imaging specifications", "parameters": parameters}],
                "source_url": first["url"],
                "collected_at": first["collected_at"],
                "source_local_path": first["local_path"],
                "source_sha256": first["sha256"],
                "category": "消费航拍",
                "status": "partial",
                "note": "已收录官方支持文章中的飞行与影像信息，参数尚未补齐。",
            }
        )
    write_json(archive.root / "技术参数缺失清单.json", missing)
    write_json(
        archive.root / "机型参数目录/提取记录.json",
        {"built_at": now(), "product_sources": PRODUCTS},
    )
    return write_catalog(archive.root, "Skydio", models)


def make_archive(root, **kwargs):
    return Archive(root, "Skydio", ["skydio.com"], cdns=["cdn.sanity.io"], **kwargs)
