"""Prepare immutable native-text and pixel evidence; no model-generated captions as source."""

import hashlib
import json
from pathlib import Path

from app.services.video_graph.identity import stable_hash

PREPARE_VERSION = "document-evidence-v4-correct-crop-matrix"


def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def save_json(path, value):
    # Reuse the parser's unique-temp + atomic-replace writer. Separate workers can
    # finish identical requests concurrently without sharing a .tmp filename.
    from tools.drone_documents.common import write_json

    write_json(Path(path), value)


def sha256(data):
    return hashlib.sha256(data).hexdigest()


def checked_path(root, relative):
    root = Path(root).resolve()
    path = (root / relative).resolve()
    if not path.is_relative_to(root):
        raise ValueError("文档资源路径越界")
    return path


def ensure_report_path(root, output):
    """Never let a report overwrite its original PDF, pixels or source manifest."""
    root, output = Path(root).resolve(), Path(output).resolve()
    data = read_json(root / "source.json")
    protected = {root / "source.json", root / "prepare.plan.json"}
    protected.update(checked_path(root, a["storage_key"]) for a in data["assets"].values())
    if output in protected:
        raise ValueError("报告不能覆盖来源清单或原始证据资源")


def add_asset(root, raw, suffix, mime):
    digest = sha256(raw)
    key = f"assets/{digest}{suffix}"
    path = checked_path(root, key)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() and sha256(path.read_bytes()) != digest:
        raise ValueError("已有内容寻址资源损坏")
    if not path.exists():
        path.write_bytes(raw)
    return dict(id=digest, storage_key=key, sha256=digest, mime_type=mime, bytes=len(raw))


def normalized_box(rect, page):
    return [
        round(rect[0] / page.width * 1000, 2),
        round(rect[1] / page.height * 1000, 2),
        round(rect[2] / page.width * 1000, 2),
        round(rect[3] / page.height * 1000, 2),
    ]


def prepare(selection_path, output, *, source_root=None):
    import pymupdf

    from tools.drone_documents.geometry import _merge_boxes

    root, source_root = Path(output), Path(source_root or Path.cwd())
    selection = read_json(selection_path)
    config_hash = stable_hash([PREPARE_VERSION, pymupdf.VersionBind, selection])
    if (root / "source.json").exists():
        existing = load_bundle(root)
        if existing["config_hash"] != config_hash:
            raise ValueError("来源或解析规则已变，请使用新的输出目录")
        return existing
    if root.exists() and any(root.iterdir()):
        plan = root / "prepare.plan.json"
        if not plan.exists() or read_json(plan).get("config_hash") != config_hash:
            raise ValueError("未完成准备目录的来源/规则不同，请使用新目录")
    root.mkdir(parents=True, exist_ok=True)
    save_json(root / "prepare.plan.json", dict(config_hash=config_hash))
    bundle = dict(format_version=1, config_hash=config_hash, manuals=[], cases=[], assets={})
    for manual in selection["manuals"]:
        path = checked_path(source_root, manual["source_path"])
        raw = path.read_bytes()
        if sha256(raw) != manual["sha256"]:
            raise ValueError("原始手册哈希与固定选择不符")
        source_id = manual["sha256"]
        pdf_asset = add_asset(root, raw, ".pdf", "application/pdf")
        bundle["assets"][pdf_asset["id"]] = pdf_asset
        document = {
            k: manual[k]
            for k in (
                "brand",
                "model",
                "title",
                "language",
                "manual_version",
                "manual_date",
                "date_basis",
                "source_url",
                "sha256",
                "pdf_pages",
            )
        }
        document.update(id=source_id, pdf_asset_id=pdf_asset["id"], pages={})
        with pymupdf.open(stream=raw, filetype="pdf") as pdf:
            if len(pdf) != manual["pdf_pages"]:
                raise ValueError("固定PDF页数不符")
            for number in sorted({n for v in manual["sample_pages"].values() for n in v}):
                if not 1 <= number <= len(pdf):
                    raise ValueError("试点页码越界")
                page = pdf[number - 1]
                page_id = f"{source_id[:16]}:p{number}"
                width, height = page.rect.width, page.rect.height
                full = add_asset(
                    root,
                    page.get_pixmap(matrix=pymupdf.Matrix(1.6, 1.6), alpha=False).tobytes("png"),
                    ".png",
                    "image/png",
                )
                bundle["assets"][full["id"]] = full
                lines = []
                for block in page.get_text("dict")["blocks"]:
                    for line in block.get("lines", []):
                        text = "".join(s["text"] for s in line["spans"])
                        if text.strip():
                            lines.append(
                                dict(
                                    id=f"{page_id}:l{len(lines)}",
                                    text=text,
                                    bbox=normalized_box(line["bbox"], page.rect),
                                )
                            )
                drawings = page.get_drawings()
                # These official diagrams contain tens of thousands of vector paths.
                # Bounded native clustering preserves them instead of treating only
                # embedded bitmap objects as pictures.
                fallback = len(drawings) > 80000
                vectors = (
                    page.cluster_drawings(drawings=drawings)
                    if not fallback
                    else [
                        pymupdf.Rect(0, height * i / 4, width, height * (i + 1) / 4)
                        for i in range(4)
                    ]
                )
                boxes = _merge_boxes(
                    [pymupdf.Rect(i["bbox"]) for i in page.get_image_info()] + vectors, page.rect
                )
                regions = []
                for box in boxes:
                    box = (box + (-6, -6, 6, 6)) & page.rect
                    area = box.get_area() / page.rect.get_area()
                    if (
                        area < 0.008
                        or area > 0.88
                        or box.y1 < height * 0.05
                        or box.y0 > height * 0.95
                    ):
                        continue
                    crop = add_asset(
                        root,
                        page.get_pixmap(
                            matrix=pymupdf.Matrix(
                                min(2, 1500 / max(box.width, box.height)),
                                min(2, 1500 / max(box.width, box.height)),
                            ),
                            clip=box,
                            alpha=False,
                        ).tobytes("png"),
                        ".png",
                        "image/png",
                    )
                    bundle["assets"][crop["id"]] = crop
                    regions.append(
                        dict(
                            id=f"{page_id}:r{len(regions)}",
                            bbox=normalized_box(box, page.rect),
                            asset_id=crop["id"],
                        )
                    )
                document["pages"][str(number)] = dict(
                    id=page_id,
                    number=number,
                    width=width,
                    height=height,
                    asset_id=full["id"],
                    lines=lines,
                    regions=regions,
                    geometry_fallback=fallback,
                )
        bundle["manuals"].append(document)
        for kind, numbers in manual["sample_pages"].items():
            cid = ("DJI" if manual["brand"] == "DJI" else "PARROT") + "-" + kind
            # Keep the complete selected range, but bound vision calls to one physical
            # page so long parameter chapters cannot exhaust provider context/timeouts.
            for number in numbers:
                bundle["cases"].append(
                    dict(
                        id=f"{cid}-p{number}" if len(numbers) > 1 else cid,
                        group_id=cid,
                        source_id=source_id,
                        kind=kind,
                        pages=[number],
                    )
                )
    bundle["source_hash"] = stable_hash(bundle)
    save_json(root / "source.json", bundle)
    return load_bundle(root)


def load_bundle(root):
    root = Path(root)
    data = read_json(root / "source.json")
    if data.get("source_hash") != stable_hash(
        {k: v for k, v in data.items() if k != "source_hash"}
    ):
        raise ValueError("文档来源包校验失败")
    for asset in data["assets"].values():
        if sha256(checked_path(root, asset["storage_key"]).read_bytes()) != asset["sha256"]:
            raise ValueError("文档图片或PDF资源损坏")
    return data


def case_source(bundle, case):
    manual = next(m for m in bundle["manuals"] if m["id"] == case["source_id"])
    return manual, [manual["pages"][str(n)] for n in case["pages"]]
