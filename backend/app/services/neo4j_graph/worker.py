"""Neo4j worker; PostgreSQL is read only except for source row locks at activation."""

import uuid
from typing import Any

from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from app.db.base import utcnow
from app.models.content import ContentItem
from app.schemas.video_graph import ChunkExtraction, VideoAssessment
from app.services.neo4j_graph.store import GraphStore, decode
from app.services.video_graph import (
    LeaseLostError,
    VideoExtractor,
    chunk_fingerprint,
    load_material,
    locate_quote,
    update_coverage,
    validate_extraction,
)
from app.services.video_graph_diagnostics import describe_graph_error, graph_error_summary


def enqueue_videos(
    engine, store, *, extractor_version, content_ids=None, retry_failed=False, retry_partial=False
):
    counts = dict(scanned=0, queued=0, unchanged=0, withdrawn=0, no_evidence=0)
    with Session(engine) as session:
        query = select(ContentItem.id).where(ContentItem.item_type == "video_description")
        if content_ids:
            query = query.where(ContentItem.id.in_(content_ids))
        ids = session.scalars(query.order_by(ContentItem.created_at, ContentItem.id)).all()
        for content_id in ids:
            material = load_material(session, content_id)
            if material is None:
                continue
            counts["scanned"] += 1
            if not material.extraction_chunks:
                counts["no_evidence"] += 1
            counts[store.enqueue(material, extractor_version, retry_failed, retry_partial)] += 1
    # PostgreSQL cannot cascade into Neo4j. Reconcile deletions within this scan's scope.
    known = store.read(
        "MATCH (v:VGVideo) WHERE NOT coalesce(v.deleted,false) "
        "AND ($ids IS NULL OR v.id IN $ids) RETURN v.id AS id",
        ids=[str(v) for v in content_ids] if content_ids else None,
    )
    present = {str(v) for v in ids}
    for row in known:
        if row["id"] not in present:
            store.withdraw(row["id"], deleted=True)
    return counts


def process_next(
    engine: Engine,
    store: GraphStore,
    extractor: VideoExtractor,
    *,
    content_ids: list[uuid.UUID] | None = None,
) -> dict[str, Any] | None:
    claimed = store.claim(extractor.version, content_ids)
    if claimed is None:
        return None
    build, token = claimed
    build_id = build["id"]
    content_id = uuid.UUID(build["content_item_id"])
    with Session(engine) as session:
        material = load_material(session, content_id)
    output = decode(build.get("output_json"))
    expected_version = build["extractor_version"]
    stage = "prepare"
    current_chunk = None
    chunk_index = None
    extraction = None
    output.pop("last_error", None)
    try:
        if expected_version != extractor.version:
            raise ValueError("worker 抽取版本与任务版本不一致，请使用 scan 对齐配置")
        if material is None:
            raise LeaseLostError("源视频已删除")
        if "assessment" not in output:
            stage = "assessment"
            assessment = (
                extractor.assess(material)
                if material.extraction_chunks
                else VideoAssessment(relevant=False, reason="没有可用的字幕或摘要切片")
            )
            output["assessment"] = assessment.model_dump()
            stage = "checkpoint"
            store.checkpoint(build_id, content_id, token, output)
        output["chunks"] = dict(output.get("chunks", {}))
        output["chunk_errors"] = dict(output.get("chunk_errors", {}))
        output["chunk_fingerprints"] = {
            str(chunk.id): chunk_fingerprint(chunk) for chunk in material.extraction_chunks
        }
        update_coverage(output, material)

        def skip_chunk(exc: ValueError) -> None:
            assert current_chunk is not None
            key = str(current_chunk.id)
            previous_error = output["chunk_errors"].get(key, {})
            detail = describe_graph_error(
                exc,
                stage=stage,
                chunk=current_chunk,
                chunk_index=chunk_index,
                extraction=extraction,
                locate=locate_quote,
            )
            output["chunk_errors"][key] = {
                **detail,
                "fingerprint": chunk_fingerprint(current_chunk),
                "attempts": previous_error.get("attempts", 0) + 1,
                "last_attempt_at": utcnow().isoformat(),
            }
            update_coverage(output, material)
            store.checkpoint(build_id, content_id, token, output)

        if output["assessment"]["relevant"]:
            chunks = material.extraction_chunks
            for index, chunk in enumerate(chunks):
                key = str(chunk.id)
                if key in output["chunks"]:
                    try:
                        validate_extraction(
                            chunk, ChunkExtraction.model_validate(output["chunks"][key])
                        )
                    except ValueError:
                        del output["chunks"][key]
                    else:
                        output["chunk_errors"].pop(key, None)
                        continue
                current_chunk, chunk_index = chunk, index + 1
                extraction = None
                neighbors = "\n".join(
                    c.text[:500] for c in chunks[max(0, index - 1) : index + 2] if c.id != chunk.id
                )
                stage = "extraction"
                try:
                    extraction = extractor.extract(material, chunk, neighbors)
                except ValidationError as exc:
                    skip_chunk(exc)
                    continue
                stage = "validation"
                try:
                    extracted = validate_extraction(chunk, extraction)
                except ValueError as exc:
                    skip_chunk(exc)
                    continue
                output["chunks"][key] = extracted.model_dump()
                output["chunk_errors"].pop(key, None)
                update_coverage(output, material)
                stage = "checkpoint"
                store.checkpoint(build_id, content_id, token, output)
        stage = "activation"
        current_chunk, chunk_index, extraction = None, None, None
        with Session(engine) as session, session.begin():
            current_material = load_material(session, content_id, lock=True)
            if current_material is None:
                store.withdraw(content_id, deleted=True)
                raise LeaseLostError("源视频已删除")
            result = store.activate(current_material, build_id, token, output)
        return {
            "build_id": str(build_id),
            "content_item_id": str(content_id),
            "status": result,
            "assessment": output["assessment"],
            "chunks": len(output["chunks"]),
            "coverage": output["coverage"],
            "chunk_errors": output["chunk_errors"],
            "projection_activated": output.get("projection_activated", False),
            **(
                {
                    "error": output["last_error"]["error_type"],
                    "error_detail": output["last_error"],
                }
                if "last_error" in output
                else {}
            ),
        }
    except LeaseLostError:
        return {
            "build_id": str(build_id),
            "content_item_id": str(content_id),
            "status": "lease_lost",
        }
    except Exception as exc:
        detail = describe_graph_error(
            exc,
            stage=stage,
            chunk=current_chunk,
            chunk_index=chunk_index,
            extraction=extraction,
            locate=locate_quote,
        )
        try:
            store.fail(build_id, content_id, token, detail, graph_error_summary(detail))
        except LeaseLostError:
            pass
        return {
            "build_id": str(build_id),
            "content_item_id": str(content_id),
            "status": "failed",
            "error": type(exc).__name__,
            "error_detail": detail,
        }
