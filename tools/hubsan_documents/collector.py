import json
import re
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urljoin, urlsplit

from bs4 import BeautifulSoup, Comment

from tools.autel_documents.collector import digest, write_json
from tools.drone_collection.archive import SUCCESS, Archive, write_catalog

APIS = [
    "https://chn-filemanger.hubsan.com/index/index/getSupport.html",
    "https://www-filemanger.hubsan.com/index/index/getSupport.html",
]
PRODUCTS = "https://www.hubsan.com/product.json"
LEGACY = "https://www.hubsan.com/support/index/index/products.html"
CHINESE_PRODUCTS = "https://chn.hubsan.com/index.php?main_page=index&cPath=1"
ALIASES = {
    "黑鹰4号": "blackhawk-4",
    "黑鹰3号": "blackhawk-3",
    "黑鹰2号内置版": "blackhawk-2-4g",
    "黑鹰2号": "blackhawk-2",
    "黑鹰1号超越版": "blackhawk-1-upgraded",
    "黑鹰1号避障版": "blackhawk-1-obstacle",
    "黑鹰1号": "blackhawk-1",
    "迷你一英寸": "mini-1-inch",
    "blackhawk 2(4g)": "blackhawk-2-4g",
    "blackhawk 3": "blackhawk-3",
    "zino2 +": "zino-2-plus",
    "zino2": "zino-2",
    "zino pro+": "zino-pro-plus",
    "zino pro+ (5g ver)": "zino-pro-plus",
    "zino pro+ (jp spec)": "zino-pro-plus-jp",
    "0cean 1": "ocean-1",
    "h109spro professional": "h109s-professional",
    "zino with an extra battery": "zino",
    "远洋1号": "ocean-1",
    "萤火虫": "firefly",
}


def model_slug(name):
    name = name.strip()
    if name.casefold() in ALIASES:
        return ALIASES[name.casefold()]
    if name in ALIASES:
        return ALIASES[name]
    name = name.lower().replace("精英版", " refined").replace("hubsan ", "")
    if re.match(r"h109s.*\b(professional|advanced|standard)\b", name):
        return "h109s-" + re.search(r"\b(professional|advanced|standard)\b", name)[1]
    name = re.sub(r"(h\d+[a-z]?\+?)\s*x4.*", r"\1", name)
    name = re.sub(r"(h\d+[a-z]?)\s*(?:nano|gps).*", r"\1", name)
    name = re.sub(r"\s+(?:portable|standard|professional|advanced)\s+version.*", "", name)
    name = name.replace("+", " plus ")
    return re.sub(r"[^a-z0-9]+", "-", name).strip("-")


def is_aircraft(name):
    return not re.search(
        r"remote|transmitter|goggles|vision \(|\bHT\d+|\bH7000\b|配件|遥控|无忧换|Spare", name, re.I
    )


def document_language(url):
    """Menu region does not identify a PDF's language; use explicit filename markers."""
    path = urlsplit(url).path.lower()
    if re.search(r"(?:[_/-])(?:cn|ch|zh)(?:[_/.-])", path):
        return "zh-CN"
    if re.search(r"(?:[_/-])(?:en|eng|ing)(?:[_/.-])", path):
        return "en"
    for pattern, language in [
        ("ge|de", "de"),
        ("jp|ja", "ja"),
        ("sp|es", "es"),
        ("fr", "fr"),
        ("it", "it"),
        ("ko|kr", "ko"),
    ]:
        if re.search(r"(?:[_/-])(?:" + pattern + r")(?:[_/.-])", path):
            return language
    return "und"


def register(archive, name, source_url):
    slug = model_slug(name)
    if not slug or not is_aircraft(name):
        return None
    models = archive.state["models"]
    previous = models.get(slug, {})
    models[slug] = {
        "slug": slug,
        "name": previous.get("name", name.strip()),
        "source_url": previous.get("source_url", source_url),
        "product_urls": previous.get("product_urls", []),
        "aliases": sorted(set(previous.get("aliases", []) + [name.strip()])),
    }
    return slug


def product_identity(name, models):
    def key(value):
        return re.sub(r"\s+", "", value).casefold().removeprefix("hubsan").replace("+", "plus")

    compact = key(name)
    candidates = []
    for slug, model in models.items():
        for alias in model["aliases"]:
            prefix = key(alias)
            if not compact.startswith(prefix):
                continue
            remainder = compact[len(prefix) :]
            # A numeric suffix or PLUS is a different model, rather than a bundle.
            if (
                remainder
                and re.match(r"[a-z0-9]", remainder)
                and not re.match(r"[1-9]套餐", remainder)
            ):
                continue
            candidates.append((len(prefix), slug))
    if candidates:
        return max(candidates)[1], None
    model_name = re.split(r"官方标配|标准版|便携版|[1-9]套餐|[（(]", name, maxsplit=1)[0].strip()
    return None, model_name


def collect(archive):
    for url in APIS:
        page = archive.fetch(url, kind="json", title="Hubsan 官方下载索引")
        if page["status"] not in SUCCESS:
            continue
        data = json.loads(archive.read(page))
        for language, section in data["data"].items():
            for product in section.get("download", {}).get("products", []):
                name = product["name"].strip()
                slug = register(archive, name, url) or "accessories"
                for key in ["umPath", "qsPath", "ctPath"]:
                    value = product.get(key)
                    if not value or not value.lower().endswith((".pdf", ".zip")):
                        continue
                    kind = "zip" if value.lower().endswith(".zip") else "pdf"
                    key_id = slug + "|" + value
                    old = archive.state["documents"].get(key_id, {})
                    archive.add_document(
                        value,
                        slug,
                        name,
                        name + " — " + key,
                        url,
                        kind=kind,
                        language=document_language(value),
                        menu_languages=sorted(set(old.get("menu_languages", []) + [language])),
                        document_type="user_manual" if key == "umPath" else "quick_start",
                    )
    page = archive.fetch(LEGACY, title="Hubsan 历史手册目录")
    if page["status"] in SUCCESS:
        soup = BeautifulSoup(archive.read(page), "html.parser")
        for c in soup.find_all(string=lambda s: isinstance(s, Comment)):
            c.extract()
        for container in soup.select(".hbs-sinrighttext"):
            heading = container.find("h4")
            if not heading:
                continue
            name = heading.get_text(" ", strip=True)
            slug = register(archive, name, LEGACY) or "accessories"
            for a in container.select("a[href]"):
                if "User Manual" in a.get_text() and a["href"].lower().endswith((".pdf", ".zip")):
                    # The same official site also publishes these legacy paths over HTTPS.
                    url = urljoin(LEGACY, a["href"]).replace("http://", "https://", 1)
                    archive.add_document(
                        url,
                        slug,
                        name,
                        name + " — User Manual",
                        LEGACY,
                        kind="zip" if url.endswith(".zip") else "pdf",
                        language=document_language(url),
                    )
    product_page = archive.fetch(PRODUCTS, kind="json", title="Hubsan 官方产品列表")
    if product_page["status"] in SUCCESS:
        for entry in json.loads(archive.read(product_page))["product"]:
            product = entry.get("na") or entry.get("eur")
            if not product or not product.get("id"):
                continue
            name = product["name"].strip()
            slug = register(archive, name, PRODUCTS)
            if not slug:
                continue
            # Discard session IDs, currency, region and shopping parameters.
            url = "https://www.hubsan.com/na/index.php?" + urlencode(
                {
                    "main_page": "product_info",
                    "products_id": product["id"].strip(),
                }
            )
            archive.state["models"][slug]["product_urls"].append(url)
    page = archive.fetch(CHINESE_PRODUCTS, title="Hubsan 中文无人机产品列表")
    if page["status"] in SUCCESS:
        for model in archive.state["models"].values():
            model["product_urls"] = [
                u for u in model["product_urls"] if urlsplit(u).hostname != "chn.hubsan.com"
            ]
        soup = BeautifulSoup(archive.read(page), "html.parser")
        for a in soup.select("a[href]"):
            name = a.get_text(" ", strip=True)
            if not name or "product_info" not in a["href"] or not is_aircraft(name):
                continue
            slug, new_name = product_identity(name, archive.state["models"])
            if not slug and new_name:
                slug = register(archive, new_name, CHINESE_PRODUCTS)
            if not slug:
                continue
            parsed = urlsplit(urljoin(CHINESE_PRODUCTS, a["href"]))
            pid = parse_qs(parsed.query).get("products_id", [""])[0]
            if pid:
                url = "https://chn.hubsan.com/index.php?" + urlencode(
                    {
                        "main_page": "product_info",
                        "products_id": pid,
                    }
                )
                archive.state["models"][slug]["product_urls"].append(url)
    archive.checkpoint()
    for model in archive.state["models"].values():
        # A bundle changes battery count, not the aircraft specification; retain one page per site.
        chosen = {}
        for url in model["product_urls"]:
            chosen.setdefault(urlsplit(url).hostname, url)
        model["product_urls"] = list(chosen.values())
        for url in model["product_urls"]:
            archive.fetch(url, title=model["name"] + " 官方产品页", slug=model["slug"])
    archive.checkpoint()
    archive.export()


def extract_product_specs(source):
    soup = BeautifulSoup(source, "html.parser")
    sections = soup.select(".hbs-proshowcontent > ul")
    parameters = []
    nodes = soup.select("regsub > ul > li, .part_box ul > li")
    if len(sections) >= 2:
        nodes += sections[1].select("li")
    seen = set()
    for li in nodes:
        text = li.get_text("\n", strip=True)
        if not text or text in seen or text.startswith("UPC:"):
            continue
        key = re.sub(r"\s+", "", text).casefold()
        if key in seen:
            continue
        seen.add(key)
        parts = re.split(r"\s*[:：]\s*|\s*-\s+(?=\d)", text, maxsplit=1)
        if len(parts) == 2 and parts[0] and parts[1]:
            parameters.append({"name": parts[0], "value": parts[1]})
        else:
            parameters.append({"name": "官方规格", "value": text})
    return [{"name": "技术参数", "parameters": parameters}] if parameters else []


def build(archive, *, reviewed_records=None):
    models, missing = [], []
    reviewed = (
        reviewed_records
        if reviewed_records is not None
        else json.loads((Path(__file__).parent / "reviewed_images.json").read_text())
    )
    image_state_path = archive.root / "参数图片/采集状态.json"
    image_pages = (
        json.loads(image_state_path.read_text())["pages"] if image_state_path.exists() else {}
    )
    reviewed_models = {}
    for record in reviewed:
        if not record.get("reviewed"):
            continue
        page = next(
            (
                r
                for r in image_pages.values()
                if r.get("sha256") == record["source_sha256"] and r.get("status") in SUCCESS
            ),
            None,
        )
        if not page:
            # A changed or unavailable source must be reviewed before its values are published.
            continue
        image = archive.root / "参数图片" / page["local_path"]
        if digest(image.read_bytes()) != record["source_sha256"]:
            raise ValueError("Reviewed image hash mismatch")
        reviewed_models[record["slug"]] = {
            "groups": record["groups"],
            "source_url": page["page_url"],
            "source_image_url": page["url"],
            "source_local_path": "参数图片/" + page["local_path"],
            "source_sha256": page["sha256"],
            "collected_at": page["collected_at"],
            "status": "partial",
            "note": "部分技术参数未收录，请以官方来源为准。",
            "official_version": record.get("official_version", ""),
            "category": record.get("category", "消费航拍"),
        }
    canonical_models = {}
    for original in archive.state["models"].values():
        slug = model_slug(original["name"])
        model = canonical_models.setdefault(slug, {**original, "slug": slug, "product_urls": []})
        model["product_urls"] = list(
            dict.fromkeys(model["product_urls"] + original["product_urls"])
        )
    for model in canonical_models.values():
        best = None
        for url in model["product_urls"]:
            page = archive.state["pages"].get(url)
            if not page or page["status"] not in SUCCESS:
                continue
            source = archive.read(page)
            if digest(source) != page["sha256"]:
                raise ValueError("Source snapshot hash mismatch")
            groups = extract_product_specs(source)
            if groups and (
                not best
                or sum(len(g["parameters"]) for g in groups)
                > sum(len(g["parameters"]) for g in best["groups"])
            ):
                best = {
                    **model,
                    "source_url": url,
                    "collected_at": page["collected_at"],
                    "source_sha256": page["sha256"],
                    "source_local_path": page["local_path"],
                    "groups": groups,
                }
        if model["slug"] in reviewed_models:
            best = {**model, **reviewed_models[model["slug"]]}
        if best:
            models.append(best)
        else:
            missing.append(
                {
                    "slug": model["slug"],
                    "name": model["name"],
                    "reason": "产品页无可直接提取的文字参数，需检查参数图片或手册",
                }
            )
    write_json(archive.root / "技术参数缺失清单.json", missing)
    return write_catalog(archive.root, "Hubsan", models)


def make_archive(root, **kwargs):
    return Archive(root, "Hubsan", ["hubsan.com"], **kwargs)
