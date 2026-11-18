"""Resume verified downloads, retain original sources, and publish DJI-compatible indexes."""

import html
import io
import json
import zipfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from urllib.parse import urlsplit

import fitz
from bs4 import BeautifulSoup

from tools.autel_documents.collector import (
    DOCUMENT_FIELDS,
    PAGE_FIELDS,
    SPEC_FIELDS,
    Client,
    FetchError,
    atomic_bytes,
    digest,
    local_path,
    now,
    write_csv,
    write_json,
)
from tools.autel_documents.sources import canonical_url, safe_name, text_content, url_id

SUCCESS = {"saved", "downloaded", "existing"}


def policy(domains, cdns=()):
    def allowed(url, *, document=False):
        try:
            host = urlsplit(canonical_url(url)).hostname
        except ValueError:
            return False
        return any(host == d or host.endswith("." + d) for d in domains) or (
            document and host in cdns
        )

    return allowed


class Archive:
    def __init__(self, root, brand, domains, *, cdns=(), workers=4, refresh=False):
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.brand, self.workers, self.refresh = brand, workers, refresh
        self.client = Client(
            url_policy=policy(domains, cdns),
            user_agent=f"Mozilla/5.0 (compatible; {brand}DocumentsCollector/1.0)",
        )
        path = self.root / "采集状态.json"
        self.state = (
            json.loads(path.read_text())
            if path.exists()
            else {
                "format": "official-drone-archive-v1",
                "manufacturer": brand,
                "pages": {},
                "documents": {},
                "models": {},
                "created_at": now(),
            }
        )
        if self.state["manufacturer"] != brand:
            raise ValueError("Output directory belongs to another manufacturer")

    def checkpoint(self):
        self.state["updated_at"] = now()
        write_json(self.root / "采集状态.json", self.state)

    def event(self, row):
        with (self.root / "下载进度.jsonl").open("a", encoding="utf-8") as f:
            f.write(json.dumps({"event_at": now(), **row}, ensure_ascii=False) + "\n")

    def cached(self, row):
        if self.refresh or row.get("status") not in SUCCESS or not row.get("local_path"):
            return False
        try:
            return digest(local_path(self.root, row["local_path"]).read_bytes()) == row["sha256"]
        except (OSError, ValueError):
            return False

    def store(self, url, data, *, kind, title, slug="official-site", **metadata):
        sha = digest(data)
        directory = "网页文档" if kind in {"html", "json"} else slug
        path = Path(directory) / f"{safe_name(title, 65)}_{url_id(url)}_{sha[:12]}.{kind}"
        atomic_bytes(self.root / path, data)
        row = {
            "url": url,
            "slug": slug,
            "name": title,
            "title": title,
            "kind": kind,
            "local_path": path.as_posix(),
            "bytes": len(data),
            "sha256": sha,
            "status": "saved" if kind in {"html", "json"} else "downloaded",
            "collected_at": now(),
            "error": "",
            "manufacturer": self.brand,
            **metadata,
        }
        if kind == "html":
            row["text_path"] = path.with_suffix(".txt").as_posix()
            atomic_bytes(self.root / row["text_path"], text_content(data.decode("utf-8")).encode())
        return row

    def fetch(self, url, *, kind="html", title="", slug="official-site", **metadata):
        url = canonical_url(url)
        old = self.state["pages"].get(url, {})
        if self.cached(old):
            return {**old, "status": "existing"}
        row = {
            "url": url,
            "kind": kind,
            "title": title or slug,
            "slug": slug,
            "status": "failed",
            "error": "",
            "local_path": "",
            **metadata,
        }
        try:
            data, final, _ = self.client.fetch(url, kind=kind)
            row = self.store(
                url, data, kind=kind, title=title or slug, slug=slug, final_url=final, **metadata
            )
        except (FetchError, OSError, ValueError) as exc:
            row["error"] = str(exc)
        self.state["pages"][url] = row
        self.event(row)
        self.checkpoint()
        print(f"{self.brand} {kind} {row['status']} {url}", flush=True)
        return row

    def read(self, row):
        return local_path(self.root, row["local_path"]).read_bytes()

    def add_document(self, url, slug, name, title, page_url, *, kind="pdf", **metadata):
        url = canonical_url(url, page_url)
        if not self.client.url_policy(url, document=True):
            self.state.setdefault("excluded_links", {})[url] = "Not an official document host"
            return
        key = slug + "|" + url
        old = self.state["documents"].get(key, {})
        self.state["documents"][key] = {
            **old,
            "url": url,
            "slug": slug,
            "name": name,
            "title": title,
            "page_url": page_url,
            "kind": kind,
            "manufacturer": self.brand,
            "source_pages": sorted(set(old.get("source_pages", []) + [page_url])),
            "status": old.get("status", "pending"),
            **metadata,
        }

    def _download(self, row):
        if self.cached(row):
            return {**row, "status": "existing"}
        result = {**row, "status": "failed", "error": "", "local_path": "", "sha256": ""}
        try:
            data, final, _ = self.client.fetch(row["url"], kind=row.get("kind", "pdf"))
            if row.get("kind", "pdf") == "pdf":
                with fitz.open(stream=data, filetype="pdf") as pdf:
                    if not pdf.page_count or pdf.needs_pass:
                        raise ValueError("Empty or encrypted PDF")
            elif row["kind"] == "zip":
                with zipfile.ZipFile(io.BytesIO(data)) as z:
                    if sum(i.file_size for i in z.infolist()) > 300 * 1024 * 1024:
                        raise ValueError("ZIP expanded size exceeds archive limit")
                    if len(z.infolist()) > 200:
                        raise ValueError("Too many ZIP members")
            result = {
                **row,
                **self.store(
                    row["url"],
                    data,
                    kind=row.get("kind", "pdf"),
                    title=row["title"],
                    slug=row["slug"],
                    final_url=final,
                ),
                "name": row["name"],
            }
        except (FetchError, OSError, ValueError, RuntimeError, zipfile.BadZipFile) as exc:
            result["error"] = str(exc)
        return result

    def download(self):
        grouped = {}
        for key, row in self.state["documents"].items():
            if not row.get("archive_member"):
                grouped.setdefault(row["url"], []).append((key, row))
        rows = [
            next((r for _, r in group if self.cached(r)), group[0][1]) for group in grouped.values()
        ]
        with ThreadPoolExecutor(max_workers=self.workers) as pool:
            futures = [pool.submit(self._download, row) for row in rows]
            for index, future in enumerate(as_completed(futures), 1):
                result = future.result()
                for key, old in grouped[result["url"]]:
                    common = {
                        k: result.get(k, "")
                        for k in [
                            "local_path",
                            "sha256",
                            "bytes",
                            "status",
                            "error",
                            "collected_at",
                            "final_url",
                        ]
                    }
                    row = {**old, **common}
                    self.state["documents"][key] = row
                    if row.get("kind") == "zip" and row["status"] in SUCCESS:
                        try:
                            self.extract_zip(row)
                        except (ValueError, RuntimeError, zipfile.BadZipFile) as exc:
                            row["archive_extract_error"] = str(exc)
                            self.state.setdefault("archive_errors", {})[key] = str(exc)
                    self.event(row)
                self.checkpoint()
                print(
                    f"{self.brand} document {index}/{len(rows)} {result['status']} "
                    + result["title"],
                    flush=True,
                )
        self.export()

    def extract_zip(self, row):
        with zipfile.ZipFile(local_path(self.root, row["local_path"])) as z:
            for member in z.infolist():
                if (
                    not member.filename.lower().endswith(".pdf")
                    or member.file_size > 150 * 1024 * 1024
                ):
                    continue
                data = z.read(member)
                if not data.startswith(b"%PDF-"):
                    continue
                with fitz.open(stream=data, filetype="pdf") as pdf:
                    if not pdf.page_count or pdf.needs_pass:
                        continue
                title = Path(member.filename).name
                result = self.store(
                    row["url"],
                    data,
                    kind="pdf",
                    title=title,
                    slug=row["slug"],
                    name=row["name"],
                    page_url=row["page_url"],
                    archive_member=member.filename,
                    archive_sha256=row["sha256"],
                )
                self.state["documents"][row["slug"] + "|" + row["url"] + "|" + member.filename] = (
                    result
                )

    def export(self):
        docs = list(self.state["documents"].values())
        pages = list(self.state["pages"].values())
        write_csv(
            self.root / "文档索引.csv",
            docs,
            DOCUMENT_FIELDS + ["kind", "archive_member", "archive_sha256", "menu_languages"],
        )
        write_csv(
            self.root / "网页采集记录.csv",
            pages,
            PAGE_FIELDS
            + [
                "manufacturer",
                "capture_kind",
                "api_source_url",
                "api_source_sha256",
                "api_updated_at",
            ],
        )
        write_json(self.root / "文档清单.json", docs)
        good = [r for r in docs if r.get("status") in SUCCESS]
        pdfs = [r for r in good if r.get("kind", "pdf") == "pdf"]
        report = {
            "manufacturer": self.brand,
            "generated_at": now(),
            "document_references": len(docs),
            "successful_document_references": len(good),
            "unique_pdfs": len({r["sha256"] for r in pdfs}),
            "unique_pdf_bytes": sum({r["sha256"]: r["bytes"] for r in pdfs}.values()),
            "unique_zips": len({r["sha256"] for r in good if r.get("kind") == "zip"}),
            "successful_images": sum(
                r.get("kind") == "image" and r["status"] in SUCCESS for r in pages
            ),
            "successful_pages": sum(r["status"] in SUCCESS for r in pages),
            "failed_pages": sum(r["status"] == "failed" for r in pages),
            "failed_documents": sum(r.get("status") == "failed" for r in docs),
            "pending_documents": sum(r.get("status") == "pending" for r in docs),
        }
        write_json(self.root / "汇总.json", report)
        write_json(
            self.root / "失败清单.json", [r for r in pages + docs if r.get("status") == "failed"]
        )
        lines = ["<!doctype html><meta charset='utf-8'>", f"<h1>{self.brand} 官方资料</h1><ul>"]
        for r in docs:
            href = r.get("local_path") or r["url"]
            lines.append(
                f'<li>{html.escape(r["name"])} — '
                f'<a href="{html.escape(href, quote=True)}">{html.escape(r["title"])}</a>'
                f' [{r.get("status", "pending")}]</li>'
            )
        atomic_bytes(self.root / "资料目录.html", ("\n".join(lines) + "</ul>").encode())
        return report

    def verify(self):
        errors, seen = [], set()
        for row in list(self.state["pages"].values()) + list(self.state["documents"].values()):
            if row.get("status") not in SUCCESS or row.get("local_path") in seen:
                continue
            try:
                data = self.read(row)
                assert digest(data) == row["sha256"], "SHA256 mismatch"
                if row.get("kind") == "pdf":
                    with fitz.open(stream=data, filetype="pdf") as pdf:
                        assert pdf.page_count and not pdf.needs_pass
                seen.add(row["local_path"])
            except (OSError, ValueError, RuntimeError, AssertionError) as exc:
                errors.append({"path": row.get("local_path"), "error": str(exc)})
        catalog = self.root / "机型参数目录"
        if (catalog / "汇总.json").exists():
            for row in json.loads((catalog / "汇总.json").read_text())["models"]:
                try:
                    data = local_path(catalog, row["html_path"]).read_bytes()
                    assert digest(data) == row["sha256"], "Catalog HTML hash mismatch"
                    model = json.loads(local_path(catalog, row["json_path"]).read_text())
                    parameters = [p for g in model["groups"] for p in g["parameters"]]
                    assert len(parameters) == row["parameter_count"], "Catalog count mismatch"
                    soup = BeautifulSoup(data, "html.parser")
                    actual = [
                        (s.get_text(), s.parent.find("p").get_text("\n"))
                        for s in soup.select("strong")
                    ]
                    assert actual == [
                        (p["name"], p["value"]) for p in parameters
                    ], "Catalog values mismatch"
                    if model.get("source_local_path"):
                        source = local_path(self.root, model["source_local_path"]).read_bytes()
                        assert (
                            digest(source) == model["source_sha256"]
                        ), "Catalog source hash mismatch"
                except (OSError, ValueError, RuntimeError, AssertionError, KeyError) as exc:
                    errors.append({"path": row.get("html_path"), "error": str(exc)})
        report = {
            "verified_at": now(),
            "ok": not errors,
            "files_verified": len(seen),
            "errors": errors,
        }
        write_json(self.root / "校验报告.json", report)
        return report


def write_catalog(root, brand, models):
    output = Path(root) / "机型参数目录"
    records = []
    for model in models:
        slug, name, groups = model["slug"], model["name"], model["groups"]
        if not groups:
            continue
        fragments = ["<!doctype html><meta charset='utf-8'>", f"<h1>{html.escape(name)}</h1>"]
        text = [name, model["source_url"]]
        for group in groups:
            fragments.append(f"<h2>{html.escape(group['name'])}</h2>")
            for p in group["parameters"]:
                value = html.escape(p["value"]).replace("\n", "<br>")
                fragments.append(
                    f"<div><strong>{html.escape(p['name'])}</strong><p>{value}</p></div>"
                )
                text.append(p["name"] + "\n" + p["value"])
        data = "\n".join(fragments).encode()
        row = {
            "slug": slug,
            "name": name,
            "manufacturer": brand,
            "category": model.get("category", "消费航拍"),
            "status": model.get("status", "saved"),
            "source_url": model["source_url"],
            "collected_at": model["collected_at"],
            "group_count": len(groups),
            "parameter_count": sum(len(g["parameters"]) for g in groups),
            "html_path": f"{slug}/技术参数.html",
            "text_path": f"{slug}/技术参数.txt",
            "json_path": f"{slug}/技术参数.json",
            "sha256": digest(data),
            "error": model.get("note", ""),
            "official_version": model.get("official_version", ""),
            "updated_at": model.get("updated_at", ""),
            "source_local_path": model.get("source_local_path", ""),
            "source_sha256": model.get("source_sha256", ""),
            "source_image_url": model.get("source_image_url", ""),
        }
        atomic_bytes(output / row["html_path"], data)
        atomic_bytes(output / row["text_path"], "\n\n".join(text).encode())
        write_json(output / row["json_path"], {**model, **row})
        records.append(row)
    write_csv(
        output / "技术参数索引.csv",
        records,
        SPEC_FIELDS + ["official_version", "updated_at", "source_local_path", "source_image_url"],
    )
    report = {
        "manufacturer": brand,
        "generated_at": now(),
        "model_count": len(records),
        "parameter_count": sum(r["parameter_count"] for r in records),
        "models": records,
    }
    write_json(output / "汇总.json", report)
    return report
