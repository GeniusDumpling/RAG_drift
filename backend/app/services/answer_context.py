"""Select diverse evidence and keep full generation context separate from UI snippets."""

from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.content import ContentChunk
from app.schemas.search import EvidenceObject


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
) -> AnswerContext:
    # First give each source a place, then allow a second passage per source.
    selected: list[EvidenceObject] = []
    counts: dict[UUID, int] = {}
    seen_chunks: set[UUID] = set()
    for allowance in range(1, max_per_content + 1):
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
    for item in selected:
        hit = by_id.get(item.chunk_id)
        if hit is None or hit.content_item_id != item.content_item_id:
            continue
        header = (
            f"[{len(blocks) + 1}] 标题: {item.title or '无标题'}\n"
            f"来源: {item.source_site_name}\nURL: {item.canonical_url}\n内容: "
        )
        # Fair allocation prevents one large passage consuming the entire budget.
        budget = min(remaining - len(header) - 2, max_chars // len(selected))
        if budget < 1:
            break
        # Prefer the actual hit. Only add neighbors when its full text fits.
        text = hit.display_text.strip()[:budget]
        used_ids = [hit.id]
        for neighbor in neighbors:
            if (
                neighbor.content_item_id != hit.content_item_id
                or abs(neighbor.chunk_index - hit.chunk_index) != 1
            ):
                continue
            addition = neighbor.display_text.strip()
            if len(text) + len(addition) + 2 <= budget:
                text = (
                    addition + "\n\n" + text
                    if neighbor.chunk_index < hit.chunk_index
                    else text + "\n\n" + addition
                )
                used_ids.append(neighbor.id)
        if not text:
            continue
        block = header + text
        blocks.append(block)
        used_evidence.append(item)
        context_chunk_ids.extend(used_ids)
        remaining -= len(block) + 2
    return AnswerContext(used_evidence, "\n\n".join(blocks), list(dict.fromkeys(context_chunk_ids)))
