"""Explicit footnote bindings and comparable, source-preserving parameter claims."""

import re
from collections import defaultdict
from pathlib import Path

from .common import read_jsonl, uid, write_jsonl

LABELS = {
    "最长飞行时间": "max_flight_time",
    "最长悬停时间": "max_hover_time",
    "起飞重量": "takeoff_weight",
}


def parameter_key(name: str) -> str | None:
    return next((key for label, key in LABELS.items() if name.startswith(label)), None)


def bind_footnotes(parameters: list[dict]) -> list[dict]:
    notes = []
    for i, parameter in enumerate(parameters):
        parameter["footnotes"] = []
        for j, text in enumerate(parameter.get("local_notes", [])):
            marker = re.match(r"^\s*([*†‡]+|\[\d+\])", text)
            notes.append(
                {
                    "text": text,
                    "marker": marker[1] if marker else None,
                    "owner_index": i,
                    "note_index": j,
                    "section": parameter["section"],
                }
            )
    for i, parameter in enumerate(parameters):
        main = parameter["main_value"]
        for note in notes:
            own = note["owner_index"] == i
            shared = (
                note["marker"] is not None
                and note["marker"] in main
                and note["section"] == parameter["section"]
                and 0 <= note["owner_index"] - i <= 3
            )
            if own or shared:
                parameter["footnotes"].append(
                    {**note, "binding": "local_sup" if own else "shared_marker_same_section"}
                )
    return parameters


def native_parameter(text: str) -> dict | None:
    for label in LABELS:
        match = re.fullmatch(re.escape(label) + r"\s*\n\s*(.+)", text.strip(), re.S)
        if match and re.search(r"\d", match[1]):
            return {
                "parameter_name": label,
                "parameter_value": match[1].strip(),
                "parameter_key": LABELS[label],
                "origin": "native_pdf_text",
            }
    return None


def _value(key, text):
    if key in {"max_flight_time", "max_hover_time"}:
        m = re.search(r"(\d+(?:\.\d+)?)\s*(?:分钟|min)", text)
        return {"number": float(m[1]), "unit": "min"} if m else None
    if key == "takeoff_weight":
        m = re.search(r"(\d+(?:\.\d+)?)\s*(kg|千克|g|克)", text)
        if m:
            return {"number": float(m[1]) * (1000 if m[2] in {"kg", "千克"} else 1), "unit": "g"}
    return None


def parameter_values(key: str, text: str) -> list[dict]:
    """Keep multiple configurations explicit; never treat the first value as universal."""
    if key in {"max_flight_time", "max_hover_time"}:
        values = [
            {"number": float(m[1]), "unit": "min"}
            for m in re.finditer(r"(\d+(?:\.\d+)?)\s*(?:分钟|min)", text)
        ]
    elif key == "takeoff_weight":
        values = [
            {"number": float(m[1]) * (1000 if m[2] in {"kg", "千克"} else 1), "unit": "g"}
            for m in re.finditer(r"(\d+(?:\.\d+)?)\s*(kg|千克|g|克)", text)
        ]
    else:
        values = []
    return list({(v["number"], v["unit"]): v for v in values}.values())


def comparison_eligible(key: str, text: str) -> bool:
    return len(parameter_values(key, text)) == 1 and not re.search(
        r"低于|小于|大于|不超过|不高于|至少|≤|≥|<|>|约", text
    )


def annotate_parameter_claims(root: Path) -> None:
    elements = read_jsonl(root / "elements.jsonl")
    documents = {d["id"]: d for d in read_jsonl(root / "documents.jsonl")}
    claims = []
    for e in elements:
        if e["element_type"] != "parameter":
            continue
        meta = e["metadata_json"]
        key = parameter_key(meta["parameter_name"])
        value = _value(key, meta["parameter_value"])
        if key is None or value is None:
            continue
        doc = documents[e["document_version_id"]]
        notes = meta.get("footnotes", [])
        context = meta["parameter_value"] + "\n" + "\n".join(n["text"] for n in notes)
        speed = re.search(r"(\d+(?:\.\d+)?)\s*km/h", context, re.I)
        conditions = {"original_text": context, "wind": "无风" if "无风" in context else None}
        if speed and key == "max_flight_time":
            conditions["test_speed_kmh"] = float(speed[1])
        claim = {
            "id": uid(e["id"], "claim"),
            "element_id": e["id"],
            "processing_id": e["processing_id"],
            "parameter_key": key,
            "value": value,
            "value_variants": parameter_values(key, meta["parameter_value"]),
            "comparison_eligible": bool(comparison_eligible(key, meta["parameter_value"])),
            "conditions": conditions,
            "source": {
                "document_id": doc["id"],
                "title": doc["title"],
                "official_version": doc["official_version"],
                "source_url": doc["source_url"],
                "published_at": doc["published_at"],
                "collected_at": doc["metadata_json"].get("collected_at"),
                "source_pages": sorted({r["source_page_no"] for r in e["regions"]}),
                "footnote_ids": meta.get("footnote_ids", []),
            },
        }
        claims.append(claim)
        meta.update({"parameter_key": key, "claim": claim, "source_differences": []})
    groups = defaultdict(list)
    for claim in claims:
        groups[claim["parameter_key"]].append(claim)
    differences = []
    for key, candidates in groups.items():
        for i, left in enumerate(candidates):
            for right in candidates[i + 1 :]:
                if left["source"]["document_id"] == right["source"]["document_id"]:
                    continue
                for dimension in ["value", "test_speed_kmh"]:
                    a = left["value"] if dimension == "value" else left["conditions"].get(dimension)
                    b = (
                        right["value"]
                        if dimension == "value"
                        else right["conditions"].get(dimension)
                    )
                    if a is None or b is None or a == b:
                        continue
                    differences.append(
                        {
                            "id": uid("parameter-difference", left["id"], right["id"], dimension),
                            "parameter_key": key,
                            "dimension": dimension,
                            "status": "unresolved",
                            "claims": [left, right],
                            "note": "来源记载存在差异，保留各自版本与测试条件，尚未核定统一值。",
                        }
                    )
    for e in elements:
        claim = e["metadata_json"].get("claim")
        if claim:
            e["metadata_json"]["source_differences"] = [
                d for d in differences if any(c["id"] == claim["id"] for c in d["claims"])
            ]
    write_jsonl(root / "elements.jsonl", elements)
    write_jsonl(root / "parameter_claims.jsonl", claims)
    write_jsonl(root / "source_differences.jsonl", differences)
