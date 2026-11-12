"""Read-only corpus audit; exit 2 until every image and index layer is complete."""

from __future__ import annotations

import argparse
import json
import math
from datetime import UTC, datetime
from pathlib import Path

import httpx

from .bundle import load_bundle
from .common import ROOT
from .media import atomic_json
from .media_enrichment import OBSERVATION_VERSION, Observation, catalog, definitions


def audit(root: Path, bundle: dict) -> dict:
    from app.core.config import get_settings
    from sqlalchemy import select
    from sqlalchemy.orm import Session

    from .database import ContentChunk, ContentItem, configured_engine

    settings = get_settings()
    model = settings.document_graph_vlm_model or settings.vlm_model
    errors = []

    def check(condition, stage, identity):
        if not condition:
            errors.append({"stage": stage, "identity": str(identity)})
        return condition

    entries = catalog(root, bundle)
    refs = bundle["media_refs"]
    mapped = [
        r for r in refs if entries.get(r["source_guid"], {}).get("source_url") == r["source_url"]
    ]
    assets = {a["sha256"]: a for a in entries.values()}
    observations = 0
    for sha in assets:
        path = root / "observations" / f"{sha}.json"
        if not path.exists():
            continue
        try:
            row = json.loads(path.read_text())
            Observation.model_validate(row)
            observations += int(
                check(
                    row.get("status") == "success"
                    and row.get("sha256") == sha
                    and row.get("version") == OBSERVATION_VERSION
                    and row.get("model") == model
                    and row.get("generated") is True,
                    "observation_provenance",
                    sha,
                )
            )
        except (ValueError, TypeError):
            check(False, "observation_schema", sha)

    engine, _ = configured_engine(None, with_index=False)
    try:
        with Session(engine) as session:
            items = list(
                session.scalars(
                    select(ContentItem).where(ContentItem.dedup_key.in_(bundle["guides_by_id"]))
                )
            )
            item_ids = {item.id: item for item in items}
            chunks = list(
                session.scalars(
                    select(ContentChunk).where(ContentChunk.content_item_id.in_(item_ids))
                )
            )
            synced = 0
            source_refs = {r["media_ref_id"]: r for r in mapped}
            for item in items:
                for ref in item.metadata_json.get("ifixit", {}).get("media_refs", []):
                    source = source_refs.get(ref["media_ref_id"])
                    if (
                        source
                        and source["document_id"] == item.dedup_key
                        and ref.get("source_guid") == source["source_guid"]
                        and ref.get("source_url") == source["source_url"]
                    ):
                        asset = entries[source["source_guid"]]
                        synced += int(
                            ref.get("asset_sha256") == asset["sha256"]
                            and ref.get("mapping_status") == "confirmed"
                        )
            expected_base = {d["logical_chunk_key"]: d for d in bundle["chunks"]}
            expected_visual = {d["key"]: d for d in definitions(root, bundle)}
            actual_base, actual_visual, ready = set(), set(), []
            for chunk in chunks:
                metadata = chunk.chunk_metadata_json
                key = metadata.get("logical_chunk_key")
                owner = metadata.get("chunk_owner")
                if owner == "ifixit_rag":
                    actual_base.add(key)
                    definition = expected_base.get(key)
                    check(
                        definition
                        and metadata.get("chunk_hash") == definition["chunk_hash"]
                        and chunk.display_text == definition["display_text"]
                        and chunk.embed_text == definition["embed_text"]
                        and item_ids[chunk.content_item_id].dedup_key == definition["document_id"],
                        "base_content",
                        key,
                    )
                elif owner == "ifixit_media":
                    actual_visual.add(key)
                    definition = expected_visual.get(key)
                    check(
                        definition
                        and metadata.get("media_hash") == definition["hash"]
                        and chunk.display_text == definition["body"]
                        and chunk.embed_text == definition["embed"]
                        and item_ids[chunk.content_item_id].dedup_key == definition["document_id"],
                        "visual_content",
                        key,
                    )
                else:
                    check(False, "unexpected_owner", chunk.id)
                    continue
                ok = check(
                    chunk.embed_status == "success"
                    and metadata.get("embedding_model") == settings.embedding_model
                    and metadata.get("embedding_dimension") == settings.embedding_dimension
                    and metadata.get("vector_collection") == settings.qdrant_collection,
                    "embedding_metadata",
                    key,
                )
                if ok:
                    ready.append(
                        (
                            str(chunk.id),
                            str(chunk.content_item_id),
                            owner,
                            metadata.get("image_sha256"),
                        )
                    )
    finally:
        engine.dispose()

    verified = set()
    with httpx.Client(base_url=settings.qdrant_url, timeout=60) as client:
        for start in range(0, len(ready), 128):
            batch = {row[0]: row for row in ready[start : start + 128]}
            response = client.post(
                f"/collections/{settings.qdrant_collection}/points",
                json={
                    "ids": list(batch),
                    "with_payload": True,
                    "with_vector": True,
                },
            )
            response.raise_for_status()
            for point in response.json()["result"]:
                point_id = point["id"]
                _, item_id, owner, sha = batch[point_id]
                vector, payload = point["vector"], point["payload"]
                ok = check(
                    isinstance(vector, list)
                    and len(vector) == settings.embedding_dimension
                    and all(math.isfinite(v) for v in vector)
                    and any(vector),
                    "vector_values",
                    point_id,
                )
                ok &= check(
                    payload.get("chunk_id") == point_id
                    and payload.get("content_item_id") == item_id,
                    "vector_payload",
                    point_id,
                )
                if owner == "ifixit_media":
                    ok &= check(
                        payload.get("image_sha256") == sha
                        and payload.get("chunk_type") == "image_observation",
                        "vector_image",
                        point_id,
                    )
                if ok:
                    verified.add(point_id)
            check(
                {p["id"] for p in response.json()["result"]} == set(batch),
                "missing_vector_points",
                start,
            )

    coverage = {
        "guide_items": [len(items), len(bundle["guides"])],
        "mapped_refs": [len(mapped), len(refs)],
        "synced_refs": [synced, len(refs)],
        "successful_observations": [observations, len(assets)],
        "base_chunks": [len(actual_base & expected_base.keys()), len(expected_base)],
        "visual_chunks": [len(actual_visual & expected_visual.keys()), len(expected_visual)],
        "verified_vector_points": [len(verified), len(expected_base) + len(expected_visual)],
    }
    check(actual_base <= expected_base.keys(), "unexpected_base_keys", "corpus")
    check(actual_visual <= expected_visual.keys(), "unexpected_visual_keys", "corpus")
    report = {
        "complete": not errors and all(found == expected for found, expected in coverage.values()),
        "coverage_found_expected": coverage,
        "vision_model": model,
        "embedding_model": settings.embedding_model,
        "embedding_provider": settings.embedding_provider,
        "embedding_dimension": settings.embedding_dimension,
        "error_count": len(errors),
        "errors": errors,
        "checked_at": datetime.now(UTC).isoformat(),
        "read_only": True,
    }
    atomic_json(root / "audit-report.json", report)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bundle", type=Path, required=True)
    parser.add_argument("--media-dir", type=Path, default=ROOT / "data/ifixit-media")
    args = parser.parse_args()
    result = audit(args.media_dir, load_bundle(args.bundle))
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result["complete"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
