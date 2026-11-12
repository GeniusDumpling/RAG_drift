"""Serve source-identified iFixit assets and attach only the cited step's images."""

import hashlib
import json
import os
import re
from pathlib import Path

from app.schemas.search import ImageEvidence

_GUID = re.compile(r"^[A-Za-z0-9_-]{8,80}$")
_SHA = re.compile(r"^[a-f0-9]{64}$")
_BRANDS = {"dji", "parrot", "protocol", "yuneec", "gopro", "3dr", "udi", "ehang"}


def device_identity(category: str) -> str:
    parts = re.findall(r"[a-z]+|[0-9]+", category.casefold())
    if parts and parts[0] in _BRANDS:
        parts = parts[1:]
    return "".join(parts)


def query_devices(query: str, fallback_categories: list[str]) -> set[str]:
    try:
        categories = json.loads((media_root() / "devices.json").read_text())["categories"]
    except (OSError, ValueError, KeyError):
        categories = fallback_categories
    matches = []
    for category in categories:
        parts = re.findall(r"[a-z]+|[0-9]+", category.casefold())
        if not parts or len(device_identity(category)) < 5:
            continue
        aliases = [parts]
        if parts[0] in _BRANDS:
            aliases.append(parts[1:])
        for alias in aliases:
            pattern = r"(?<![a-z0-9])" + r"[\s_-]*".join(map(re.escape, alias)) + r"(?![a-z0-9])"
            matches.extend(
                (m.start(), m.end(), device_identity(category))
                for m in re.finditer(pattern, query, re.I)
            )
    # "Mavic 3" is contained in "Mavic 3 Pro"; comparison questions keep both
    # independent spans. A component marking such as H6MN matches no device alias.
    return {
        identity
        for start, end, identity in matches
        if not any(a <= start and b >= end and (b - a) > (end - start) for a, b, _ in matches)
    }


def media_root() -> Path:
    return Path(os.environ.get("IFIXIT_MEDIA_DIR", "data/ifixit-media")).resolve()


def confirmed_asset(guid: str) -> dict | None:
    if not _GUID.fullmatch(guid):
        return None
    root = media_root()
    try:
        entry = json.loads((root / "catalog" / f"{guid}.json").read_text())
        sha = entry["sha256"]
        path = (root / entry["storage_key"]).resolve()
        if (
            entry["guid"] != guid
            or entry.get("status") != "confirmed"
            or not _SHA.fullmatch(sha)
            or not path.is_relative_to(root / "blobs")
            or path.stem != sha
            or not path.is_file()
        ):
            return None
        return entry
    except (OSError, ValueError, KeyError, TypeError):
        return None


def asset_file(sha: str) -> tuple[Path, str] | None:
    if not _SHA.fullmatch(sha):
        return None
    for extension, mime in (("jpg", "image/jpeg"), ("png", "image/png"), ("webp", "image/webp")):
        root = media_root() / "blobs"
        path = (root / f"{sha}.{extension}").resolve()
        if path.is_relative_to(root) and path.is_file():
            if hashlib.sha256(path.read_bytes()).hexdigest() == sha:
                return path, mime
    return None


def images_for_chunk(item, chunk, *, metadata: dict | None = None) -> list[ImageEvidence]:
    if item.item_type != "repair_guide":
        return []
    metadata = metadata if metadata is not None else (item.metadata_json or {})
    guide = metadata.get("ifixit") or {}
    refs = {ref["media_ref_id"]: ref for ref in guide.get("media_refs", [])}
    steps = {step["step_occurrence_id"]: step for step in metadata.get("steps", [])}
    result = []
    seen = set()
    selected_refs = (chunk.chunk_metadata_json or {}).get("media_ref_ids", [])
    if not selected_refs and (chunk.chunk_metadata_json or {}).get("chunk_type") in {
        "guide_intro",
        "guide_overview",
    }:
        selected_refs = [key for key, ref in refs.items() if ref.get("role") == "cover"]
    for key in selected_refs:
        ref = refs.get(key)
        if ref is None:
            continue
        asset = confirmed_asset(ref.get("source_guid") or "")
        if asset is None or asset["source_url"] != ref.get("source_url"):
            continue
        step = steps.get(ref.get("step_occurrence_id")) or {}
        identity = (asset["sha256"], step.get("step_order"))
        if identity in seen:
            continue
        seen.add(identity)
        observation = {}
        try:
            observation = json.loads(
                (media_root() / "observations" / f"{asset['sha256']}.json").read_text()
            )
            if (
                observation.get("sha256") != asset["sha256"]
                or observation.get("status") != "success"
            ):
                observation = {}
        except (OSError, ValueError):
            pass
        result.append(
            ImageEvidence(
                asset_id=asset["sha256"],
                source_guid=asset["guid"],
                url=f"/ifixit-media/assets/{asset['sha256']}",
                source_url=asset["source_url"],
                guide_url=item.canonical_url,
                step_order=step.get("step_order"),
                step_title=step.get("title") or None,
                caption=f"{guide.get('category_raw') or item.title} · "
                + (
                    step.get("title")
                    or (
                        "指南封面"
                        if ref.get("role") == "cover"
                        else f"步骤 {step.get('step_order', '')}"
                    )
                ),
                width=asset["width"],
                height=asset["height"],
                observation=observation.get("description"),
                ocr_text=observation.get("ocr_text", []),
                description_generated=bool(observation),
            )
        )
    return result
