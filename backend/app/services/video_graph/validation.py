"""Source, semantic and review-receipt validation independent of graph storage."""

from typing import Any

from app.schemas.video_graph import ChunkExtraction
from app.services.video_graph.material import VideoChunk, VideoMaterial, locate_quote
from app.services.video_graph.semantics import (
    RelationSemanticError,
    validate_entity_semantics,
    validate_relation_semantics,
)


def validate_extraction(
    chunk: VideoChunk,
    extraction: ChunkExtraction,
    *,
    material: VideoMaterial | None = None,
    require_review: bool = False,
) -> ChunkExtraction:
    """Reject unsupported outputs rather than silently activating a partial video graph."""
    if require_review or extraction.schema_version == 2:
        from app.services.video_graph.review import validate_review

        if material is None:
            raise ValueError("v2 校验需要视频材料上下文")
        validate_review(chunk, extraction, material)
    keys = {entity.key for entity in extraction.entities}
    if len(keys) != len(extraction.entities):
        raise ValueError("模型返回了重复实体 key")
    for entity in extraction.entities:
        if locate_quote(chunk.text, entity.surface_text) is None:
            raise ValueError("实体提及无法在当前切片原文中定位")
        validate_entity_semantics(entity)
    if chunk.evidence_type != "transcript_segment":
        return extraction.model_copy(update={"relations": []})
    entities = {entity.key: entity for entity in extraction.entities}
    for relation in extraction.relations:
        if relation.subject not in keys or relation.object not in keys:
            raise ValueError("关系引用了不存在的实体")
        if locate_quote(chunk.text, relation.quote) is None:
            raise ValueError("关系证据无法在当前字幕切片中定位")
        if any(len(k) > 80 or len(v) > 500 for k, v in relation.qualifiers.items()):
            raise ValueError("关系限定条件过长")
        validate_relation_semantics(relation, entities[relation.subject], entities[relation.object])
        if relation.predicate in {"used_in", "located_in"}:
            if locate_quote(relation.quote, entities[relation.object].surface_text) is None:
                raise RelationSemanticError("地理关系证据中必须包含宾语的原文名称")
    return extraction


def update_coverage(output: dict[str, Any], material: VideoMaterial) -> dict[str, Any]:
    relevant = bool(output.get("assessment", {}).get("relevant"))
    ids = {str(c.id) for c in material.extraction_chunks} if relevant else set()
    passed = ids & output.get("chunks", {}).keys()
    skipped = (ids & output.get("chunk_errors", {}).keys()) - passed
    coverage = {
        "applicable": relevant,
        "total_chunks": len(ids),
        "passed_chunks": len(passed),
        "skipped_chunks": len(skipped),
        "pending_chunks": len(ids - passed - skipped),
        "coverage_ratio": len(passed) / len(ids) if ids else None,
        "complete": len(passed) == len(ids),
    }
    output["coverage"] = coverage
    reviewed = [r for key, r in output.get("chunks", {}).items() if key in ids and r.get("review")]
    if reviewed:
        output["quality"] = {
            "reviewed_chunks": len(reviewed),
            "candidate_entities": sum(r["review"]["candidate_entities"] for r in reviewed),
            "accepted_entities": sum(len(r["entities"]) for r in reviewed),
            "candidate_relations": sum(r["review"]["candidate_relations"] for r in reviewed),
            "accepted_relations": sum(len(r["relations"]) for r in reviewed),
            "rejected_candidates": sum(len(r["review"]["rejected"]) for r in reviewed),
            **{
                f"rejected_{kind}": sum(
                    e["kind"] == kind for r in reviewed for e in r["review"]["rejected"]
                )
                for kind in ("entity", "relation", "attribute", "identity")
            },
        }
    return coverage
