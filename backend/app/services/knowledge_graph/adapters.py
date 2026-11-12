"""Map independently reviewed native outputs into the shared publication contract."""

from app.schemas.video_graph import ChunkExtraction
from app.services.video_graph.identity import entity_identity
from app.services.video_graph.material import locate_quote
from app.services.video_graph.validation import validate_extraction

from .schema import Entity, Evidence, Fact, Publication, normalized


def _merge_entity(entities, entity):
    previous = entities.get(entity.id)
    if previous:
        if (normalized(previous.name), previous.entity_type, previous.scope) != (
            normalized(entity.name),
            entity.entity_type,
            entity.scope,
        ):
            raise ValueError("同一实体 ID 对应不同身份")
        entity = entity.model_copy(
            update={
                "name": previous.name,
                "mentions": previous.mentions + entity.mentions,
            }
        )
    entities[entity.id] = entity


def video_publication(material, build_id, output, version):
    entities, facts = {}, []
    if output.get("assessment", {}).get("relevant"):
        for chunk in material.extraction_chunks:
            raw = output.get("chunks", {}).get(str(chunk.id))
            if raw is None:
                continue
            extraction = validate_extraction(
                chunk, ChunkExtraction.model_validate(raw), material=material, require_review=True
            )
            review_id = extraction.review.result_hash

            def evidence(quote, confidence, chunk=chunk, review_id=review_id):
                offsets = locate_quote(chunk.text, quote)
                if offsets is None:
                    raise ValueError("视频证据不在原始切片中")
                start, end = offsets
                return Evidence(
                    quote=chunk.text[start:end],
                    confidence=confidence,
                    review_id=review_id,
                    locator=dict(
                        type="video",
                        content_item_id=str(material.id),
                        chunk_id=str(chunk.id),
                        char_start=start,
                        char_end=end,
                        start_seconds=chunk.start_seconds,
                        end_seconds=chunk.end_seconds,
                        evidence_type=chunk.evidence_type,
                        caption_source=material.caption_source,
                    ),
                )

            keys = {}
            for entity in extraction.entities:
                identity = entity_identity(entity, material)
                eid = str(identity.id)
                keys[entity.key] = eid
                confidence = (
                    entity.confidence
                    if chunk.evidence_type == "transcript_segment"
                    else min(entity.confidence, 0.5)
                )
                mention = evidence(entity.surface_text, confidence)
                mention = mention.model_copy(
                    update={
                        "locator": {
                            **mention.locator,
                            "surface_text": entity.surface_text,
                            "identity_quote": entity.identity_quote,
                            "identity_source": entity.identity_source,
                        }
                    }
                )
                _merge_entity(
                    entities,
                    Entity(
                        id=eid,
                        name=identity.name,
                        entity_type=entity.entity_type,
                        scope=identity.scope_key,
                        mentions=[mention],
                    ),
                )
                for attribute, value in sorted(entity.attributes.items()):
                    facts.append(
                        Fact(
                            subject=eid,
                            predicate="has_parameter",
                            kind="attribute",
                            attribute=attribute,
                            value=value,
                            evidence=[evidence(chunk.text, confidence)],
                        )
                    )
            for relation in extraction.relations:
                subject, obj = keys[relation.subject], keys[relation.object]
                if subject == obj:
                    continue
                facts.append(
                    Fact(
                        subject=subject,
                        object=obj,
                        predicate=relation.predicate,
                        assertion=relation.assertion,
                        qualifiers=relation.qualifiers,
                        evidence=[evidence(relation.quote, relation.confidence)],
                    )
                )
    return Publication(
        source_type="video",
        external_id=str(material.id),
        native_build_id=build_id,
        source_fingerprint=material.fingerprint,
        extractor_version=version,
        title=material.title,
        url=material.url,
        metadata=dict(source_key=material.source_key, caption_language=material.caption_language),
        entities=list(entities.values()),
        facts=facts,
    )


def document_publication(manual, bundle, build_id, output, policy, *, root):
    from app.services.document_graph.grounding import validate_result

    entities, facts = {}, []
    cases = {c["id"]: c for c in bundle["cases"] if c["source_id"] == manual["id"]}
    if set(cases) != set(output["cases"]):
        raise ValueError("文档发布页面覆盖范围不一致")
    # Native checkpoints serialize object keys in sorted order. A replay must
    # yield exactly the same publication as the in-memory extraction order.
    for cid in sorted(output["cases"]):
        result = output["cases"][cid]
        validate_result(bundle, cases[cid], result, policy, root=root)
        review_id = result["review"]["result_hash"]

        def evidence(row, review_id=review_id, cid=cid):
            return Evidence(
                quote=row["quote"],
                review_id=review_id,
                locator={"type": "document", "case_id": cid, **row},
            )

        keys = {e["key"]: e["id"] for e in result["entities"]}
        for entity in result["entities"]:
            _merge_entity(
                entities,
                Entity(
                    id=entity["id"],
                    name=entity["canonical_name"],
                    entity_type=entity["entity_type"],
                    scope=entity["scope_key"],
                    mentions=[evidence(r) for r in entity.get("source", [])],
                ),
            )
        for row in result["facts"]:
            p = row["proposal"]
            kind = (
                "relation"
                if p.get("object")
                else {
                    "has_parameter": "attribute",
                    "indicates": "state",
                    "instruction": "instruction",
                }.get(p["predicate"], "attribute")
            )
            facts.append(
                Fact(
                    subject=keys[p["subject"]],
                    object=keys[p["object"]] if p.get("object") else None,
                    predicate=p["predicate"],
                    value=p.get("value"),
                    attribute=p.get("attribute"),
                    kind=kind,
                    assertion=p.get("assertion", "asserted"),
                    qualifiers=p.get("qualifiers", {}),
                    details={k: p[k] for k in ("led_pattern", "operating_state") if p.get(k)},
                    evidence=[evidence(r) for r in row["source"]],
                )
            )
    return Publication(
        source_type="document",
        external_id=manual["id"],
        native_build_id=build_id,
        source_fingerprint=bundle["source_hash"],
        extractor_version=policy,
        title=manual["title"],
        url=manual["source_url"],
        metadata={k: manual[k] for k in ("model", "manual_version", "sha256")},
        entities=list(entities.values()),
        facts=facts,
    )
