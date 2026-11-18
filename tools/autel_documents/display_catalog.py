"""Build an aircraft-only catalog from verified official page snapshots."""

import csv
import html
import json
from pathlib import Path

from bs4 import BeautifulSoup

from .collector import (
    SPEC_FIELDS,
    SUCCESS,
    atomic_bytes,
    digest,
    local_path,
    now,
    write_csv,
    write_json,
)
from .sources import extract_specifications

# Prefer the current Chinese routes. Different site routes are not different models.
# data-type values explicitly separate V2 and V3 on the official EVO II pages.
MODELS = [
    ("autel-alpha", "Autel Alpha", "autel-alpha", "", "行业应用"),
    ("autel-evo-iii", "Autel EVO III 系列", "autel-evo-iii", "", "行业应用"),
    ("dragonfish-25", "Dragonfish-25 道通龙鱼", "dragonfish-25", "", "行业应用"),
    ("evo-ii-dual-640t-v3", "EVO II Dual 640T V3", "evo-ii-dual", "V3", "行业应用"),
    ("evo-ii-dual-640t-v2", "EVO II Dual 640T V2", "evo-ii-dual", "V2", "行业应用"),
    ("evo-ii-enterprise-v3", "EVO II 行业系列 V3", "evo-ii-enterprise-v3", "V3", "行业应用"),
    ("evo-ii-enterprise-v2", "EVO II 行业系列 V2", "evo-ii-enterprise-v3", "V2", "行业应用"),
    ("evo-ii-rtk-v3", "EVO II RTK 系列 V3", "evo-ii-rtk", "V3", "行业应用"),
    ("evo-ii-rtk-v2", "EVO II RTK 系列 V2", "evo-ii-rtk", "V2", "行业应用"),
    ("evo-ii-pro-v3", "EVO II Pro V3", "evo-ii-pro", "V3", "消费航拍"),
    ("evo-ii", "EVO II", "evo-ii", "", "消费航拍"),
    ("evo-lite-enterprise", "EVO Lite 行业系列", "evo-lite-enterprise-series", "", "行业应用"),
    ("evo-lite", "EVO Lite / Lite+ 系列", "evo-lite", "", "消费航拍"),
    ("evo-nano", "EVO Nano / Nano+ 系列", "evo-nano", "", "消费航拍"),
    ("evo-max-4t-v2", "EVO Max 4T V2", "evo-max-4t", "", "行业应用"),
    ("evo-max-4n-v2", "EVO Max 4N V2", "evo-max-4n", "", "行业应用"),
    ("evo-max-series", "EVO Max 系列（4T / 4N / 4T XE）", "evo-max-series", "", "行业应用"),
]


def project_column(source: str, model: str) -> tuple[list[dict], list[str]]:
    """Project only tables explicitly headed by model; never infer column positions."""
    soup = BeautifulSoup(source, "html.parser")
    groups, notes = [], []
    for section in soup.select(".insrecruit-list > ul > li"):
        heading = section.select_one(".rectue-top .name")
        parameters = []
        for table in section.select("table"):
            rows = table.select("tr")
            if not rows:
                continue
            headers = rows[0].find_all(["th", "td"], recursive=False)
            labels = [h.get_text(" ", strip=True).casefold() for h in headers]
            if labels.count(model.casefold()) != 1:
                raise ValueError("Cannot identify explicit Dragonfish model column")
            column = labels.index(model.casefold())
            for row in rows[1:]:
                cells = row.find_all(["td", "th"], recursive=False)
                if len(cells) != len(headers) or any(
                    c.get("rowspan", "1") != "1" or c.get("colspan", "1") != "1" for c in cells
                ):
                    notes.append("部分技术参数未收录，请以官方来源为准。")
                    continue
                parameters.append(
                    {
                        "name": cells[0].get_text("\n", strip=True),
                        "value": cells[column].get_text("\n", strip=True),
                    }
                )
        if parameters:
            groups.append(
                {
                    "name": heading.get_text(" ", strip=True) if heading else "技术参数",
                    "parameters": parameters,
                }
            )
    return groups, sorted(set(notes))


def build_catalog(root: Path, output: Path | None = None, *, definitions=None, dragonfish=True):
    root = root.resolve()
    output = output or root / "机型参数目录"
    with (root / "技术参数索引.csv").open(encoding="utf-8-sig") as file:
        originals = {r["slug"]: r for r in csv.DictReader(file)}
    state = json.loads((root / "采集状态.json").read_text())
    records, excluded, models = [], [], []
    used = set()

    def emit(slug, name, page, groups, notes, category, **provenance):
        if not groups:
            raise ValueError(f"No usable parameters for {slug}")
        fragments = ["<!doctype html><meta charset='utf-8'>", f"<h1>{html.escape(name)}</h1>"]
        text = [name, page["source_url"]]
        for group in groups:
            fragments.append(f"<h2>{html.escape(group['name'])}</h2>")
            text.append(group["name"])
            for parameter in group["parameters"]:
                value = html.escape(parameter["value"]).replace("\n", "<br>")
                fragments.append(
                    f"<div><strong>{html.escape(parameter['name'])}</strong>"
                    f"<p>{value}</p></div>"
                )
                text.append(parameter["name"] + "\n" + parameter["value"])
        data = "\n".join(fragments).encode()
        record = {
            "slug": slug,
            "name": name,
            "manufacturer": "Autel",
            "category": category,
            "status": "partial" if notes else "saved",
            "source_url": page["source_url"],
            "collected_at": page["collected_at"],
            "group_count": len(groups),
            "parameter_count": sum(len(g["parameters"]) for g in groups),
            "html_path": f"{slug}/技术参数.html",
            "text_path": f"{slug}/技术参数.txt",
            "json_path": f"{slug}/技术参数.json",
            "sha256": digest(data),
            "source_html_path": page["source_html_path"],
            "source_sha256": page["source_sha256"],
            "scope_kind": "aircraft_or_family",
            "error": "部分技术参数未收录，请以官方来源为准。" if notes else "",
        }
        atomic_bytes(output / record["html_path"], data)
        atomic_bytes(output / record["text_path"], "\n\n".join(text).encode())
        model = {**record, "groups": groups, "extraction_notes": notes, **provenance}
        write_json(output / record["json_path"], model)
        records.append(record)
        models.append(model)

    for slug, name, source_slug, variant, category in (
        MODELS if definitions is None else definitions
    ):
        row = originals[source_slug]
        source = local_path(root, row["source_html_path"]).read_bytes()
        if digest(source) != row["source_sha256"]:
            raise ValueError("Source snapshot hash mismatch: " + source_slug)
        soup = BeautifulSoup(source, "html.parser")
        if variant:
            containers = soup.select(f'.insrecruit-list[data-type="{variant}"]')
            if not containers:
                raise ValueError(f"Missing official {variant} table in {source_slug}")
            source = ("<html>" + "".join(map(str, containers)) + "</html>").encode()
        groups, notes = extract_specifications(source.decode("utf-8"))
        emit(
            slug,
            name,
            row,
            groups,
            notes,
            category,
            source_slug=source_slug,
            source_variant=variant,
        )
        used.add(source_slug)

    if dragonfish:
        page = next(
            r
            for r in state["pages"].values()
            if r["slug"] == "dragonfish-drones-pro" and r["status"] in SUCCESS
        )
        source = local_path(root, page["local_path"]).read_bytes()
        if digest(source) != page["sha256"]:
            raise ValueError("Dragonfish source snapshot hash mismatch")
        provenance = {
            "source_url": page["url"],
            "collected_at": page["collected_at"],
            "source_html_path": page["local_path"],
            "source_sha256": page["sha256"],
        }
        for slug, name, column in [
            ("dragonfish-standard", "Dragonfish Standard 道通龙鱼", "龙鱼standard"),
            ("dragonfish-pro", "Dragonfish Pro 道通龙鱼", "龙鱼Pro"),
        ]:
            groups, notes = project_column(source.decode(), column)
            emit(slug, name, provenance, groups, notes, "行业应用", source_column=column)

    for slug, row in originals.items():
        if slug not in used:
            excluded.append(
                {
                    "source_slug": slug,
                    "name": row["name"],
                    "reason": "配件或软件"
                    if row["scope_kind"] != "aircraft_or_family"
                    else "同型号的其他站点或历史路由，原始资料保留",
                }
            )
    write_csv(output / "技术参数索引.csv", records, SPEC_FIELDS)
    write_json(output / "机型参数目录.json", models)
    report = {
        "generated_at": now(),
        "model_count": len(records),
        "parameter_count": sum(r["parameter_count"] for r in records),
        "partial_count": sum(r["status"] == "partial" for r in records),
        "excluded_entries": excluded,
        "source_root": str(root),
        "model_slugs": [r["slug"] for r in records],
    }
    write_json(output / "汇总.json", report)
    return report
