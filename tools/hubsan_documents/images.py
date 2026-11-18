"""Archive official specification images and read their ruled table cells with local OCR."""

import argparse
import csv
import fcntl
import io
import json
import os
import re
import subprocess
from pathlib import Path
from urllib.parse import urljoin

import numpy as np
from bs4 import BeautifulSoup
from PIL import Image, ImageOps

from tools.autel_documents.collector import digest, write_json
from tools.drone_collection.archive import SUCCESS, Archive
from tools.hubsan_documents.collector import extract_product_specs


def ocr_cell(image):
    image = ImageOps.autocontrast(image.convert("L"))
    stream = io.BytesIO()
    image.save(stream, format="PNG")
    result = subprocess.run(
        ["tesseract", "stdin", "stdout", "-l", "chi_sim+eng", "--psm", "6", "tsv"],
        input=stream.getvalue(),
        capture_output=True,
        check=True,
        env={**os.environ, "OMP_THREAD_LIMIT": "1"},
        timeout=30,
    )
    words = [
        r
        for r in csv.DictReader(io.StringIO(result.stdout.decode()), delimiter="\t")
        if r.get("text", "").strip()
    ]
    lines = {}
    for r in words:
        key = (r["block_num"], r["par_num"], r["line_num"])
        lines.setdefault(key, []).append(r["text"])
    text = "\n".join(" ".join(parts) for parts in lines.values())
    confidence = min((float(r["conf"]) for r in words if float(r["conf"]) >= 0), default=0)
    return text, confidence


def read_image(path):
    with Image.open(path) as source:
        image = source.convert("RGB")
    width, height = image.size
    if height < width * 0.3 or width < 600 or width * height > 60_000_000:
        return {"accepted": False, "reason": "Not a tall specification table"}
    header, _ = ocr_cell(image.crop((0, 0, width, min(height, int(width * 0.4)))))
    if not re.search(r"技\s*术\s*参\s*数|specifications?", header, re.I):
        return {"accepted": False, "reason": "No explicit specification heading", "header": header}
    gray = np.array(image.convert("L"))
    # Official Hubsan tables have long pale horizontal rules separating each field.
    x0, x1 = int(width * 0.04), int(width * 0.96)
    # Older and newer tables have different column widths. Locate their whitespace gutter.
    profile = (gray[int(width * 0.4) :, :] < 180).mean(axis=0)
    low = profile < 0.008
    runs = []
    begin = None
    for x in range(int(width * 0.20), int(width * 0.46)):
        if low[x] and begin is None:
            begin = x
        elif not low[x] and begin is not None:
            runs.append((x - begin, begin, x))
            begin = None
    if begin is not None:
        runs.append((int(width * 0.46) - begin, begin, int(width * 0.46)))
    split = (max(runs)[1] + max(runs)[2]) // 2 if runs else int(width * 0.34)
    region = gray[:, x0:x1]
    ruled = ((region > 185) & (region < 250)).sum(axis=1) > (x1 - x0) * 0.72
    boundaries = []
    for y in np.flatnonzero(ruled):
        if not boundaries or y - boundaries[-1] > 5:
            boundaries.append(int(y))
    if len(boundaries) < 4:
        return {"accepted": False, "reason": "Table rules not reliably detected", "header": header}
    rows = []
    for top, bottom in zip(boundaries, boundaries[1:], strict=False):
        if bottom - top < 25 or bottom - top > width * 0.6:
            continue
        label, label_score = ocr_cell(image.crop((x0, top + 3, split, bottom - 2)))
        value, value_score = ocr_cell(image.crop((split, top + 3, x1, bottom - 2)))
        rows.append(
            {
                "name": label,
                "value": value,
                "label_confidence": label_score,
                "value_confidence": value_score,
                "source_bbox": [x0, top, x1, bottom],
            }
        )
    return {
        "accepted": True,
        "header": header,
        "rows": rows,
        "size": [width, height],
        "source_sha256": digest(Path(path).read_bytes()),
        "ocr": "tesseract chi_sim+eng psm6 cells",
        "ocr_version": 2,
        "column_split": split,
    }


def collect_images(root, *, refresh=False):
    root = Path(root)
    state = json.loads((root / "采集状态.json").read_text())
    archive = Archive(root / "参数图片", "Hubsan", ["hubsan.com"], workers=2, refresh=refresh)
    with (archive.root / ".collector.lock").open("w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        for model in state["models"].values():
            pages = [state["pages"].get(u) for u in model["product_urls"]]
            pages = [p for p in pages if p and p["status"] in SUCCESS]
            # Prefer the Chinese source when available; avoid duplicating regional bundles.
            pages.sort(key=lambda p: "chn.hubsan.com" not in p["url"])
            if not pages:
                continue
            if any(extract_product_specs((root / p["local_path"]).read_bytes()) for p in pages):
                # Legacy products publish text specifications; their marketing images are redundant.
                continue
            page = pages[0]
            soup = BeautifulSoup((root / page["local_path"]).read_bytes(), "html.parser")
            sections = soup.select(".hbs-proshowcontent > ul")
            if not sections:
                continue
            nodes = sections[1].select("img[src]") if len(sections) > 1 else []
            nodes += sections[0].select("img[src]")
            for node in nodes:
                url = urljoin(page["url"], node["src"])
                row = archive.fetch(
                    url,
                    kind="image",
                    title=model["name"] + " 官方参数图片",
                    slug=model["slug"],
                    page_url=page["url"],
                )
                if row["status"] not in SUCCESS:
                    continue
                ocr_path = archive.root / (row["local_path"] + ".ocr.json")
                if (
                    not ocr_path.exists()
                    or json.loads(ocr_path.read_text()).get("ocr_version") != 2
                ):
                    try:
                        result = read_image(archive.root / row["local_path"])
                    except (ValueError, OSError, subprocess.SubprocessError) as exc:
                        result = {"accepted": False, "reason": str(exc)}
                    result.update(
                        ocr_version=2,
                        slug=model["slug"],
                        url=url,
                        page_url=page["url"],
                        local_path="参数图片/" + row["local_path"],
                        collected_at=row["collected_at"],
                    )
                    write_json(ocr_path, result)
                archive.state["pages"][row["url"]]["ocr_path"] = ocr_path.relative_to(
                    archive.root
                ).as_posix()
                archive.checkpoint()
        return archive.export()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="采集 Hubsan 官方参数图片和 OCR 辅助记录")
    parser.add_argument("--output", type=Path, default=Path("data/Hubsan无人机文档"))
    parser.add_argument("--refresh", action="store_true")
    args = parser.parse_args()
    report = collect_images(args.output, refresh=args.refresh)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    raise SystemExit(2 if report["failed_pages"] else 0)
