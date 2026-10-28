"""Build a DJI-compatible model/specification catalog from verified local archives."""

import csv
import io
import json
import os
import re
from collections import Counter
from html import escape
from pathlib import Path

import pymupdf
from bs4 import BeautifulSoup

from tools.drone_documents.catalog import indexed_path
from tools.drone_documents.common import digest, now, write_json
from tools.drone_documents.extract import parse_specifications

from .catalog import Catalog
from .prepare import IMAGE_PLACEHOLDER, html_blocks, table_matrix, version_metadata

FORMAT = "parrot.specifications.v1"
INDEX_FIELDS = [
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
    "family",
    "source_kind",
    "official_version",
    "updated_at",
    "source_pages",
    "json_path",
    "source_sha256",
]
WEB_MODELS = [
    ("anafi", "ANAFI", "/en/drones/anafi/technical-specifications"),
    ("anafi-thermal", "ANAFI Thermal", "/en/drones/anafi-thermal/technical-specifications"),
    (
        "anafi-ai",
        "ANAFI Ai",
        "/en/drones/anafi-ai/technical-documentation/technical-specifications",
    ),
    ("anafi-usa", "ANAFI USA", "/en/drones/anafi-usa/technical-specifications"),
]
PDF_MODELS = [
    ("anafi-usa-gov", "ANAFI USA GOV", "ANAFI USA GOV User Guide"),
    ("anafi-usa-mil", "ANAFI USA MIL", "ANAFI USA MIL User Guide"),
    ("anafi-ukr", "ANAFI UKR", "ANAFI UKR User Guide"),
    ("anafi-ukr-gov", "ANAFI UKR GOV", "ANAFI UKR User Guide"),
    ("anafi-ukr-xlr", "ANAFI UKR XLR", "ANAFI UKR User Guide"),
]
GROUP_NAMES = {
    "aircraft": "飞行器",
    "drone": "飞行器",
    "anafi drone": "飞行器",
    "anafi ai drone": "飞行器",
    "anafi thermal drone": "飞行器",
    "imaging system": "成像系统",
    "video specifications": "视频规格",
    "video resolution": "视频分辨率",
    "photo specifications": "照片规格",
    "photo resolution": "照片分辨率",
    "digital zoom": "数字变焦",
    "image stabilization": "影像稳定与云台",
    "smart battery": "智能电池",
    "standard smart battery": "标准智能电池",
    "xlr smart battery": "XLR 智能电池",
    "drone smart battery": "飞行器智能电池",
    "fast-charging smart battery": "快充智能电池",
    "sensors": "传感器",
    "eo image chain": "可见光成像",
    "ir image chain": "红外成像",
    "thermal-imaging camera": "热成像相机",
    "radio link": "通信链路",
    "connectivity": "连接能力",
    "security": "安全机制",
    "cybersecurity": "网络与数据安全",
    "autonomous flight": "自主飞行",
    "navigation system": "导航系统",
    "ground control station": "地面控制站",
    "carry case": "携行箱",
    "shoulder bag": "肩包",
    "ecosystem": "生态系统",
    "ld-asph lens": "LD-ASPH 镜头",
}
PARAMETER_NAMES = {
    "weight": "质量",
    "mass": "质量",
    "size": "尺寸",
    "size folded": "折叠尺寸",
    "size unfolded": "展开尺寸",
    "maximum take-off mass (mtom)": "最大起飞质量（MTOM）",
    "maximum flight time": "最长飞行时间",
    "max flight time": "最长飞行时间",
    "maximum hover time": "最长悬停时间",
    "maximum flight distance": "最大飞行距离",
    "maximum horizontal speed": "最大水平速度",
    "max horizontal speed": "最大水平速度",
    "maximum horizontal airspeed": "最大水平空速",
    "maximum horizontal ground speed": "最大水平地速",
    "maximum ascent speed": "最大上升速度",
    "maximum descent speed": "最大下降速度",
    "maximum ascent/descent speed": "最大上升/下降速度",
    "maximum wind resistance": "最大抗风能力",
    "max wind resistance": "最大抗风能力",
    "maximum transmission range (los – line of sight) with skycontroller ukr": "视距通信距离",
    "maximum transmission distance": "最大通信距离",
    "max transmission range": "最大通信距离",
    "service ceiling": "使用升限",
    "operating temperature": "工作温度",
    "operating temperatures": "工作温度",
    "operating temperature range": "工作温度范围",
    "sensor": "传感器",
    "resolution": "分辨率",
    "digital zoom": "数字变焦",
    "iso range": "ISO 范围",
    "aperture": "光圈",
    "capacity": "容量",
    "battery capacity": "电池容量",
    "battery life": "电池续航",
    "voltage": "电压",
    "voltage (nominal)": "标称电压",
    "type": "类型",
    "maximum charging power": "最大充电功率",
    "charging port": "充电接口",
    "photo formats": "照片格式",
    "video format": "视频格式",
    "photo resolution": "照片分辨率",
    "thermal sensitivity": "热灵敏度",
    "electronic shutter speed": "电子快门速度",
    "controllable gimbal tilt range": "云台俯仰可控范围",
    "maximum propeller speed": "最大桨速",
    "sound power level": "声功率级",
    "maximum static thrust": "最大静态推力",
    "thrust to weight ratio": "推重比",
    "deployment time": "部署时间",
}


def translated(text, mapping):
    key = re.sub(r"\s*\[\d+\]\s*$", "", text).strip().casefold()
    label = mapping.get(key)
    return f"{label} / {text}" if label else text


def parameter(group, name, value, **metadata):
    return {
        "group": translated(group, GROUP_NAMES),
        "group_original": group,
        "name": translated(name, PARAMETER_NAMES),
        "name_original": name,
        "value": value.strip(),
        "footnotes": [],
        **metadata,
    }


def parse_html(raw: bytes, model_name: str):
    soup = BeautifulSoup(raw, "html.parser")
    main = soup.select_one("main, article")
    if main is None:
        raise ValueError("技术参数网页没有 main/article 正文")
    rows, group = [], ""
    for kind, text, node in html_blocks(main):
        text = IMAGE_PLACEHOLDER.sub("", text).strip()
        if kind == "heading":
            if text.casefold() in {model_name.casefold(), "technical specifications"}:
                continue
            group = text
            continue
        if not group:
            continue
        if kind == "table":
            for cells in table_matrix(node):
                if len(cells) >= 2 and cells[0].strip():
                    rows.append(parameter(group, cells[0], " | ".join(cells[1:])))
            continue
        if not text or text.lower().startswith(("download", "source:")):
            continue
        anchor = node.get("id") or next((p.get("id") for p in node.parents if p.get("id")), None)
        # Bulleted specifications belong to the preceding label (e.g. resolution,
        # multiple battery configurations, lens subfields), not to a new aircraft.
        if node.find_parent(["ul", "ol"]) and rows and rows[-1]["group_original"] == group:
            rows[-1]["value"] += ("\n" if rows[-1]["value"] else "") + text
        elif ":" in text:
            name, value = text.split(":", 1)
            rows.append(parameter(group, name.strip(), value, source_anchor=anchor))
        elif rows and rows[-1]["group_original"] == group and not rows[-1]["value"]:
            rows[-1]["value"] = text
        elif rows and rows[-1]["group_original"] == group and rows[-1]["name_original"] == "说明":
            rows[-1]["value"] += "\n" + text
        else:
            rows.append(parameter(group, "说明", text, source_anchor=anchor))
    if not rows:
        raise ValueError("网页没有可识别的参数")
    return rows


def _heading(text):
    clean = re.sub(r"\s*\[\d+\]\s*$", "", text).strip()
    return clean.casefold() in GROUP_NAMES and clean.upper() == clean


def parse_pdf_page(page):
    """Read each native column in order; retain nested bullet conditions verbatim."""
    rows = []
    native = page.get_text("dict")
    right_headers = [
        line["bbox"][0]
        for block in native["blocks"]
        for line in block.get("lines", [])
        if _heading("".join(s["text"] for s in line["spans"]).strip())
        and line["bbox"][0] > page.rect.width * 0.4
    ]
    # XLR's right column starts just left of the physical page midpoint.
    # Detect its heading margin instead of mixing it into the aircraft column.
    split = min(right_headers) - 2 if right_headers else page.rect.width / 2
    for left, right in [(0, split), (split, page.rect.width)]:
        lines = []
        for block in native["blocks"]:
            for line in block.get("lines", []):
                text = "".join(s["text"] for s in line["spans"]).strip()
                x0, y0, x1, y1 = line["bbox"]
                if text and left <= x0 < right and 50 < y0 < page.rect.height - 65:
                    lines.append((y0, x0, text, [x0, y0, x1, y1]))
        group, parts, boxes = "", [], []

        def flush(current_group):
            nonlocal parts, boxes
            if parts:
                text = "\n".join(parts).strip()
                if ":" in text:
                    name, value = text.split(":", 1)
                    name = " ".join(name.split())
                else:
                    name, value = "说明", text
                rows.append(
                    parameter(
                        current_group,
                        name,
                        value,
                        source_page=page.number + 1,
                        source_bbox=[
                            min(b[0] for b in boxes),
                            min(b[1] for b in boxes),
                            max(b[2] for b in boxes),
                            max(b[3] for b in boxes),
                        ],
                        source_text=text,
                    )
                )
            parts, boxes = [], []

        for _, _, text, box in sorted(lines):
            if _heading(text):
                flush(group)
                group = text
            elif not group:
                continue
            elif text in {"•", "l", "●"}:
                flush(group)
            elif text in {"o", "▪", "◦"}:
                continue
            elif (
                text.startswith(("©", "Figure "))
                or text == "ANAFI USA GOV"
                or text == "ANAFI USA MIL"
            ):
                continue
            else:
                parts.append(text)
                boxes.append(box)
        flush(group)
    if not rows or not any(r["group_original"] == "AIRCRAFT" for r in rows):
        raise ValueError("PDF 参数页未识别出 AIRCRAFT 参数组")
    return rows


def parse_pdf(raw: bytes, model_name: str):
    with pymupdf.open(stream=raw, filetype="pdf") as pdf:
        meta = version_metadata("\n".join(p.get_text() for p in list(pdf)[:5]))
        if model_name.startswith("ANAFI UKR"):
            pattern = re.compile(r"^\s*\d+\.\d+\.\s*" + re.escape(model_name) + r"\s*$", re.M)
            candidates = [
                i
                for i, page in enumerate(pdf)
                if pattern.search(page.get_text()) and "AIRCRAFT" in page.get_text()
            ]
        else:
            candidates = [
                p - 1
                for _, title, p in pdf.get_toc()
                if title.strip().casefold() == "technical specifications"
            ]
        if len(candidates) != 1:
            raise ValueError(f"{model_name} 参数章节定位不唯一: {candidates}")
        number = candidates[0]
        rows = parse_pdf_page(pdf[number])
        notes = []
        if model_name.startswith("ANAFI UKR"):
            # The shared spec footnotes follow the three explicitly separated variants.
            for i in range(number + 1, min(number + 5, len(pdf))):
                text = pdf[i].get_text()
                if re.search(r"^\s*6\.\s+Pack contents", text, re.M):
                    break
                for match in re.finditer(
                    r"^\s*\[(\d+)\]\s*(.+?)(?=^\s*\[\d+\]|\Z)", text, re.M | re.S
                ):
                    notes.append(
                        {
                            "marker": "[" + match[1] + "]",
                            "text": " ".join(match[2].split()),
                            "source_page": i + 1,
                        }
                    )
            if not notes:
                raise ValueError("UKR 参数缺少共享脚注页，拒绝丢弃参数测试条件")
        for row in rows:
            row["footnotes"] = [n for n in notes if n["marker"] in row["value"]]
        # Keep every shared condition available, including those relevant to headings.
        rows.extend(
            parameter("参数脚注与条件", n["marker"], n["text"], source_page=n["source_page"])
            for n in notes
        )
    return rows, meta


def render_model(model, output):
    slug = model["slug"]
    folder = indexed_path(output, slug)
    folder.mkdir(parents=True, exist_ok=True)
    json_path = folder / "技术参数.json"
    for name in ["技术参数.html", "技术参数.txt", "技术参数.json"]:
        if (folder / name).exists() and (
            not json_path.exists() or json.loads(json_path.read_text())["format"] != FORMAT
        ):
            raise ValueError(f"拒绝覆盖非本工具生成的文件: {folder / name}")
    html = [
        '<!doctype html><html lang="zh-CN"><meta charset="utf-8">',
        f'<title>{escape(model["name"])} 技术参数</title>',
        "<style>body{max-width:1050px;margin:24px auto;padding:0 20px;font:16px/1.6 sans-serif}"
        ".parameter{border-bottom:1px solid #ddd;padding:12px 0;white-space:pre-wrap}"
        "sup{display:block;font-size:13px;color:#555}h2{margin-top:32px}</style><main>",
        f'<h1>{escape(model["name"])} 技术参数</h1>',
        f'<p>厂商：Parrot；来源类型：{model["source_kind"]}；'
        f'资料版本：{escape(model.get("official_version") or "来源未标明")}；'
        f'资料更新日期：{escape(model.get("updated_at") or "来源未标明")}</p>',
        f'<p>整理时间：{escape(model["built_at"])}。网页原始采集时间未提供，不以整理时间代替。</p>',
        f'<p>{escape(model["note"])}</p>',
        f'<p><a href="{escape(model["source_url"], quote=True)}">官方来源</a></p>',
    ]
    source = Path(model["source_absolute_path"])
    if not source.is_relative_to(output):
        snapshot = output / "来源快照" / (model["source_sha256"] + source.suffix)
        snapshot.parent.mkdir(parents=True, exist_ok=True)
        if not snapshot.exists():
            snapshot.write_bytes(source.read_bytes())
        if digest(snapshot.read_bytes()) != model["source_sha256"]:
            raise ValueError("导出目录内的来源快照哈希不一致")
        source = snapshot
    model["source_snapshot_path"] = str(source.relative_to(output))
    relative = os.path.relpath(source, folder)
    if model["source_kind"] == "pdf":
        html.append(
            f'<p><a href="{escape(relative, quote=True)}#page={model["source_pages"][0]}">'
            '本地原始 PDF 参数页</a></p>'
        )
    text = [model["name"], "Source: " + model["source_url"], model["note"]]
    active = None
    for row in model["parameters"]:
        if row["group"] != active:
            active = row["group"]
            html.append("<h2>" + escape(active) + "</h2>")
            text.extend(["", active])
        html.append(
            '<div class="parameter"><strong>'
            + escape(row["name"])
            + "</strong><br>"
            + escape(row["value"])
        )
        for note in row["footnotes"]:
            html.append("<sup>" + escape(note["marker"] + " " + note["text"]) + "</sup>")
        html.append("</div>")
        text.append(row["name"] + ": " + row["value"])
        text.extend(n["marker"] + " " + n["text"] for n in row["footnotes"])
    html.append("</main></html>")
    (folder / "技术参数.html").write_text("\n".join(html), encoding="utf-8")
    (folder / "技术参数.txt").write_text("\n".join(text) + "\n", encoding="utf-8")
    public = {k: v for k, v in model.items() if k != "source_absolute_path"}
    write_json(json_path, public)
    return {
        "slug": slug,
        "name": model["name"],
        "status": "partial" if any(not r["value"] for r in model["parameters"]) else "saved",
        "source_url": model["source_url"],
        "collected_at": "",
        "group_count": len({r["group"] for r in model["parameters"]}),
        "parameter_count": len(model["parameters"]),
        "html_path": f"{slug}/技术参数.html",
        "text_path": f"{slug}/技术参数.txt",
        "sha256": digest((folder / "技术参数.html").read_bytes()),
        "error": model["note"],
        "manufacturer": "Parrot",
        "category": model["category"],
        "family": model["family"],
        "source_kind": model["source_kind"],
        "official_version": model["official_version"] or "",
        "updated_at": model["updated_at"] or "",
        "source_pages": ",".join(map(str, model["source_pages"])),
        "json_path": f"{slug}/技术参数.json",
        "source_sha256": model["source_sha256"],
    }


def write_csv(path, rows, fields):
    stream = io.StringIO(newline="")
    writer = csv.DictWriter(stream, fieldnames=fields)
    writer.writeheader()
    writer.writerows(rows)
    path.write_text(stream.getvalue(), encoding="utf-8-sig", newline="")


def build_catalog(input_root: Path, output: Path | None = None):
    catalog = Catalog(input_root)
    output = (output or input_root).resolve()
    output.mkdir(parents=True, exist_ok=True)
    built = now()
    models, missing = [], []
    for kind, entries in [("html", WEB_MODELS), ("pdf", PDF_MODELS)]:
        for slug, name, selector in entries:
            source = next(
                (
                    r
                    for r in catalog.records
                    if r["kind"] == kind
                    and (
                        r["url"].rstrip("/").endswith(selector)
                        if kind == "html"
                        else r["title"] == selector
                    )
                ),
                None,
            )
            if source is None:
                missing.append({"slug": slug, "name": name, "reason": "本地没有对应参数来源"})
                continue
            raw = catalog.read(source)
            rows, meta = (parse_html(raw, name), {}) if kind == "html" else parse_pdf(raw, name)
            family = (
                "anafi-ukr"
                if slug.startswith("anafi-ukr")
                else ("anafi-usa" if slug.startswith("anafi-usa") else slug)
            )
            model = {
                "format": FORMAT,
                "manufacturer": "Parrot",
                "slug": slug,
                "name": "Parrot " + name,
                "family": family,
                "category": "消费航拍" if slug == "anafi" else "行业应用",
                "source_kind": kind,
                "source_url": source["url"],
                "source_local_path": source["local_path"],
                "source_absolute_path": str(indexed_path(catalog.root, source["local_path"])),
                "source_sha256": source["sha256"],
                "collected_at": None,
                "built_at": built,
                "official_version": meta.get("official_version"),
                "updated_at": meta.get("updated_at"),
                "source_pages": sorted({r["source_page"] for r in rows if "source_page" in r}),
                "parameters": rows,
                "note": "依据本地官方资料快照整理；原文数值、单位、版本差异"
                "和测试条件保留，不自动合并。"
                + (
                    "本页按手册中明确的型号章节提取。"
                    if kind == "pdf"
                    else "网页中其他版本的限定说明仍随参数保留。"
                ),
            }
            models.append(model)
    if not models:
        raise ValueError("本地没有可整理的机型参数来源")
    covered = {m["family"] for m in models}
    for slug in sorted(
        {r.get("slug") for r in catalog.records + catalog.failures if r.get("slug")} - covered
    ):
        missing.append(
            {
                "slug": slug,
                "kind": "sensor" if slug == "sequoia" else "product_family",
                "reason": "传感器产品，不列为无人机"
                if slug == "sequoia"
                else "本地仅有支持页或下载失败记录，未发现可用的完整参数来源",
            }
        )
    # Validate all input extraction before publishing the derived catalog.
    index = [render_model(m, output) for m in sorted(models, key=lambda m: m["slug"])]
    for row in index:
        raw = indexed_path(output, row["html_path"]).read_bytes()
        if digest(raw) != row["sha256"] or len(parse_specifications(raw)) != row["parameter_count"]:
            raise ValueError(f"导出参数与 DJI 读取器数量/哈希不一致: {row['slug']}")
    write_csv(output / "技术参数索引.csv", index, INDEX_FIELDS)
    flat = []
    for model in models:
        for row in model["parameters"]:
            flat.append(
                {
                    "manufacturer": "Parrot",
                    "slug": model["slug"],
                    "model": model["name"],
                    "group": row["group"],
                    "parameter": row["name"],
                    "value": row["value"],
                    "conditions": "\n".join(
                        n["marker"] + " " + n["text"] for n in row["footnotes"]
                    ),
                    "source_url": model["source_url"],
                    "official_version": model["official_version"] or "",
                    "updated_at": model["updated_at"] or "",
                    "source_page": row.get("source_page", ""),
                    "source_path": model["source_local_path"],
                    "source_sha256": model["source_sha256"],
                }
            )
    if flat:
        write_csv(output / "技术参数明细.csv", flat, list(flat[0]))
    summary = {
        "format": FORMAT,
        "built_at": built,
        "manufacturer": "Parrot",
        "models": len(models),
        "parameters": len(flat),
        "results": dict(Counter(r["status"] for r in index)),
        "missing_families_or_models": len(missing),
        "scope": "已采集本地资料；不是官网全部历史机型的完整清单",
        "source_collection_time": "原采集索引未记录，保持为空；built_at 仅为整理时间",
    }
    write_json(output / "技术参数汇总.json", summary)
    write_json(
        output / "技术参数校验报告.json",
        {
            "models": len(index),
            "parameters": len(flat),
            "dji_parameter_reader_counts_match": True,
            "generated_html_hashes_match": True,
            "verified_source_files": len({m["source_sha256"] for m in models}),
            "source_index_hashes": {
                name: digest((catalog.root / name).read_bytes())
                for name in ["文档索引.csv", "网页采集记录.csv"]
                if (catalog.root / name).exists()
            },
            "items": [
                {
                    "slug": r["slug"],
                    "parameters": r["parameter_count"],
                    "html_sha256": r["sha256"],
                    "source_sha256": r["source_sha256"],
                }
                for r in index
            ],
        },
    )
    write_json(output / "技术参数缺失清单.json", missing)
    write_json(
        output / "机型参数目录.json",
        {
            **summary,
            "items": [{k: v for k, v in m.items() if k != "source_absolute_path"} for m in models],
        },
    )
    links = "\n".join(
        f'<tr><td><a href="{r["html_path"]}">{escape(r["name"])}</a></td>'
        f'<td>{r["group_count"]}</td><td>{r["parameter_count"]}</td>'
        f'<td>{escape(r["official_version"] or "网页未标明")}</td>'
        f'<td>{escape(r["updated_at"])}</td></tr>'
        for r in index
    )
    (output / "机型参数目录.html").write_text(
        '<!doctype html><html lang="zh-CN"><meta charset="utf-8"><title>Parrot 机型参数目录</title>'
        "<style>body{max-width:1100px;margin:32px auto;font:16px/1.6 sans-serif}"
        "table{border-collapse:collapse}td,th{border:1px solid #ccc;padding:10px}</style>"
        f"<h1>Parrot 机型参数目录</h1><p>{len(models)} 个机型/版本，{len(flat)} 项参数。"
        "依据本地资料整理，保留原文条件；缺失机型见技术参数缺失清单.json。</p>"
        "<table><tr><th>机型/版本</th><th>参数组</th><th>参数项</th><th>资料版本</th><th>更新日期</th></tr>"
        + links
        + "</table></html>",
        encoding="utf-8",
    )
    return summary
