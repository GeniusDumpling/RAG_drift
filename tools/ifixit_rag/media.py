"""Download source-identified images and maintain an atomic, content-addressed catalog."""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlparse

import httpx
from PIL import Image

from .bundle import load_bundle
from .common import ROOT

GUID = re.compile(r"^[A-Za-z0-9_-]{8,80}$")


def atomic_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    with temporary.open("w", encoding="utf-8") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())
    temporary.replace(path)
    directory = os.open(path.parent, os.O_RDONLY)
    try:
        os.fsync(directory)
    finally:
        os.close(directory)


def fetch_one(root: Path, guid: str, url: str, client: httpx.Client) -> dict:
    parsed = urlparse(url)
    if (
        not GUID.fullmatch(guid)
        or parsed.scheme != "https"
        or parsed.hostname != "guide-images.cdn.ifixit.com"
        or parsed.path != f"/igi/{guid}.full"
    ):
        raise ValueError("unexpected source image identity or URL")
    catalog = root / "catalog" / f"{guid}.json"
    if catalog.exists():
        entry = json.loads(catalog.read_text())
        path = root / entry["storage_key"]
        if (
            entry.get("source_url") == url
            and entry.get("status") == "confirmed"
            and path.is_file()
            and hashlib.sha256(path.read_bytes()).hexdigest() == entry["sha256"]
        ):
            return entry
    error = None
    variants = [
        url,
        url.removesuffix(".full") + ".large",
        url.removesuffix(".full") + ".standard",
        url.removesuffix(".full") + ".thumbnail",
    ]
    variant = 0
    for attempt in range(4):
        try:
            download_url = variants[variant]
            with client.stream("GET", download_url) as response:
                response.raise_for_status()
                data = bytearray()
                for part in response.iter_bytes():
                    data.extend(part)
                    if len(data) > 30 * 1024 * 1024:
                        raise ValueError("source image exceeds 30 MiB")
            raw = bytes(data)
            with Image.open(io.BytesIO(raw)) as image:
                image.load()
                width, height, kind = image.width, image.height, image.format
            formats = {
                "JPEG": ("jpg", "image/jpeg"),
                "PNG": ("png", "image/png"),
                "WEBP": ("webp", "image/webp"),
            }
            if kind not in formats:
                raise ValueError("unsupported image format")
            extension, mime = formats[kind]
            sha = hashlib.sha256(raw).hexdigest()
            key = f"blobs/{sha}.{extension}"
            target = root / key
            target.parent.mkdir(parents=True, exist_ok=True)
            if not target.exists():
                temporary = target.with_suffix(f".{guid}.tmp")
                temporary.write_bytes(raw)
                temporary.replace(target)
            entry = {
                "guid": guid,
                "source_url": url,
                "download_url": download_url,
                "sha256": sha,
                "storage_key": key,
                "mime_type": mime,
                "width": width,
                "height": height,
                "byte_size": len(raw),
                "status": "confirmed",
                "mapping_evidence": "downloaded an exact source media GUID variant",
                "fetched_at": datetime.now(UTC).isoformat(),
            }
            atomic_json(catalog, entry)
            return entry
        except (httpx.HTTPError, OSError, ValueError) as exc:
            error = exc
            if isinstance(exc, httpx.HTTPStatusError) and exc.response.status_code in (
                403,
                404,
                410,
            ):
                variant += 1
                if variant == len(variants):
                    break
            if attempt < 3:
                time.sleep(2**attempt)
    raise RuntimeError(f"{guid}: {type(error).__name__}: {error}")


def download(
    bundle_dir: Path, root: Path, workers: int = 8, guide_ids: list[int] | None = None
) -> dict:
    bundle = load_bundle(bundle_dir)
    refs = bundle["media_refs"]
    if guide_ids:
        docs = {g["document_id"] for g in bundle["guides"] if g["guide_id"] in guide_ids}
        refs = [r for r in refs if r["document_id"] in docs]
    sources = {r["source_guid"]: r["source_url"] for r in refs if r["source_guid"]}
    root.mkdir(parents=True, exist_ok=True)
    confirmed, errors = {}, []
    with httpx.Client(
        timeout=60,
        follow_redirects=False,
        headers={"User-Agent": "IntelligenceRAG-iFixitMedia/1.0"},
    ) as client:
        with ThreadPoolExecutor(max_workers=workers) as pool:
            pending = {
                pool.submit(fetch_one, root, guid, url, client): guid
                for guid, url in sources.items()
            }
            for future in as_completed(pending):
                guid = pending[future]
                try:
                    confirmed[guid] = future.result()
                except Exception as exc:
                    errors.append({"guid": guid, "error": str(exc)[:800]})
                if (len(confirmed) + len(errors)) % 100 == 0:
                    print(
                        json.dumps(
                            {
                                "confirmed": len(confirmed),
                                "failed": len(errors),
                                "total": len(sources),
                            }
                        ),
                        flush=True,
                    )
    report = {
        "completed_at": datetime.now(UTC).isoformat(),
        "source_guids": len(sources),
        "confirmed": len(confirmed),
        "failed": len(errors),
        "errors": errors,
        "confirmed_references": sum(r["source_guid"] in confirmed for r in refs),
        "unique_files": len({entry["sha256"] for entry in confirmed.values()}),
    }
    atomic_json(
        root / ("download-report.json" if not guide_ids else "pilot-download-report.json"), report
    )
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bundle", type=Path, required=True)
    parser.add_argument("--media-dir", type=Path, default=ROOT / "data/ifixit-media")
    parser.add_argument("--workers", type=int, default=8, choices=range(1, 17))
    parser.add_argument("--guide-id", action="append", type=int)
    args = parser.parse_args()
    result = download(args.bundle, args.media_dir, args.workers, args.guide_id)
    print(json.dumps(result, ensure_ascii=False), flush=True)
    return 2 if result["failed"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
