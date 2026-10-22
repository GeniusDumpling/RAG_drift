from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path
from typing import Any

from .common import digest, read_jsonl
from .prepare import FILES, parse_markdown


def load_bundle(directory: Path) -> dict[str, Any]:
    """Validate the complete offline bundle before any database mutation."""
    manifest = json.loads((directory / "manifest.json").read_text(encoding="utf-8"))
    if manifest.get("schema_version") != 1:
        raise ValueError("unsupported bundle schema_version")
    if set(manifest.get("outputs", {})) != set(FILES):
        raise ValueError("manifest output list is incomplete")
    for name in FILES:
        if digest((directory / name).read_bytes()) != manifest["outputs"][name]:
            raise ValueError(f"bundle checksum mismatch: {name}")
    result: dict[str, Any] = {"manifest": manifest, "directory": str(directory.resolve())}
    for name in FILES:
        if name.endswith(".jsonl"):
            result[name[:-6]] = read_jsonl(directory / name)
    guides = {g["document_id"]: g for g in result["guides"]}
    if len(guides) != len(result["guides"]):
        raise ValueError("duplicate guide document_id")
    raws = {r["document_id"]: r for r in result["raw_guides"]}
    if len(raws) != len(result["raw_guides"]) or set(raws) != set(guides):
        raise ValueError("raw payloads must match guides exactly")
    for raw in raws.values():
        if digest(raw["markdown"].encode("utf-8")) != raw["source_sha256"]:
            raise ValueError("raw Markdown checksum mismatch")
        guide = guides[raw["document_id"]]
        if parse_markdown(raw["markdown"], f"{guide['guide_id']}_guide") != raw["payload"]:
            raise ValueError("raw JSON payload does not match its Markdown snapshot")
    by_doc: dict[str, list[dict[str, Any]]] = defaultdict(list)
    seen_keys = set()
    for chunk in result["chunks"]:
        doc = chunk["document_id"]
        if doc not in guides or chunk["logical_chunk_key"] in seen_keys:
            raise ValueError("orphan or duplicate chunk")
        seen_keys.add(chunk["logical_chunk_key"])
        unhashed = {k: v for k, v in chunk.items() if k != "chunk_hash"}
        if digest(unhashed) != chunk["chunk_hash"]:
            raise ValueError("chunk definition hash mismatch")
        if (
            guides[doc]["cleaned_text"][chunk["char_start"] : chunk["char_end"]]
            != chunk["display_text"]
        ):
            raise ValueError("chunk character offsets do not match cleaned_text")
        by_doc[doc].append(chunk)
    for chunks in by_doc.values():
        if [c["chunk_index"] for c in chunks] != list(range(len(chunks))):
            raise ValueError("chunk indices must be contiguous and ordered per guide")
    step_ids = set()
    for step in result["steps"]:
        key = step["step_occurrence_id"]
        if step["document_id"] not in guides or key in step_ids:
            raise ValueError("orphan or duplicate step")
        step_ids.add(key)
    for chunk in result["chunks"]:
        if any(key not in step_ids for key in chunk["step_occurrence_ids"]):
            raise ValueError("chunk references an unknown step")
    expected = manifest["counts"]
    for name in ("guides", "steps", "chunks", "relations"):
        if len(result[name]) != expected[name]:
            raise ValueError(f"bundle count mismatch: {name}")
    result["guides_by_id"] = guides
    result["raws_by_id"] = raws
    result["chunks_by_doc"] = by_doc
    steps_by_doc: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for step in result["steps"]:
        steps_by_doc[step["document_id"]].append(step)
    result["steps_by_doc"] = steps_by_doc
    for guide in result["guides"]:
        document_id = guide["document_id"]
        source_steps = raws[document_id]["payload"].get("steps", [])
        normalized_steps = steps_by_doc[document_id]
        if len(source_steps) != len(normalized_steps) or guide["step_count"] != len(source_steps):
            raise ValueError("normalized step count differs from raw steps")
        for index, (source, step) in enumerate(zip(source_steps, normalized_steps, strict=True)):
            if (
                step["source_array_index"] != index
                or step["step_order"] != index + 1
                or step["source_step_id"] != source.get("stepid")
                or step["source_guide_id"] != source.get("guideid")
                or [line["text_raw"] for line in step["lines"]]
                != [line["text_raw"] for line in source["lines"]]
            ):
                raise ValueError("normalized steps do not preserve source execution order/content")
        expected_hash = digest(
            {
                "guide": {k: v for k, v in guide.items() if k != "content_hash"},
                "steps": steps_by_doc[document_id],
                "chunks": by_doc[document_id],
            }
        )
        if expected_hash != guide["content_hash"]:
            raise ValueError("guide definition hash mismatch")
    return result
