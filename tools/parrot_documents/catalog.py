"""Read Parrot's two archive indexes without changing the source files."""

from collections import defaultdict
from pathlib import Path
from urllib.parse import urlsplit

from tools.drone_documents.catalog import indexed_path, read_index
from tools.drone_documents.common import digest

NAMES = {
    "anafi": "ANAFI",
    "anafi-ai": "ANAFI Ai",
    "anafi-usa": "ANAFI USA",
    "anafi-ukr": "ANAFI UKR",
    "anafi-thermal": "ANAFI Thermal",
}
SUCCESS = {"existing", "downloaded", "saved"}


def scope_id(slug: str) -> str:
    return "parrot:" + slug


def document_type(title: str) -> str:
    title = title.casefold()
    for terms, kind in [
        (("user guide", "user manual", "用户手册"), "user_manual"),
        (("quick start", "quick-start"), "quick_start"),
        (("safety", "安全"), "safety_guidelines"),
        (("white paper", "whitepaper"), "white_paper"),
        (("conformity", "certificate"), "declaration_of_conformity"),
        (("product sheet", "datasheet"), "product_sheet"),
        (("release notes",), "release_notes"),
    ]:
        if any(term in title for term in terms):
            return kind
    return "reference"


class Catalog:
    def __init__(self, root: Path):
        self.root = root.resolve()
        self.records = []
        self.failures = []
        self.scopes = {}
        for kind, filename in [("pdf", "文档索引.csv"), ("html", "网页采集记录.csv")]:
            for original in read_index(self.root, filename):
                row = dict(original)
                relative = row.get("local_path", "").replace("\\", "/")
                url = urlsplit(row.get("url", ""))
                parts = url.path.strip("/").split("/")
                slug = row.get("slug") or (
                    Path(relative).parts[1]
                    if len(Path(relative).parts) > 2
                    else (
                        "sdk-" + parts[1]
                        if url.hostname == "developer.parrot.com" and len(parts) > 1
                        else "documentation"
                    )
                )
                self.scopes[slug] = {
                    "slug": scope_id(slug),
                    "name": "Parrot " + NAMES.get(slug, slug),
                    "manufacturer": "Parrot",
                }
                row.update(kind=kind, scope=slug)
                if row.get("status") not in SUCCESS:
                    self.failures.append(row)
                    continue
                path = indexed_path(self.root, relative)
                if path.suffix.lower() != (".pdf" if kind == "pdf" else ".html"):
                    raise ValueError(f"索引格式与文件类型不符: {relative}")
                row.update(local_path=relative, title=row.get("title") or path.stem)
                self.records.append(row)
        if not self.scopes:
            raise ValueError("未找到 Parrot 文档索引或网页采集记录")

    def summaries(self) -> list[dict]:
        return [
            {
                "scope": slug,
                **info,
                "scope_kind": "sdk" if slug.startswith("sdk-") else "product",
                "pdfs": len(
                    {r["sha256"] for r in self.records if r["scope"] == slug and r["kind"] == "pdf"}
                ),
                "webpages": sum(r["scope"] == slug and r["kind"] == "html" for r in self.records),
                "failed_records": sum(r["scope"] == slug for r in self.failures),
            }
            for slug, info in sorted(self.scopes.items())
        ]

    def select(self, scopes=None, kind="all", types=None, limit=None) -> list[dict]:
        if scopes and set(scopes) - self.scopes.keys():
            raise ValueError("未知 scope: " + ", ".join(sorted(set(scopes) - self.scopes.keys())))
        grouped = defaultdict(list)
        # Group using the complete archive before selecting. Shared-file identities and
        # all aliases therefore remain stable across per-scope and full-corpus runs.
        for row in self.records:
            if kind != "all" and row["kind"] != kind:
                continue
            key = (row["kind"], row["sha256"])
            if row["kind"] == "html":
                key += (row["url"],)
            grouped[key].append(row)
        result = []
        for aliases in grouped.values():
            if scopes and not any(r["scope"] in scopes for r in aliases):
                continue
            ordered = sorted(aliases, key=lambda r: (r["scope"], r["title"], r["url"]))
            # Prefer informative support titles over generic "Download" titles.
            row = min(
                ordered,
                key=lambda r: ("download" in r["title"].lower(), len(r["title"]), r["title"]),
            )
            dtype = (
                document_type(row["title"])
                if row["kind"] == "pdf"
                else ("sdk_documentation" if row["scope"].startswith("sdk-") else "product_page")
            )
            if types and dtype not in types:
                continue
            result.append({**row, "document_type": dtype, "aliases": ordered})
        result.sort(key=lambda r: (r["kind"] != "pdf", r["scope"], r["title"], r["url"]))
        return result[:limit] if limit is not None else result

    def read(self, row: dict) -> bytes:
        path = indexed_path(self.root, row["local_path"])
        raw = path.read_bytes()
        if not row.get("sha256") or digest(raw) != row["sha256"]:
            raise ValueError(f"原文件哈希与索引不一致: {row['local_path']}")
        if row["kind"] == "pdf" and not raw.startswith(b"%PDF-"):
            raise ValueError(f"不是 PDF: {path.name}")
        parsed = urlsplit(row["url"])
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise ValueError("索引来源 URL 无效")
        return raw
