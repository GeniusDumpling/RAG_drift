"""Resumable, source-attributed archive with DJI-compatible CSV fields."""

import csv
import hashlib
import html
import json
import threading
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlsplit

import requests

from . import VERSION
from .sources import (
    allowed_url,
    canonical_url,
    discover,
    extract_specifications,
    guess_language,
    page_kind,
    page_scope,
    page_title,
    safe_name,
    seeds,
    text_content,
    url_id,
)

DOCUMENT_FIELDS = [
    "slug",
    "name",
    "title",
    "date",
    "page_url",
    "url",
    "local_path",
    "status",
    "bytes",
    "sha256",
    "error",
    "manufacturer",
    "official_version",
    "language",
    "document_type",
    "collected_at",
    "final_url",
    "scope_kind",
]
PAGE_FIELDS = [
    "slug",
    "name",
    "title",
    "url",
    "local_path",
    "text_path",
    "status",
    "bytes",
    "sha256",
    "error",
    "collected_at",
    "final_url",
    "kind",
    "http_status",
    "document_count",
    "discovered_from",
    "scope_kind",
]
SPEC_FIELDS = [
    "slug",
    "name",
    "status",
    "source_url",
    "collected_at",
    "group_count",
    "parameter_count",
    "html_path",
    "text_path",
    "sha256",
    "error",
    "manufacturer",
    "category",
    "source_html_path",
    "source_sha256",
    "scope_kind",
    "json_path",
]
SUCCESS = {"downloaded", "existing", "saved"}


def now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def atomic_bytes(path: Path, data: bytes):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".part")
    temporary.write_bytes(data)
    temporary.replace(path)


def write_json(path: Path, value):
    atomic_bytes(path, (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode())


def write_csv(path: Path, rows: list[dict], fields: list[str]):
    import io

    output = io.StringIO(newline="")
    writer = csv.DictWriter(output, fieldnames=fields, extrasaction="ignore")
    writer.writeheader()
    writer.writerows(rows)
    atomic_bytes(path, output.getvalue().encode("utf-8-sig"))


def local_path(root: Path, relative: str) -> Path:
    path = (root / relative).resolve()
    if not relative or not path.is_relative_to(root.resolve()):
        raise ValueError("Local artifact path leaves archive")
    return path


def scope_kind(slug: str) -> str:
    if slug == "official-site":
        return "directory"
    if any(
        term in slug
        for term in (
            "nest",
            "control",
            "mapper",
            "command",
            "mounting",
            "parts",
            "deck",
            "asat",
            "repeater",
            "loudspeaker",
            "spotlight",
        )
    ):
        return "accessory_or_software"
    return "aircraft_or_family"


def category(slug: str) -> str:
    if scope_kind(slug) != "aircraft_or_family":
        return "配件与软件"
    if slug in {"evo", "evo-ii", "evo-ii-drones", "x-star"} or (
        any(term in slug for term in ("evo-nano", "evo-lite", "evo-ii-pro"))
        and not any(term in slug for term in ("ent", "rtk"))
    ):
        return "消费航拍"
    return "行业应用"


class FetchError(RuntimeError):
    def __init__(self, message, status=0):
        super().__init__(message)
        self.status = status


class Client:
    """Anonymous GET only; check every redirect and limit body size and host request rate."""

    def __init__(
        self,
        timeout=30.0,
        retries=2,
        delay=0.3,
        max_pdf_mb=150,
        *,
        url_policy=allowed_url,
        user_agent=None,
    ):
        self.timeout, self.retries, self.delay = timeout, retries, delay
        self.max_pdf_bytes = max_pdf_mb * 1024 * 1024
        self.lock = threading.Lock()
        self.last_request = {}
        self.url_policy = url_policy
        self.user_agent = user_agent or "Mozilla/5.0 (compatible; AutelDocumentsCollector/1.0)"

    def fetch(self, url: str, *, document=False, kind=None) -> tuple[bytes, str, dict]:
        kind = kind or ("pdf" if document else "html")
        document = kind in {"pdf", "zip", "image"}
        original = canonical_url(url)
        for attempt in range(self.retries + 1):
            current = original
            try:
                for _ in range(6):
                    if not self.url_policy(current, document=document):
                        raise FetchError("Redirect or URL outside official sources")
                    host = urlsplit(current).hostname
                    with self.lock:
                        pause = self.last_request.get(host, 0) + self.delay - time.monotonic()
                        if pause > 0:
                            time.sleep(pause)
                        self.last_request[host] = time.monotonic()
                    with requests.get(
                        current,
                        timeout=(10, self.timeout),
                        stream=True,
                        allow_redirects=False,
                        headers={
                            "User-Agent": self.user_agent,
                            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
                        },
                    ) as response:
                        status = response.status_code
                        if status in {301, 302, 303, 307, 308}:
                            location = response.headers.get("Location")
                            if not location:
                                raise FetchError("Redirect without Location", status)
                            current = canonical_url(location, current)
                            continue
                        if status != 200:
                            raise FetchError(f"HTTP {status}", status)
                        limit = self.max_pdf_bytes if document else 10 * 1024 * 1024
                        data = bytearray()
                        for chunk in response.iter_content(128 * 1024):
                            data.extend(chunk)
                            if len(data) > limit:
                                raise FetchError("Response exceeds configured size limit", status)
                        result = bytes(data)
                        if kind == "pdf":
                            if not result.startswith(b"%PDF-"):
                                raise FetchError(
                                    "Response is not a PDF (possibly an error page)", status
                                )
                            # A matching header alone does not prove the download is complete.
                            if b"%%EOF" not in result[-4096:]:
                                raise FetchError("Truncated PDF: missing EOF marker", status)
                        elif kind == "json":
                            try:
                                json.loads(result)
                            except ValueError as exc:
                                raise FetchError("Response is not JSON", status) from exc
                        elif kind == "zip":
                            if not result.startswith(b"PK"):
                                raise FetchError("Response is not a ZIP", status)
                        elif kind == "image":
                            if not result.startswith((b"\x89PNG", b"\xff\xd8", b"RIFF")):
                                raise FetchError("Response is not a supported image", status)
                        elif b"<html" not in result[:16384].lower() and (
                            b"<!doctype html" not in result[:16384].lower()
                        ):
                            raise FetchError("Response is not an HTML page", status)
                        return result, current, dict(response.headers)
                raise FetchError("Too many redirects")
            except (requests.RequestException, FetchError) as exc:
                error = exc if isinstance(exc, FetchError) else FetchError(type(exc).__name__)
                retryable = error.status in {0, 408, 429, 500, 502, 503, 504}
                if not retryable or attempt == self.retries:
                    raise error from exc
                time.sleep(min(2**attempt, 8))
        raise AssertionError("unreachable")


class Collector:
    def __init__(self, root: Path, client: Client, *, workers=4, refresh=False):
        self.root = root.resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.client, self.workers, self.refresh = client, workers, refresh
        state_path = self.root / "采集状态.json"
        self.state = (
            json.loads(state_path.read_text())
            if state_path.exists()
            else {
                "format": VERSION,
                "created_at": now(),
                "pages": {},
                "documents": {},
                "seeds": [],
            }
        )
        if self.state.get("format") != VERSION:
            raise ValueError("Unsupported collection state version")
        # Numeric English download URLs identify pages, not aircraft models.
        for row in self.state["pages"].values():
            row["slug"] = page_scope(row["url"], row.get("name", ""))
            row["scope_kind"] = scope_kind(row["slug"])
        normalized = {}
        for row in self.state["documents"].values():
            row["slug"] = page_scope(row["page_url"], row.get("name", ""))
            row["scope_kind"] = scope_kind(row["slug"])
            key = row["slug"] + "|" + row["url"]
            old = normalized.get(key, {})
            row["source_pages"] = sorted(
                set(old.get("source_pages", []) + row.get("source_pages", [row["page_url"]]))
            )
            normalized[key] = row
        self.state["documents"] = normalized
        self.checked = set()

    def log(self, row: dict):
        with (self.root / "下载进度.jsonl").open("a", encoding="utf-8") as file:
            file.write(json.dumps({"event_at": now(), **row}, ensure_ascii=False) + "\n")

    def checkpoint(self):
        self.state["updated_at"] = now()
        write_json(self.root / "采集状态.json", self.state)

    def cached(self, row: dict, *, pdf=False) -> bool:
        if self.refresh or row.get("status") not in SUCCESS or not row.get("local_path"):
            return False
        try:
            data = local_path(self.root, row["local_path"]).read_bytes()
            return digest(data) == row.get("sha256") and (
                not pdf or data.startswith(b"%PDF-") and b"%%EOF" in data[-4096:]
            )
        except (OSError, ValueError):
            return False

    def fetch_page(self, item: dict) -> dict:
        url = item["url"]
        previous = self.state["pages"].get(url, {})
        if self.cached(previous):
            return {**previous, "status": "existing", "error": ""}
        row = {
            "url": url,
            "slug": page_scope(url),
            "name": item.get("title", ""),
            "kind": page_kind(url),
            "status": "failed",
            "error": "",
            "local_path": "",
            "discovered_from": item.get("discovered_from", "seed"),
            "http_status": 0,
        }
        row["scope_kind"] = scope_kind(row["slug"])
        try:
            data, final, headers = self.client.fetch(url)
            encoding = requests.utils.get_encoding_from_headers(headers)
            if not encoding or encoding.lower() == "iso-8859-1":
                encoding = "utf-8"
            content = data.decode(encoding, errors="replace")
            row.update(
                title=page_title(content, final),
                name=page_title(content, final),
                final_url=final,
                status="saved",
                bytes=len(data),
                sha256=digest(data),
                collected_at=now(),
                http_status=200,
            )
            row["slug"] = page_scope(url, row["name"])
            row["scope_kind"] = scope_kind(row["slug"])
            stem = f"{url_id(url)}-{row['sha256'][:12]}"
            relative = Path("网页文档") / row["slug"] / (stem + ".html")
            row.update(
                local_path=relative.as_posix(), text_path=relative.with_suffix(".txt").as_posix()
            )
            atomic_bytes(self.root / relative, data)
            atomic_bytes(self.root / row["text_path"], text_content(content).encode())
            if urlsplit(url).hostname == "manuals.autelrobotics.com":
                row["discovery_status"] = "javascript_directory_not_enumerated"
        except (FetchError, ValueError, OSError) as exc:
            row.update(error=str(exc), http_status=getattr(exc, "status", 0))
            if previous.get("local_path"):
                row["previous_artifact"] = previous
        return row

    def discover_pages(self, *, extra_seeds=(), max_pages=400):
        seed_urls = list(dict.fromkeys(self.state["seeds"] + seeds() + list(extra_seeds)))
        for url in seed_urls:
            if not allowed_url(url):
                raise ValueError(f"Seed must be an official Autel page: {url}")
        self.state["seeds"] = seed_urls
        queue = {url: {"url": url, "discovered_from": "seed"} for url in seed_urls}
        self.checked = set()
        with ThreadPoolExecutor(max_workers=self.workers) as pool:
            while queue and len(self.checked) < max_pages:
                batch = [
                    queue.pop(url)
                    for url in list(queue)[
                        : min(
                            self.workers,
                            max_pages - len(self.checked),
                        )
                    ]
                ]
                for row in pool.map(self.fetch_page, batch):
                    url = row["url"]
                    self.checked.add(url)
                    self.state["pages"][url] = row
                    if row["status"] in SUCCESS:
                        content = (
                            local_path(self.root, row["local_path"])
                            .read_bytes()
                            .decode(
                                "utf-8",
                                errors="replace",
                            )
                        )
                        links, documents = discover(content, row.get("final_url", url))
                        row["document_count"] = len(documents)
                        for link in links:
                            if link["url"] not in self.checked:
                                queue.setdefault(link["url"], {**link, "discovered_from": url})
                        self.add_documents(row, documents)
                    self.log({"kind": "page", **row})
                    print(f"page {len(self.checked)} {row['status']} {url}", flush=True)
                # A batch may link back to another page being processed in the same batch.
                queue = {url: item for url, item in queue.items() if url not in self.checked}
                self.state["pending_pages"] = list(queue.values())
                self.checkpoint()
        self.state["discovery_finished_at"] = now()
        self.checkpoint()
        self.export()

    def add_documents(self, page: dict, documents: list[dict]):
        for doc in documents:
            slug, url = page["slug"], page["url"]
            key = slug + "|" + doc["url"]
            previous = self.state["documents"].get(key, {})
            self.state["documents"][key] = {
                **previous,
                **doc,
                "slug": slug,
                "name": page["name"],
                "manufacturer": "Autel",
                "scope_kind": scope_kind(slug),
                "page_url": previous.get("page_url", url),
                "source_pages": sorted(set(previous.get("source_pages", []) + [url])),
                "status": previous.get("status", "pending"),
                "discovered_at": previous.get("discovered_at", now()),
                "last_seen_at": page.get("collected_at", now()),
            }

    def reindex(self):
        """Reparse existing, hash-verified snapshots without contacting any server."""
        for row in self.state["pages"].values():
            if row["status"] not in SUCCESS:
                continue
            data = local_path(self.root, row["local_path"]).read_bytes()
            if digest(data) != row["sha256"]:
                raise ValueError("Snapshot hash mismatch: " + row["local_path"])
            _, documents = discover(
                data.decode("utf-8", errors="replace"), row.get("final_url", row["url"])
            )
            row["document_count"] = len(documents)
            self.add_documents(row, documents)
        for row in self.state["documents"].values():
            if row.get("status") in SUCCESS:
                row["language"] = guess_language(row["title"], row["url"])
        self.checkpoint()
        self.export()

    def fetch_document(self, row: dict) -> dict:
        if self.cached(row, pdf=True):
            return {
                **row,
                "status": "existing",
                "error": "",
                "language": guess_language(row["title"], row["url"]),
            }
        previous = row.copy() if row.get("local_path") else None
        result = {
            **row,
            "status": "failed",
            "local_path": "",
            "bytes": 0,
            "sha256": "",
            "error": "",
        }
        try:
            data, final, _ = self.client.fetch(row["url"], document=True)
            sha = digest(data)
            filename = f"{safe_name(row['title'], 75)}_{url_id(row['url'])}_{sha[:12]}.pdf"
            relative = Path(row["slug"]) / filename
            atomic_bytes(self.root / relative, data)
            result.update(
                status="downloaded",
                local_path=relative.as_posix(),
                bytes=len(data),
                sha256=sha,
                collected_at=now(),
                final_url=final,
            )
            result["language"] = guess_language(row["title"], row["url"])
        except (FetchError, OSError, ValueError) as exc:
            result["error"] = str(exc)
            if previous:
                result["previous_artifact"] = previous
        return result

    def download(self, *, scopes=(), max_documents=None):
        records = [r for r in self.state["documents"].values() if not scopes or r["slug"] in scopes]
        if scopes:
            missing = set(scopes) - {r["slug"] for r in self.state["documents"].values()}
            if missing:
                raise ValueError("Unknown scope: " + ", ".join(sorted(missing)))
        if max_documents is not None:
            records = records[:max_documents]
        # Fetch each URL once even if several products link to the same PDF.
        by_url = {}
        for row in records:
            by_url.setdefault(row["url"], []).append(row)
        unique = [
            next((r for r in rows if self.cached(r, pdf=True)), rows[0]) for rows in by_url.values()
        ]
        with ThreadPoolExecutor(max_workers=self.workers) as pool:
            for number, result in enumerate(pool.map(self.fetch_document, unique), 1):
                for alias in by_url[result["url"]]:
                    record = {
                        **alias,
                        **{
                            key: result.get(key, "")
                            for key in (
                                "status",
                                "local_path",
                                "bytes",
                                "sha256",
                                "error",
                                "collected_at",
                                "final_url",
                                "language",
                            )
                        },
                    }
                    if result.get("previous_artifact"):
                        record["previous_artifact"] = result["previous_artifact"]
                    self.state["documents"][alias["slug"] + "|" + alias["url"]] = record
                    self.log({"kind": "pdf", **record})
                self.checkpoint()
                print(
                    f"pdf {number}/{len(unique)} {result['status']} {result['title']}", flush=True
                )
        self.state["download_finished_at"] = now()
        self.checkpoint()
        self.export()

    def export(self):
        pages = list(self.state["pages"].values())
        documents = list(self.state["documents"].values())
        write_csv(self.root / "网页采集记录.csv", pages, PAGE_FIELDS)
        write_csv(self.root / "文档索引.csv", documents, DOCUMENT_FIELDS)
        write_json(self.root / "文档清单.json", documents)
        scopes = sorted({r["slug"] for r in documents})
        scope_rows = []
        for slug in scopes:
            rows = [r for r in documents if r["slug"] == slug]
            scope_rows.append(
                {
                    "slug": slug,
                    "name": rows[0]["name"],
                    "scope_kind": scope_kind(slug),
                    "page_url": rows[0]["page_url"],
                    "count": len(rows),
                    "saved_count": sum(r.get("status") in SUCCESS for r in rows),
                    "failed_count": sum(r.get("status") == "failed" for r in rows),
                    "status": "ok" if all(r.get("status") in SUCCESS for r in rows) else "partial",
                }
            )
        write_csv(
            self.root / "机型采集记录.csv",
            scope_rows,
            [
                "slug",
                "name",
                "page_url",
                "count",
                "status",
                "saved_count",
                "failed_count",
                "scope_kind",
            ],
        )
        specifications = self.export_specs(pages)
        successful = [r for r in documents if r.get("status") in SUCCESS]
        report = {
            "collector_version": VERSION,
            "generated_at": now(),
            "manufacturer": "Autel",
            "scope_count": len(scopes),
            "scope_kind_counts": dict(Counter(scope_kind(s) for s in scopes)),
            "document_records": len(documents),
            "successful_records": len(successful),
            "unique_pdf_urls": len({r["url"] for r in documents}),
            "unique_pdf_contents": len({r["sha256"] for r in successful}),
            "failed_documents": sum(r.get("status") == "failed" for r in documents),
            "pending_documents": sum(r.get("status", "pending") == "pending" for r in documents),
            "downloaded_bytes_unique": sum({r["sha256"]: r["bytes"] for r in successful}.values()),
            "pages": len(pages),
            "failed_pages": sum(r["status"] == "failed" for r in pages),
            "pending_pages": len(self.state.get("pending_pages", [])),
            "specification_scopes": len(specifications),
            "parameter_count": sum(r["parameter_count"] for r in specifications),
            "limitations": [
                "型号、系列、配件和软件按官方入口分别归档；scope 数量不是独立机型数。",
                "只采集公开 PDF 与产品/下载 HTML，不下载固件、APP、视频和图片压缩包。",
                "文档中心为动态目录；未枚举受限目录，PDF 来源为公开官网链接。",
                "语言由标题/路径初判，未翻译；英文站访问失败时保留失败记录。",
            ],
        }
        write_json(self.root / "汇总.json", report)
        failures = [{"kind": "page", **r} for r in pages if r["status"] == "failed"] + [
            {"kind": "pdf", **r} for r in documents if r.get("status") == "failed"
        ]
        write_json(self.root / "失败清单.json", failures)
        index = [
            "<!doctype html><meta charset='utf-8'><title>Autel 官方资料目录</title>",
            "<h1>Autel 官方资料目录</h1>",
            "<p>按官方型号或系列入口归档，包含配件及软件；数量不等同于独立机型数。</p>",
        ]
        for scope in scope_rows:
            index.append(
                f"<h2>{html.escape(scope['name'])} ({html.escape(scope['slug'])})</h2><ul>"
            )
            for row in documents:
                if row["slug"] != scope["slug"]:
                    continue
                href = row.get("local_path") or row["url"]
                index.append(
                    f'<li><a href="{html.escape(href, quote=True)}">'
                    f'{html.escape(row["title"])}</a> '
                    f'{html.escape(row.get("official_version", ""))} '
                    f'[{html.escape(row.get("status", "pending"))}] '
                    f'<a href="{html.escape(row["page_url"], quote=True)}">官方来源</a></li>'
                )
            index.append("</ul>")
        atomic_bytes(self.root / "资料目录.html", "\n".join(index).encode())
        return report

    def export_specs(self, pages: list[dict]) -> list[dict]:
        records, seen = [], set()
        # Prefer Chinese source when a model exists in both languages; raw pages retain both.
        ordered = sorted(pages, key=lambda r: ("autelrobotics.cn" not in r["url"], r["url"]))
        for page in ordered:
            if page["kind"] != "product" or page["status"] not in SUCCESS:
                continue
            groups, notes = extract_specifications(
                local_path(self.root, page["local_path"])
                .read_bytes()
                .decode("utf-8", errors="replace"),
            )
            if not groups or page["slug"] in seen:
                continue
            seen.add(page["slug"])
            slug = page["slug"]
            fragments = [
                "<!doctype html><meta charset='utf-8'>",
                "<h1>" + html.escape(page["name"]) + "</h1>",
            ]
            text = [page["name"], page["url"]]
            for group in groups:
                fragments.append("<h2>" + html.escape(group["name"]) + "</h2>")
                text.append(group["name"])
                for parameter in group["parameters"]:
                    value = html.escape(parameter["value"]).replace("\n", "<br>")
                    fragments.append(
                        "<div><strong>"
                        + html.escape(parameter["name"])
                        + "</strong>"
                        + "<p>"
                        + value
                        + "</p></div>"
                    )
                    text.append(parameter["name"] + "\n" + parameter["value"])
            raw = "\n".join(fragments).encode()
            record = {
                "slug": slug,
                "name": page["name"],
                "manufacturer": "Autel",
                "category": category(slug),
                "scope_kind": scope_kind(slug),
                "status": "partial" if notes else "saved",
                "source_url": page["url"],
                "collected_at": page["collected_at"],
                "group_count": len(groups),
                "parameter_count": sum(len(g["parameters"]) for g in groups),
                "html_path": f"{slug}/技术参数.html",
                "text_path": f"{slug}/技术参数.txt",
                "json_path": f"{slug}/技术参数.json",
                "sha256": digest(raw),
                "source_html_path": page["local_path"],
                "source_sha256": page["sha256"],
                "error": " ".join(notes),
            }
            atomic_bytes(self.root / record["html_path"], raw)
            atomic_bytes(self.root / record["text_path"], "\n\n".join(text).encode())
            write_json(self.root / record["json_path"], {**record, "groups": groups})
            records.append(record)
        write_csv(self.root / "技术参数索引.csv", records, SPEC_FIELDS)
        return records


def verify(root: Path) -> dict:
    """Re-read all indexed bytes, not merely status strings or directory counts."""
    errors, totals = [], Counter()
    for name, kind in [
        ("文档索引.csv", "pdf"),
        ("网页采集记录.csv", "html"),
        ("技术参数索引.csv", "specification"),
    ]:
        with (root / name).open(encoding="utf-8-sig", newline="") as file:
            rows = list(csv.DictReader(file))
        for row in rows:
            if row["status"] not in SUCCESS | {"partial"}:
                continue
            relative = row.get("local_path") or row.get("html_path")
            try:
                data = local_path(root, relative).read_bytes()
                if digest(data) != row["sha256"]:
                    raise ValueError("SHA256 mismatch")
                if kind == "pdf":
                    if not data.startswith(b"%PDF-") or b"%%EOF" not in data[-4096:]:
                        raise ValueError("Invalid/truncated PDF")
                    import fitz

                    with fitz.open(stream=data, filetype="pdf") as document:
                        if not document.page_count or document.needs_pass:
                            raise ValueError("Empty or encrypted PDF")
                        totals["pdf_pages_references"] += document.page_count
                else:
                    local_path(root, row["text_path"]).read_text(encoding="utf-8")
                    if kind == "specification":
                        model = json.loads(local_path(root, row["json_path"]).read_text())
                        count = sum(len(g["parameters"]) for g in model["groups"])
                        if count != int(row["parameter_count"]):
                            raise ValueError("Parameter count mismatch")
                        source = local_path(root, row["source_html_path"]).read_bytes()
                        if digest(source) != row["source_sha256"]:
                            raise ValueError("Specification source mismatch")
                totals[kind + "_records_verified"] += 1
            except (OSError, ValueError, RuntimeError, TypeError, KeyError) as exc:
                errors.append({"path": relative, "error": str(exc)})
    result = {"verified_at": now(), "ok": not errors, "counts": dict(totals), "errors": errors}
    write_json(root / "校验报告.json", result)
    return result
