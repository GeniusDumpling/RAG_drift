"""Forum graph recall preserves question, action and feedback with actual post citations."""

import asyncio
from uuid import UUID

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.models.content import ContentChunk
from app.schemas.search import ForumGraphAnswerEvidence
from app.services.knowledge_graph.schema import digest
from app.services.retrieval import _hydrate_and_rank_evidence, _SearchHit

from .queries import retrieve
from .schema import Span
from .source import load_material, map_span, validate_span
from .storage import ForumGraphStore


def collect(settings, query):
    engine = create_engine(settings.sync_database_url, pool_pre_ping=True)
    try:
        with ForumGraphStore(settings) as store, Session(engine) as session:
            response = retrieve(store, session, query, limit=settings.answer_graph_top_k)
            records, fingerprints = {}, {}
            for fact in response["items"]:
                for source in fact["sources"]:
                    material = load_material(session, source["metadata"]["content_item_id"])
                    if material is None:
                        raise ValueError("论坛来源消失")
                    fingerprints[material.content_item_id] = material.fingerprint

                    def add(
                        span,
                        locator,
                        *,
                        role,
                        outcome=None,
                        combined=False,
                        material=material,
                        fact=fact,
                    ):
                        span = validate_span(material, span)
                        post = material.post(span.post_id)
                        if not post.indexable:
                            return
                        for mapping in map_span(material, span):
                            entry = ForumGraphAnswerEvidence(
                                id=digest(
                                    [
                                        fact["id"],
                                        locator.get("claim_id"),
                                        role,
                                        mapping["chunk_id"],
                                        span.model_dump(),
                                    ]
                                ),
                                fact_id=fact["id"],
                                source_id=material.source_id,
                                content_item_id=UUID(material.content_item_id),
                                post_id=post.id,
                                post_number=post.number,
                                author=post.author,
                                post_url=post.url,
                                quote=mapping["quote"],
                                case_id=locator.get("case_id"),
                                claim_id=locator.get("claim_id"),
                                role=role,
                                act="test_report" if role == "feedback" else locator["act"],
                                assertion=fact["assertion"],
                                subject=fact["subject_entity"]["name"],
                                predicate=fact["predicate"],
                                object=fact["object_entity"]["name"]
                                if fact["object_entity"]
                                else None,
                                value=fact["value"],
                                qualifiers=fact["qualifiers"],
                                outcome=outcome,
                                combined_operations=combined,
                                accepted_answer=post.accepted,
                            )
                            records.setdefault(mapping["chunk_id"], {})[entry.id] = entry

                    for evidence in source["evidence"]:
                        loc = evidence["locator"]
                        add(
                            Span(
                                post_id=loc["post_id"],
                                start=loc["post_start"],
                                end=loc["post_end"],
                                quote=evidence["quote"],
                            ),
                            loc,
                            role=loc["source_role"],
                        )
                        for evaluation in loc.get("evaluations", []):
                            for span in evaluation["evidence"]:
                                add(
                                    span,
                                    loc,
                                    role="feedback",
                                    outcome=evaluation["outcome"],
                                    combined=evaluation["combined_operations"],
                                )
            return records, fingerprints
    finally:
        engine.dispose()


async def recall(session, query, filters, settings):
    trace = dict(status="disabled")
    if not settings.forum_graph_enabled:
        return [], trace
    try:
        async with asyncio.timeout(settings.answer_graph_timeout_seconds):
            records, fingerprints = await asyncio.to_thread(collect, settings, query)
            # Alternate evidence roles, rather than taking the first facts from a
            # long thread and losing the actual test outcome.
            ordered = []
            for role in ("context", "claim", "feedback"):
                for cid, entries in records.items():
                    if cid not in ordered and any(e.role == role for e in entries.values()):
                        ordered.append(cid)
            hits = [
                _SearchHit(
                    UUID(cid), next(iter(records[cid].values())).content_item_id, 1.0, "graph"
                )
                for cid in ordered
            ]
            evidence = await _hydrate_and_rank_evidence(
                session,
                hits=hits,
                filters=filters,
                top_k=settings.answer_graph_top_k,
                snippet_terms=(),
                max_per_content=3,
            )
            chunks = {
                str(c.id): c
                for c in (
                    await session.scalars(
                        select(ContentChunk).where(
                            ContentChunk.id.in_([e.chunk_id for e in evidence]),
                            ContentChunk.embed_status != "obsolete",
                        )
                    )
                ).all()
            }
            safe = []
            for item in evidence:
                chunk = chunks.get(str(item.chunk_id))
                if chunk is None:
                    continue
                item.forum_graph_evidence = [
                    entry
                    for entry in records[str(item.chunk_id)].values()
                    if entry.content_item_id == item.content_item_id
                    and entry.post_id == chunk.chunk_metadata_json.get("post_id")
                    and entry.quote in chunk.display_text
                ][:4]
                if item.forum_graph_evidence:
                    safe.append(item)
            # Revalidate in the caller's actual source database after hydration,
            # including changes during a graph/network request.
            for cid in {str(e.content_item_id) for e in safe}:
                current = await session.run_sync(lambda s, cid=cid: load_material(s, cid))
                if current is None or current.fingerprint != fingerprints[cid]:
                    raise ValueError("图谱召回期间论坛正文或反馈已变化")
            trace.update(status="retrieved" if safe else "empty", evidence_count=len(safe))
            return safe, trace
    except Exception as exc:
        trace.update(status="fallback", reason=type(exc).__name__)
        return [], trace
