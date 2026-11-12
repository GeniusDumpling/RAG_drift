"""Select diverse evidence and keep full generation context separate from UI snippets."""

from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.content import ContentChunk, ContentItem
from app.schemas.search import EvidenceObject
from app.services.graph_evidence_text import graph_evidence_text
from app.services.ifixit_media import images_for_chunk


@dataclass(frozen=True)
class AnswerContext:
    evidence: list[EvidenceObject]
    text: str
    chunk_ids: list[UUID]


async def prepare_answer_context(
    session: AsyncSession,
    evidence: list[EvidenceObject],
    *,
    max_chars: int = 12_000,
    max_evidence: int = 8,
    max_per_content: int = 2,
    preserve_order: bool = False,
) -> AnswerContext:
    # Reranked hits retain model order; otherwise give each source a place first.
    selected: list[EvidenceObject] = []
    counts: dict[UUID, int] = {}
    seen_chunks: set[UUID] = set()
    allowances = [max_per_content] if preserve_order else range(1, max_per_content + 1)
    for allowance in allowances:
        for item in evidence:
            if item.chunk_id in seen_chunks or counts.get(item.content_item_id, 0) >= allowance:
                continue
            selected.append(item)
            seen_chunks.add(item.chunk_id)
            counts[item.content_item_id] = counts.get(item.content_item_id, 0) + 1
            if len(selected) >= max_evidence:
                break
        if len(selected) >= max_evidence:
            break
    if not selected:
        return AnswerContext([], "", [])

    hits = list(
        (
            await session.scalars(
                select(ContentChunk).where(
                    ContentChunk.id.in_([item.chunk_id for item in selected]),
                    ContentChunk.embed_status != "obsolete",
                )
            )
        ).all()
    )
    by_id = {chunk.id: chunk for chunk in hits}
    items = {
        item.id: item
        for item in await session.scalars(
            select(ContentItem).where(ContentItem.id.in_([hit.content_item_id for hit in hits]))
        )
    }
    ranges = [
        and_(
            ContentChunk.content_item_id == chunk.content_item_id,
            ContentChunk.chunk_index.between(chunk.chunk_index - 1, chunk.chunk_index + 1),
        )
        for chunk in hits
    ]
    neighbors = (
        list(
            (
                await session.scalars(
                    select(ContentChunk)
                    .where(
                        or_(*ranges),
                        ContentChunk.embed_status != "obsolete",
                    )
                    .order_by(ContentChunk.chunk_index)
                )
            ).all()
        )
        if ranges
        else []
    )

    blocks: list[str] = []
    used_evidence: list[EvidenceObject] = []
    context_chunk_ids: list[UUID] = []
    remaining = max_chars
    rendered_chunks: dict[UUID, int] = {}
    source_headers: dict[tuple[UUID, str | None, str, str], int] = {}
    for item in selected:
        hit = by_id.get(item.chunk_id)
        if hit is None or hit.content_item_id != item.content_item_id:
            continue
        header = (
            f"[{len(blocks) + 1}] 标题: {item.title or '无标题'}\n"
            f"来源: {item.source_site_name}\nURL: {item.canonical_url}\n内容: "
        )
        source_key = (item.content_item_id, item.title, item.source_site_name, item.canonical_url)
        if source_key in source_headers:
            header = (
                f"[{len(blocks) + 1}] 标题: 与第{source_headers[source_key]}条相同，"
                "来源及URL也相同；"
                f"下文事实仍引用本条编号。\n内容: "
            )
        # Fair allocation prevents one large passage consuming the entire budget.
        budget = min(remaining - len(header) - 2, max_chars // len(selected))
        if budget < 1:
            break
        # Prefer the actual hit. Only add neighbors when its full text fits.
        graph_text = graph_evidence_text(item, budget // 2)
        body = hit.display_text.strip()
        reference = (
            f"此切片原文已完整呈现于证据[{rendered_chunks[hit.id]}]。"
            if hit.id in rendered_chunks else ""
        )
        if reference and len(graph_text) + len(reference) <= budget:
            text = graph_text + reference
        else:
            text = graph_text + body[: budget - len(graph_text)]
            if len(graph_text) + len(body) <= budget:
                rendered_chunks[hit.id] = len(blocks) + 1
        used_ids = [hit.id]
        for neighbor in neighbors:
            if (
                neighbor.content_item_id != hit.content_item_id
                or abs(neighbor.chunk_index - hit.chunk_index) != 1
            ):
                continue
            addition = neighbor.display_text.strip()
            if neighbor.id in rendered_chunks:
                # Reuse text, but keep the image/provenance association for this citation.
                used_ids.append(neighbor.id)
                continue
            if len(text) + len(addition) + 2 <= budget:
                text = (
                    addition + "\n\n" + text
                    if neighbor.chunk_index < hit.chunk_index
                    else text + "\n\n" + addition
                )
                used_ids.append(neighbor.id)
                rendered_chunks[neighbor.id] = len(blocks) + 1
        if not text:
            continue
        content_item = items.get(item.content_item_id)
        images = []
        for chunk_id in used_ids:
            source_chunk = by_id.get(chunk_id) or next(
                (n for n in neighbors if n.id == chunk_id), None
            )
            if source_chunk is not None and content_item is not None:
                images.extend(images_for_chunk(content_item, source_chunk))
        images = list({(i.asset_id, i.step_order): i for i in images}.values())
        if images:
            item = item.model_copy(update={"images": images})
        block = header + text
        blocks.append(block)
        source_headers.setdefault(source_key, len(blocks))
        used_evidence.append(item)
        context_chunk_ids.extend(used_ids)
        remaining -= len(block) + 2
    return AnswerContext(used_evidence, "\n\n".join(blocks), list(dict.fromkeys(context_chunk_ids)))
