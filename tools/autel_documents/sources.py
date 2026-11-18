"""Autel's static download pages, legacy Shopify pages and specification tables."""

import hashlib
import json
import re
import unicodedata
from urllib.parse import quote, unquote, urljoin, urlsplit, urlunsplit

from bs4 import BeautifulSoup, Comment

OFFICIAL_DOMAINS = ("autelrobotics.com", "autelrobotics.cn")
# Only PDF links explicitly published on an official page may use these CDNs.
DOCUMENT_CDNS = (
    "cdn.shopify.com",
    "cdn.shopifycdn.net",
    # app.autelrobotics.com returns this exact host in its public PDF redirects.
    "d42ar9bw0cz7m.cloudfront.net",
)
ROOT_PATHS = (
    "/",
    "/download/enterprise-products/",
    "/download/customer-products/",
    "/download/ecosystem/",
    "/products/",
)
# These historical families have disappeared from the current download navigation.
LEGACY_DOCS = ("evo-nano", "evo-lite", "evo-ii", "evo-ii-enterprise-v2", "evo-ii-rtk-v2")
SHOP_SEEDS = (
    "technical-support-index",
    "evo-downloads",
    "dragonfish-downloads",
    "x-star-downloads",
)


def seeds() -> list[str]:
    return (
        [
            f"https://www.{domain}{path}"
            for domain in ("autelrobotics.cn", "autelrobotics.com")
            for path in ROOT_PATHS
        ]
        + [
            f"https://www.{domain}/doc/{slug}/"
            for domain in ("autelrobotics.cn", "autelrobotics.com")
            for slug in LEGACY_DOCS
        ]
        + [f"https://shop.autelrobotics.com/pages/{slug}" for slug in SHOP_SEEDS]
        + [
            "https://manuals.autelrobotics.com/",
        ]
    )


def canonical_url(value: str, base: str = "") -> str:
    parts = urlsplit(urljoin(base, value.strip()))
    if parts.scheme not in {"http", "https"} or not parts.hostname or parts.username:
        raise ValueError("Not a public HTTP URL")
    if parts.port not in {None, 80, 443}:
        raise ValueError("Unexpected port")
    path = quote(unquote(parts.path or "/"), safe="/!$&'()*+,;=:@-._~")
    return urlunsplit((parts.scheme.lower(), parts.netloc.lower(), path, parts.query, ""))


def official_url(url: str) -> bool:
    host = urlsplit(url).hostname or ""
    return any(host == domain or host.endswith("." + domain) for domain in OFFICIAL_DOMAINS)


def allowed_url(url: str, *, document: bool = False) -> bool:
    try:
        url = canonical_url(url)
    except ValueError:
        return False
    return official_url(url) or (document and urlsplit(url).hostname in DOCUMENT_CDNS)


def is_pdf(url: str) -> bool:
    return unquote(urlsplit(url).path).lower().endswith(".pdf")


def safe_name(value: str, length: int = 100) -> str:
    value = unicodedata.normalize("NFKC", value)
    value = re.sub(r'[\x00-\x1f<>:"/\\|?*]', "_", value).strip(" ._")
    return value[:length] or "document"


def url_id(url: str) -> str:
    return hashlib.sha256(url.encode()).hexdigest()[:12]


def page_scope(url: str, title: str = "") -> str:
    path = unquote(urlsplit(url).path).strip("/")
    bits = path.split("/")
    if len(bits) >= 2 and bits[0] in {"doc", "productdetail", "product", "pages"}:
        slug = bits[1]
        slug = re.sub(r"-(?:downloads|support)$", "", slug)
        if slug.isdigit() and title:
            compact = re.sub(r"[^a-z0-9]", "", unicodedata.normalize("NFKC", title).lower())
            for token, family in [
                ("evoliteenterprise", "evo-lite-ent"),
                ("evomax", "evo-max-series"),
                ("evonano", "evo-nano"),
                ("evolite", "evo-lite"),
                ("evoiiipro", "evo-iii-pro"),
                ("evoiii", "evo-iii"),
                ("evoiienterprise", "evo-ii-enterprise"),
                ("evoiidual", "evo-ii-dual-640t"),
                ("evoiirtk", "evo-ii-rtk"),
                ("evoiipro", "evo-ii-pro"),
                ("evoii", "evo-ii"),
                ("autelsmartcontrollerse", "control-se"),
                ("autelsmartcontroller", "autel-smart-controller"),
                ("autelmapper", "autel-mapper"),
                ("integratedcommandsystem", "command-center"),
                ("dragonfish", "dragon-fish"),
            ]:
                if token in compact:
                    return family
            return "document-" + slug
        return safe_name(slug).lower()
    return "official-site"


def page_kind(url: str) -> str:
    path = urlsplit(url).path
    if "/productdetail/" in path or "/product/" in path:
        return "product"
    if "/doc/" in path or "/pages/" in path:
        return "downloads"
    return "directory"


def document_type(title: str) -> str:
    title = re.sub(r"[_+\-]+", " ", title.casefold())
    for terms, kind in [
        (("maintenance", "保养", "维护"), "maintenance_manual"),
        (
            ("user manual", "flight manual", "product manual", "用户手册", "使用手册", "操作手册"),
            "user_manual",
        ),
        (("quick", "快速", "指引"), "quick_start"),
        (("release", "更新说明", "发布记录"), "release_notes"),
        (("safety", "安全", "免责声明"), "safety_guidelines"),
        (("brochure", "specification", "规格", "画册", "重量与尺寸", "宣传册"), "specifications"),
        (("conformity", "declaration", "合格", "符合性"), "declaration"),
        (("packing list", "产品清单", "物品清单"), "packing_list"),
        (("installation", "安装"), "installation_guide"),
    ]:
        if any(term in title for term in terms):
            return kind
    return "reference"


def guess_language(title: str, url: str) -> str:
    """Prefer explicit filename/title language markers over Chinese folder names."""
    filename = unquote(urlsplit(url).path).split("/")[-1]
    label = title + " " + filename
    if re.search(r"多语言|多語言|multi.?lang|all.?lang", label, re.I):
        return "mul"
    for terms, language in [
        (("简中", "简体中文", "Simplified Chinese"), "zh-CN"),
        (("繁中", "繁體", "Traditional Chinese"), "zh-TW"),
        (("日语", "日文", "日本語"), "ja"),
        (("法语", "法文"), "fr"),
        (("德语", "德文"), "de"),
    ]:
        if any(term.casefold() in label.casefold() for term in terms):
            return language
    codes = {
        "EN": "en",
        "JP": "ja",
        "JA": "ja",
        "FR": "fr",
        "DE": "de",
        "ES": "es",
        "SP": "es",
        "IT": "it",
        "RU": "ru",
        "PT": "pt",
        "KR": "ko",
        "KO": "ko",
        "CN": "zh-CN",
        "TC": "zh-TW",
    }
    for value in (filename, title):
        match = re.search(
            r"(?:^|[\s_+()\-])(" + "|".join(codes) + r")(?:[\s_+().\-]|$)", value, re.I
        )
        if match:
            return codes[match[1].upper()]
    if re.search(r"[\u4e00-\u9fff]", label):
        return "zh-CN"
    if re.search(
        r"user[ _+\-]manual|quick[ _+\-]start|English|safety|declaration|guide", label, re.I
    ):
        return "en"
    return "und"


def text_content(html: str) -> str:
    soup = BeautifulSoup(html, "html.parser")
    for node in soup(["script", "style", "header", "footer", "nav", "noscript"]):
        node.decompose()
    return soup.get_text("\n", strip=True)


def discover(html: str, page_url: str) -> tuple[list[dict], list[dict]]:
    """Follow only official product/download directories, never shop/blog pagination."""
    soup = BeautifulSoup(html, "html.parser")
    for comment in soup.find_all(string=lambda s: isinstance(s, Comment)):
        comment.extract()
    pages, documents = {}, {}

    def add(value: str, title: str = "", node=None):
        if value.strip().lower().startswith(("void(", "javascript:", "#")):
            return
        try:
            url = canonical_url(value, page_url)
        except (ValueError, TypeError):
            return
        if is_pdf(url):
            if not allowed_url(url, document=True):
                return
            date, version = "", ""
            if node is not None:
                row = node.find_parent("li")
                english_row = node.find_parent(class_="tabbox")
                if row is not None and row.select_one(".application-down-lih4"):
                    title = row.select_one(".application-down-lih4").get_text(" ", strip=True)
                    date_node = row.select_one(".application-down-time")
                    version_node = row.select_one(".application-down-edit")
                    date = date_node.get_text(" ", strip=True) if date_node else ""
                    version = version_node.get_text(" ", strip=True) if version_node else ""
                elif row is not None and row.select_one(".pdf-name"):
                    title = row.select_one(".pdf-name").get_text(" ", strip=True)
                    date_node = row.select_one(".pdf-time")
                    date = date_node.get_text(" ", strip=True) if date_node else ""
                elif english_row is not None and english_row.select_one("h5 b"):
                    title = english_row.select_one("h5 b").get_text(" ", strip=True)
                    date_node = english_row.select_one("h5 p")
                    date = date_node.get_text(" ", strip=True) if date_node else ""
                else:
                    # Legacy shop tables use a title, a date, and a PDF link in the same row.
                    row = node.find_parent("tr")
                    if row is not None:
                        cells = row.find_all(["td", "th"])
                        if cells:
                            title = cells[0].get_text(" ", strip=True)
                        date = next(
                            (
                                c.get_text(" ", strip=True)
                                for c in cells
                                if re.search(r"\d{4}", c.get_text())
                            ),
                            "",
                        )
                    elif node.parent:
                        title_node = node.parent.select_one("h3,h4,.download-title,.file-name")
                        if title_node:
                            title = title_node.get_text(" ", strip=True)
            if not title or title.lower() in {"pdf", "download", "下载"}:
                title = unquote(urlsplit(url).path).split("/")[-1].removesuffix(".pdf")
            if not version:
                match = re.search(r"(?<![A-Za-z0-9])[Vv]\d+(?:\.\d+)+", title)
                version = match[0] if match else ""
            documents[url] = {
                "url": url,
                "title": title,
                "date": date,
                "official_version": version,
                "document_type": document_type(title),
            }
        elif official_url(url):
            path = urlsplit(url).path
            # Developer SDK docs have their own version tree, outside the aircraft archive.
            if (urlsplit(url).hostname or "").startswith("developer."):
                return
            keep = (
                path == "/"
                or path.rstrip("/")
                in {
                    "/products",
                    "/download/enterprise-products",
                    "/download/customer-products",
                    "/download/ecosystem",
                }
                or (
                    path.startswith(("/doc/", "/productdetail/", "/product/"))
                    and len(path.strip("/").split("/")) == 2
                )
            )
            if urlsplit(url).hostname == "shop.autelrobotics.com":
                keep = path.startswith("/pages/") and any(
                    word in path for word in ("download", "support", "technical")
                )
            if keep:
                pages[url] = {"url": url, "title": title}

    for node in soup.select("a[href], [data-href], [data-url]"):
        for attr in ("href", "data-href", "data-url"):
            value = node.get(attr)
            if value:
                title = node.select_one("h3,h4")
                add(value, (title or node).get_text(" ", strip=True), node)
    # Older Autel models survive in the site's JSON menu configuration.
    # Parse only literal JSON; do not execute downloaded JavaScript.
    for script in soup.find_all("script", src=False):
        for match in re.finditer(r"var\s+menus_\d+\s*=\s*'([^']+)';", script.get_text()):
            try:
                menus = json.loads(match[1])
            except (ValueError, TypeError):
                continue

            def walk(value):
                if isinstance(value, dict):
                    if isinstance(value.get("url"), str):
                        add(value["url"], value.get("title", ""))
                    for child in value.values():
                        walk(child)
                elif isinstance(value, list):
                    for child in value:
                        walk(child)

            walk(menus)
    return list(pages.values()), list(documents.values())


def page_title(html: str, url: str) -> str:
    soup = BeautifulSoup(html, "html.parser")
    node = soup.find("title") or soup.find("h1")
    title = node.get_text(" ", strip=True) if node else page_scope(url)
    return re.split(r"\s*[|–]\s*", title)[0].strip()


def extract_specifications(html: str) -> tuple[list[dict], list[str]]:
    """Keep original strings and qualifiers; ambiguous multi-column tables stay in raw HTML."""
    soup = BeautifulSoup(html, "html.parser")
    for comment in soup.find_all(string=lambda s: isinstance(s, Comment)):
        comment.extract()
    groups, notes = [], []
    # Known official specification accordions; deliberately exclude marketing/layout tables.
    for section in soup.select(".insrecruit-list > ul > li"):
        heading = section.select_one(".rectue-top .name")
        parameters = []
        for table in section.select("table"):
            spanned_rows = 0
            for row in table.select("tr"):
                cells = row.find_all(["th", "td"], recursive=False)
                covered_by_previous_span = spanned_rows > 0
                spanned_rows = max(0, spanned_rows - 1)
                for cell in cells:
                    try:
                        spanned_rows = max(spanned_rows, int(cell.get("rowspan", "1")) - 1)
                    except ValueError:
                        spanned_rows = max(spanned_rows, 1)
                if (
                    covered_by_previous_span
                    or len(cells) != 2
                    or any(
                        c.get("colspan", "1") != "1" or c.get("rowspan", "1") != "1" for c in cells
                    )
                ):
                    if cells:
                        notes.append("含合并或多列单元格，未自动转换；完整内容见原始网页。")
                    continue
                name, value = [c.get_text("\n", strip=True) for c in cells]
                if name:
                    parameters.append({"name": name, "value": value})
        if parameters:
            groups.append(
                {
                    "name": heading.get_text(" ", strip=True) if heading else "技术参数",
                    "parameters": parameters,
                }
            )
    return groups, sorted(set(notes))
