from __future__ import annotations

import json
import re
from collections import Counter, defaultdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from . import VERSION
from .chunking import Budget, build_chunks
from .cleaning import clean_text, device_key, retrieval_terms
from .common import digest, path_label, write_json, write_jsonl

FILES = (
    "guides.jsonl",
    "steps.jsonl",
    "assets.jsonl",
    "media_refs.jsonl",
    "relations.jsonl",
    "chunks.jsonl",
    "raw_guides.jsonl",
    "quality_report.json",
)
SEMANTICS = {
    "icon_caution": "warning",
    "icon_warning": "warning",
    "icon_note": "note",
    "icon_reminder": "reminder",
}


def parse_markdown(text: str, directory: str) -> dict[str, Any]:
    match = re.search(r"^```json\s*\n(.*?)\n```\s*$", text, re.M | re.S)
    if not match:
        raise ValueError("missing fenced JSON payload")
    raw = json.loads(match.group(1))
    if not isinstance(raw, dict):
        raise ValueError("guide payload must be an object")
    if type(raw.get("guideid")) is not int or raw["guideid"] < 1:
        raise ValueError("guideid must be a positive integer")
    prefix = directory.split("_", 1)[0]
    if prefix != str(raw["guideid"]):
        raise ValueError("directory ID differs from guideid")
    for key in ("title", "category"):
        if not isinstance(raw.get(key), str) or not raw[key].strip():
            raise ValueError(f"{key} must be a non-empty string")
    locale = raw.get("locale", raw.get("langid", "en"))
    if not isinstance(locale, str) or not re.fullmatch(r"[a-zA-Z0-9_-]{1,16}", locale):
        raise ValueError("invalid locale")
    for key in ("steps", "tools", "parts", "prerequisites"):
        if not isinstance(raw.get(key, []), list):
            raise ValueError(f"{key} must be an array")
    for key in ("introduction_raw", "conclusion_raw", "subject", "summary"):
        if raw.get(key) is not None and not isinstance(raw[key], str):
            raise ValueError(f"{key} must be a string or null")
    for step in raw.get("steps", []):
        if not isinstance(step, dict) or not isinstance(step.get("lines"), list):
            raise ValueError("steps must contain objects with lines arrays")
        for line in step["lines"]:
            if not isinstance(line, dict) or not isinstance(line.get("text_raw"), str):
                raise ValueError("each step line must contain text_raw")
        if step.get("title") is not None and not isinstance(step["title"], str):
            raise ValueError("step title must be a string or null")
        if not isinstance(step.get("media") or {}, dict):
            raise ValueError("step media must be an object or null")
    for prereq in raw.get("prerequisites", []):
        if not isinstance(prereq, dict) or type(prereq.get("guideid")) is not int:
            raise ValueError("each prerequisite must contain an integer guideid")
    if raw.get("image") is not None and not isinstance(raw["image"], dict):
        raise ValueError("cover image must be an object or null")
    return raw


def _add_media(
    refs: list[dict[str, Any]],
    document_id: str,
    *,
    role: str,
    media: dict[str, Any],
    order: int,
    step_id: str | None = None,
) -> str:
    key = f"{document_id}:{step_id or role}:media:{order}"
    refs.append(
        {
            "media_ref_id": key,
            "document_id": document_id,
            "step_occurrence_id": step_id,
            "role": role,
            "media_order": order,
            "source_media_id": media.get("id"),
            "source_guid": media.get("guid"),
            "source_url": media.get("original") or media.get("standard") or media.get("url"),
            "asset_sha256": None,
            "mapping_status": "unresolved",
            "mapping_evidence": None,
        }
    )
    return key


def normalize_guide(
    path: Path,
    raw: dict[str, Any],
) -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]]]:
    guide_id = raw["guideid"]
    locale = raw.get("locale", raw.get("langid", "en"))
    document_id = f"ifixit:{guide_id}:{locale}"
    flags: set[str] = set()
    inline_refs = []

    def clean(value: str | None, field: str) -> str:
        text, refs, warnings = clean_text(value)
        inline_refs.extend({**ref, "field": field} for ref in refs)
        flags.update(warnings)
        return text

    introduction = clean(raw.get("introduction_raw"), "introduction_raw")
    conclusion = clean(raw.get("conclusion_raw"), "conclusion_raw")
    title = clean(raw["title"], "title")
    steps = []
    refs: list[dict[str, Any]] = []
    if raw.get("image"):
        _add_media(refs, document_id, role="cover", media=raw["image"], order=0)
    source_steps = raw.get("steps", [])
    # The API array already expands prerequisites in execution order. `orderby`
    # restarts within each source guide, so sorting it globally interleaves battery,
    # shell and repair operations. Preserve the array and retain local order separately.
    last_orders: dict[int, int] = {}
    for step in source_steps:
        order = step.get("orderby")
        origin = step.get("guideid", guide_id)
        if type(order) is not int or order < 1:
            flags.add("invalid_step_order")
        elif order <= last_orders.get(origin, 0):
            flags.add("invalid_step_order")
        if type(order) is int:
            last_orders[origin] = order
    seen: Counter = Counter()
    for original_index, source in enumerate(source_steps):
        position = original_index + 1
        original_id = source.get("stepid")
        if type(original_id) is not int:
            flags.add("missing_step_id")
            base = f"fallback-order:{position}"
        else:
            base = f"step:{original_id}"
        seen[base] += 1
        occurrence = f"{document_id}:{base}:occurrence:{seen[base]}"
        if seen[base] > 1:
            flags.add("duplicate_step_id")
        lines = []
        for line_index, line in enumerate(source["lines"]):
            text = clean(line["text_raw"], f"steps[{original_index}].lines[{line_index}]")
            bullet = line.get("bullet", "black")
            lines.append(
                {
                    "text": text,
                    "text_raw": line["text_raw"],
                    "bullet": bullet,
                    "level": line.get("level", 0),
                    "semantic": SEMANTICS.get(bullet, "instruction"),
                    "line_index": line_index,
                }
            )
        media_ids = []
        media = source.get("media") or {}
        if media.get("type") == "image":
            for number, image in enumerate(media.get("data", [])):
                media_ids.append(
                    _add_media(
                        refs,
                        document_id,
                        role="step_image",
                        media=image,
                        order=number,
                        step_id=occurrence,
                    )
                )
        elif media.get("type") == "video":
            data = media.get("data") or {}
            inline_refs.append(
                {
                    "type": "video",
                    "field": f"steps[{original_index}].media",
                    "step_occurrence_id": occurrence,
                    "data": data,
                    "url": next(
                        (e.get("url") for e in data.get("encodings", []) if e.get("url")), None
                    ),
                }
            )
        elif media:
            flags.add("unknown_media_type")
        step_title = clean(source.get("title"), f"steps[{original_index}].title")
        display = "\n".join(
            (f"[{line['semantic']}] " if line["semantic"] != "instruction" else "") + line["text"]
            for line in lines
            if line["text"]
        )
        steps.append(
            {
                "step_occurrence_id": occurrence,
                "document_id": document_id,
                "source_step_id": original_id,
                "source_guide_id": source.get("guideid"),
                "source_revision": source.get("revisionid"),
                "step_order": position,
                "source_order": source.get("orderby"),
                "source_array_index": original_index,
                "title": step_title,
                "lines": lines,
                "media_ref_ids": media_ids,
                "display_text": display,
                "text_hash": digest(display.encode("utf-8")),
            }
        )
    if raw.get("intro_video_url") or raw.get("intro_video"):
        inline_refs.append(
            {
                "type": "video",
                "field": "intro_video",
                "url": raw.get("intro_video_url"),
                "data": raw.get("intro_video"),
            }
        )
    has_video = any(ref["type"] == "video" for ref in inline_refs)
    if not steps:
        flags.add("no_steps")
        content_class = "video_reference" if has_video else "intro_only"
        if not introduction:
            content_class = "insufficient_content"
        if has_video:
            flags.add("video_reference_only")
    else:
        content_class = "structured_steps"
    if not introduction:
        flags.add("missing_introduction")
    terms = retrieval_terms(title + " " + (raw.get("subject") or ""))
    guide = {
        "document_id": document_id,
        "guide_id": guide_id,
        "locale": locale,
        "title": title,
        "category_raw": raw["category"],
        "device_key": device_key(raw["category"]),
        "subject_raw": raw.get("subject") or "",
        "guide_type": raw.get("type"),
        "introduction": introduction,
        "conclusion": conclusion,
        "tools": raw.get("tools", []),
        "parts": raw.get("parts", []),
        "difficulty": raw.get("difficulty"),
        "time_required": raw.get("time_required"),
        "time_required_min": raw.get("time_required_min"),
        "time_required_max": raw.get("time_required_max"),
        "source_url": raw.get("url") or f"https://www.ifixit.com/Guide/{guide_id}",
        "source_path": path_label(path),
        "source_revision": raw.get("revisionid"),
        "published_date": raw.get("published_date"),
        "modified_date": raw.get("modified_date"),
        "content_class": content_class,
        "has_video_reference": has_video,
        "inline_refs": inline_refs,
        "quality_flags": sorted(flags),
        "retrieval_terms": terms,
        "tags": ["ifixit", f"device:{device_key(raw['category'])}", *terms],
        "step_count": len(steps),
        "step_occurrence_ids": [s["step_occurrence_id"] for s in steps],
        "versions": {
            "parser": VERSION,
            "normalizer": VERSION,
            "alias": VERSION,
            "chunker": VERSION,
        },
    }
    return guide, steps, refs


def _sections(guide: dict[str, Any], steps: list[dict[str, Any]]) -> None:
    sections: list[dict[str, Any]] = []

    def add(key: str, kind: str, value: str, **extra: Any) -> None:
        if value.strip():
            sections.append({"key": key, "chunk_type": kind, "text": value, **extra})

    # An empty video-only document must not become answerable through its title alone.
    if guide["content_class"] != "insufficient_content":
        if steps:
            add(
                "overview",
                "guide_overview",
                "\n".join(
                    [
                        f"Title: {guide['title']}",
                        f"Device: {guide['category_raw']}",
                        f"Subject: {guide['subject_raw']}",
                        f"Difficulty: {guide['difficulty']}",
                        f"Time: {guide['time_required']}",
                    ]
                ),
            )
        add("introduction", "guide_overview" if steps else "guide_intro", guide["introduction"])
        for name in ("tools", "parts"):
            values = []
            for entry in guide[name]:
                if isinstance(entry, dict):
                    text, _, _ = clean_text(entry.get("text") or entry.get("name") or "")
                    values.append(
                        f"{text}; quantity={entry.get('quantity', 'unspecified')}; "
                        f"notes={entry.get('notes') or ''}"
                    )
                else:
                    values.append(str(entry))
            if values:
                add(name, "guide_overview", name.title() + ":\n" + "\n".join(values))
        if guide.get("prerequisites"):
            add(
                "prerequisites",
                "guide_overview",
                "Prerequisites:\n"
                + "\n".join(
                    f"{r['target_document_id']} ({r['target_title']}); "
                    f"resolution={r['resolution']}; inclusion={r['inclusion']}"
                    for r in guide["prerequisites"]
                ),
            )
    for step in steps:
        body = f"Step {step['step_order']}" + (f": {step['title']}" if step["title"] else "")
        if step["display_text"]:
            body += "\n" + step["display_text"]
        else:
            guide["quality_flags"] = sorted(set(guide["quality_flags"]) | {"empty_step_text"})
            continue
        add(
            step["step_occurrence_id"],
            "repair_step",
            body,
            step_order=step["step_order"],
            step_occurrence_ids=[step["step_occurrence_id"]],
            media_ref_ids=step["media_ref_ids"],
            has_safety_notes=any(line["semantic"] != "instruction" for line in step["lines"]),
        )
    if steps:
        add("conclusion", "guide_overview", guide["conclusion"])
    cursor = 0
    for section in sections:
        section["char_start"] = cursor
        cursor += len(section["text"])
        section["char_end"] = cursor
        cursor += 2
    guide["sections"] = sections
    guide["cleaned_text"] = "\n\n".join(s["text"] for s in sections)


def _scan_assets(
    path: Path,
    document_id: str,
    assets: dict[str, dict[str, Any]],
) -> tuple[list[str], list[str]]:
    from PIL import Image

    paths = []
    errors = []
    for image_path in sorted(path.parent.iterdir()):
        if image_path.suffix.lower() not in {".jpg", ".jpeg", ".png", ".webp"}:
            continue
        label = path_label(image_path)
        paths.append(label)
        data = image_path.read_bytes()
        sha = digest(data)
        if sha not in assets:
            asset = {
                "sha256": sha,
                "paths": [],
                "document_ids": [],
                "byte_size": len(data),
                "width": None,
                "height": None,
                "format": None,
                "decode_status": "success",
            }
            try:
                with Image.open(image_path) as image:
                    image.load()
                    asset.update(width=image.width, height=image.height, format=image.format)
            except Exception as exc:
                asset.update(decode_status="failed", decode_error=type(exc).__name__)
            assets[sha] = asset
        asset = assets[sha]
        asset["paths"].append(label)
        if document_id not in asset["document_ids"]:
            asset["document_ids"].append(document_id)
        if asset["decode_status"] != "success":
            errors.append(label)
    return paths, errors


def prepare(
    input_dir: Path,
    output_dir: Path,
    budget: Budget,
    media_map: Path | None = None,
) -> dict[str, Any]:
    input_dir = input_dir.resolve()
    output_dir = output_dir.resolve()
    if not input_dir.is_dir():
        raise ValueError(f"input directory does not exist: {input_dir}")
    if output_dir == input_dir or input_dir in output_dir.parents:
        raise ValueError("output directory must not be inside the raw input directory")
    if output_dir.exists():
        raise ValueError("output directory already exists; use a new run directory")
    files = sorted(input_dir.glob("*/原始JSON.md"))
    if not files:
        raise ValueError("no */原始JSON.md files found")
    mappings = json.loads(media_map.read_text(encoding="utf-8")) if media_map else []
    if not isinstance(mappings, list):
        raise ValueError("media map must be an array")
    for mapping in mappings:
        if not isinstance(mapping, dict) or not all(
            mapping.get(key)
            for key in (
                "document_id",
                "source_guid",
                "local_path",
                "evidence",
            )
        ):
            raise ValueError(
                "each media mapping requires document_id, source_guid, " "local_path and evidence"
            )
    guides = []
    steps = []
    refs = []
    raws = []
    assets: dict[str, dict[str, Any]] = {}
    errors = []
    inputs = []
    seen_documents: set[str] = set()
    raw_by_id = {}
    for path in files:
        try:
            data = path.read_bytes()
            inputs.append({"path": path_label(path), "sha256": digest(data)})
            markdown = data.decode("utf-8")
            raw = parse_markdown(markdown, path.parent.name)
            guide, new_steps, new_refs = normalize_guide(path, raw)
            if guide["document_id"] in seen_documents:
                raise ValueError(f"duplicate document_id: {guide['document_id']}")
            seen_documents.add(guide["document_id"])
            local_paths, failed_images = _scan_assets(path, guide["document_id"], assets)
            guide["local_asset_paths"] = local_paths
            if len(local_paths) != len(new_refs):
                guide["quality_flags"].append("image_count_mismatch")
            if failed_images:
                guide["quality_flags"].append("image_decode_failed")
            guide["image_counts"] = {"references": len(new_refs), "local_files": len(local_paths)}
            guides.append(guide)
            steps.extend(new_steps)
            refs.extend(new_refs)
            raws.append(
                {
                    "document_id": guide["document_id"],
                    "payload": raw,
                    "markdown": markdown,
                    "source_sha256": digest(data),
                }
            )
            raw_by_id[guide["document_id"]] = raw
        except Exception as exc:
            errors.append(
                {
                    "source_path": path_label(path),
                    "error": str(exc),
                    "error_type": type(exc).__name__,
                }
            )
    guide_by_id = {g["document_id"]: g for g in guides}
    steps_by_id: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for step in steps:
        steps_by_id[step["document_id"]].append(step)
    assets_by_path = {path: a for a in assets.values() for path in a["paths"]}
    used_mappings = set()
    for ref in refs:
        candidates = [
            (index, mapping)
            for index, mapping in enumerate(mappings)
            if mapping["document_id"] == ref["document_id"]
            and mapping["source_guid"] == ref["source_guid"]
        ]
        if len(candidates) > 1:
            raise ValueError(f"ambiguous media map for {ref['media_ref_id']}")
        if candidates:
            index, mapping = candidates[0]
            label = mapping["local_path"]
            asset = assets_by_path.get(label)
            if asset is None or label not in guide_by_id[ref["document_id"]]["local_asset_paths"]:
                raise ValueError(f"media map path is not a scanned guide asset: {label}")
            if mapping.get("sha256") and mapping["sha256"] != asset["sha256"]:
                raise ValueError(f"media map hash mismatch: {label}")
            if asset["decode_status"] != "success":
                raise ValueError(f"media map points to an invalid image: {label}")
            ref.update(
                asset_sha256=asset["sha256"],
                mapping_status="confirmed",
                mapping_evidence=mapping["evidence"],
            )
            used_mappings.add(index)
        else:
            guide_by_id[ref["document_id"]]["quality_flags"].append("image_mapping_unresolved")
    if len(used_mappings) != len(mappings):
        raise ValueError("media map contains entries which do not match source references")
    relations = []
    for guide in guides:
        own_steps = steps_by_id[guide["document_id"]]
        for prereq in raw_by_id[guide["document_id"]].get("prerequisites", []):
            target_id = f"ifixit:{prereq['guideid']}:{prereq.get('locale', guide['locale'])}"
            target_steps = steps_by_id[target_id]
            target_signatures = {(s["source_step_id"], s["text_hash"]) for s in target_steps}
            own_signatures = {(s["source_step_id"], s["text_hash"]) for s in own_steps}
            matches = len(target_signatures & own_signatures)
            inclusion = "unknown"
            if target_signatures:
                inclusion = (
                    "included"
                    if target_signatures <= own_signatures
                    else "partial"
                    if matches
                    else "not_included"
                )
            relation = {
                "document_id": guide["document_id"],
                "relation_type": "prerequisite",
                "target_document_id": target_id,
                "target_title": prereq.get("title", ""),
                "source_url": prereq.get("url"),
                "resolution": "resolved" if target_id in guide_by_id else "unresolved",
                "inclusion": inclusion,
                "matching_steps": matches,
                "origin_guide_steps": sum(
                    s["source_guide_id"] == prereq["guideid"] for s in own_steps
                ),
            }
            relations.append(relation)
            if relation["resolution"] == "unresolved":
                guide["quality_flags"].append("missing_prerequisite")
        guide["prerequisites"] = [r for r in relations if r["document_id"] == guide["document_id"]]
        guide["media_refs"] = [r for r in refs if r["document_id"] == guide["document_id"]]
        guide["quality_flags"] = sorted(set(guide["quality_flags"]))
        _sections(guide, own_steps)
    chunks = []
    for guide in guides:
        new_chunks = build_chunks(guide, budget)
        if any(not c["metadata"]["indexable"] for c in new_chunks):
            guide["quality_flags"] = sorted(set(guide["quality_flags"]) | {"oversized_chunk"})
            new_chunks = build_chunks(guide, budget)
        guide["content_hash"] = digest(
            {"guide": guide, "steps": steps_by_id[guide["document_id"]], "chunks": new_chunks}
        )
        chunks.extend(new_chunks)
    counts = {
        "input_guides": len(files),
        "guides": len(guides),
        "parse_errors": len(errors),
        "steps": len(steps),
        "lines": sum(len(s["lines"]) for s in steps),
        "image_files": sum(len(a["paths"]) for a in assets.values()),
        "unique_assets": len(assets),
        "media_references": len(refs),
        "confirmed_media_references": sum(r["mapping_status"] == "confirmed" for r in refs),
        "relations": len(relations),
        "chunks": len(chunks),
        "oversized_chunks": sum(not c["metadata"]["indexable"] for c in chunks),
        "no_steps": sum(not g["step_count"] for g in guides),
    }
    report = {
        "counts": counts,
        "errors": errors,
        "quality_flags": dict(Counter(flag for g in guides for flag in g["quality_flags"])),
        "content_classes": dict(Counter(g["content_class"] for g in guides)),
        "guides": [
            {
                "document_id": g["document_id"],
                "source_path": g["source_path"],
                "flags": g["quality_flags"],
                "image_counts": g["image_counts"],
                "content_class": g["content_class"],
            }
            for g in guides
        ],
    }
    output_dir.mkdir(parents=True)
    for name, rows in (
        ("guides", guides),
        ("steps", steps),
        ("assets", list(assets.values())),
        ("media_refs", refs),
        ("relations", relations),
        ("chunks", chunks),
        ("raw_guides", raws),
    ):
        write_jsonl(output_dir / f"{name}.jsonl", rows)
    write_json(output_dir / "quality_report.json", report)
    image_inputs = [
        {"path": path, "sha256": a["sha256"]} for a in assets.values() for path in a["paths"]
    ]
    manifest = {
        "schema_version": 1,
        "tool_version": VERSION,
        "created_at": datetime.now(UTC).isoformat(),
        "input_dir": path_label(input_dir),
        "status": "partial" if errors else "success",
        "counts": counts,
        "config": {
            "max_tokens": budget.max_tokens,
            "max_chars": budget.max_chars,
            "tokenizer": budget.tokenizer_name,
            "token_count_method": budget.method,
            "media_map_sha256": digest(media_map.read_bytes()) if media_map else None,
        },
        "inputs": inputs + image_inputs,
        "outputs": {name: digest((output_dir / name).read_bytes()) for name in FILES},
    }
    write_json(output_dir / "manifest.json", manifest)
    return manifest
