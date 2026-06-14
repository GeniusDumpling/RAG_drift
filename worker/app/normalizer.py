import hashlib
import uuid
from dataclasses import dataclass
from typing import Any

from app.agents.contracts import ExtractionAgentResponse, ExtractionItem
from app.models.content import Author, ContentItem, RawPage
from app.models.control import SourceSite
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession


def stable_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def clean_text(text: str) -> str:
    return " ".join(text.split())


@dataclass(frozen=True)
class NormalizationResult:
    content_item_ids: list[uuid.UUID]
    created_item_ids: list[uuid.UUID]
    reused_item_ids: list[uuid.UUID]
    deduped_count: int


async def normalize_extraction_response(
    session: AsyncSession,
    *,
    source_site: SourceSite,
    raw_page: RawPage,
    response: ExtractionAgentResponse,
    structured_by: str = "extraction_agent",
) -> NormalizationResult:
    author_ids = [
        await _upsert_author(session, source_site_id=source_site.id, author_json=item.author)
        for item in response.items
    ]

    inserted_items: list[ContentItem] = []
    created_item_ids: list[uuid.UUID] = []
    created_item_occurrences: list[bool] = []
    reused_item_ids: list[uuid.UUID] = []
    item_by_ref: dict[str, ContentItem] = {}
    canonical_url = raw_page.final_url or raw_page.requested_url

    for index, item in enumerate(response.items):
        cleaned = clean_text(item.body_text)
        content_hash = stable_hash(cleaned)
        item_discriminator = _stable_item_discriminator(
            index=index,
            item=item,
            raw_page_id=raw_page.id,
        )
        dedup_key = stable_hash(
            f"{source_site.id}:{canonical_url}:{item.item_type}:"
            f"{item_discriminator}:{content_hash}"
        )
        content_item = await session.scalar(
            select(ContentItem).where(ContentItem.dedup_key == dedup_key)
        )
        created_item_for_occurrence = False
        if content_item is None:
            content_item = ContentItem(
                source_site_id=source_site.id,
                raw_page_id=raw_page.id,
                crawl_run_id=raw_page.crawl_run_id,
                author_id=author_ids[index],
                parent_item_id=None,
                thread_root_id=None,
                item_type=item.item_type,
                title=item.title,
                canonical_url=canonical_url,
                source_url=raw_page.requested_url,
                published_at=item.published_at,
                language=source_site.default_language,
                raw_text=item.body_text,
                cleaned_text=cleaned,
                summary_text=item.summary_text,
                structured_by=structured_by,
                extraction_confidence=response.extraction_confidence,
                tags=item.tags,
                metadata_json={
                    **item.metadata_json,
                    "external_item_id": item.external_item_id,
                    "parent_ref": item.parent_ref,
                    "thread_root_ref": item.thread_root_ref,
                    "page_kind": response.page_kind,
                    "warnings": response.warnings,
                },
                content_hash=content_hash,
                dedup_key=dedup_key,
                search_tsv=None,
            )
            content_item, created = await _insert_content_item_or_reuse_dedup_winner(
                session,
                content_item=content_item,
                dedup_key=dedup_key,
            )
            if created:
                created_item_ids.append(content_item.id)
                created_item_for_occurrence = True
            else:
                reused_item_ids.append(content_item.id)
        else:
            reused_item_ids.append(content_item.id)

        inserted_items.append(content_item)
        created_item_occurrences.append(created_item_for_occurrence)
        for ref in _reference_keys(index=index, item=item):
            item_by_ref[ref] = content_item

    first_thread = next((item for item in inserted_items if item.item_type == "thread"), None)
    for source_item, content_item, created_item_for_occurrence in zip(
        response.items, inserted_items, created_item_occurrences, strict=True
    ):
        if not created_item_for_occurrence:
            continue

        if source_item.parent_ref is not None:
            parent = item_by_ref.get(source_item.parent_ref)
            if parent is not None:
                content_item.parent_item_id = parent.id

        if source_item.thread_root_ref is not None:
            thread_root = item_by_ref.get(source_item.thread_root_ref)
            if thread_root is not None:
                content_item.thread_root_id = thread_root.id

        if content_item.item_type == "thread" and content_item.thread_root_id is None:
            content_item.thread_root_id = content_item.id

        if content_item.item_type == "comment" and content_item.thread_root_id is None:
            if first_thread is not None:
                content_item.thread_root_id = first_thread.id
                if content_item.parent_item_id is None:
                    content_item.parent_item_id = first_thread.id

    await session.flush()
    return NormalizationResult(
        content_item_ids=[item.id for item in inserted_items],
        created_item_ids=created_item_ids,
        reused_item_ids=reused_item_ids,
        deduped_count=len(reused_item_ids),
    )


async def _insert_content_item_or_reuse_dedup_winner(
    session: AsyncSession,
    *,
    content_item: ContentItem,
    dedup_key: str,
) -> tuple[ContentItem, bool]:
    try:
        async with session.begin_nested():
            session.add(content_item)
            await session.flush()
    except IntegrityError:
        dedup_winner = await session.scalar(
            select(ContentItem).where(ContentItem.dedup_key == dedup_key)
        )
        if dedup_winner is None:
            raise
        return dedup_winner, False

    return content_item, True


async def _upsert_author(
    session: AsyncSession,
    *,
    source_site_id: uuid.UUID,
    author_json: dict[str, Any] | None,
) -> uuid.UUID | None:
    if author_json is None:
        return None

    external_author_id = _string_or_none(
        author_json.get("external_author_id") or author_json.get("id")
    )
    handle = _string_or_none(author_json.get("handle"))
    display_name = _string_or_none(author_json.get("display_name") or author_json.get("name"))
    profile_url = _string_or_none(author_json.get("profile_url") or author_json.get("url"))

    if display_name is None:
        display_name = handle or external_author_id or "Unknown author"

    existing_author: Author | None = None
    if external_author_id is not None:
        existing_author = await session.scalar(
            select(Author).where(
                Author.source_site_id == source_site_id,
                Author.external_author_id == external_author_id,
            )
        )
    elif handle is not None:
        existing_author = await session.scalar(
            select(Author).where(Author.source_site_id == source_site_id, Author.handle == handle)
        )

    if existing_author is None:
        existing_author = Author(
            source_site_id=source_site_id,
            display_name=display_name,
            handle=handle,
            profile_url=profile_url,
            external_author_id=external_author_id,
            raw_json=author_json,
        )
        session.add(existing_author)
    else:
        existing_author.display_name = display_name
        existing_author.handle = handle
        existing_author.profile_url = profile_url
        existing_author.external_author_id = external_author_id
        existing_author.raw_json = author_json

    await session.flush()
    return existing_author.id


def _stable_item_discriminator(
    *, index: int, item: ExtractionItem, raw_page_id: uuid.UUID
) -> str:
    external_item_id = _string_or_none(item.external_item_id)
    if external_item_id is not None and external_item_id != str(raw_page_id):
        return f"external:{external_item_id}"

    metadata_ref = item.metadata_json.get("ref")
    if isinstance(metadata_ref, str) and metadata_ref.strip():
        return f"ref:{metadata_ref.strip()}"

    return f"index:{index}"


def _reference_keys(*, index: int, item: ExtractionItem) -> set[str]:
    keys = {str(index), f"item:{index}"}
    if item.external_item_id is not None and item.external_item_id.strip():
        keys.add(item.external_item_id.strip())
    metadata_ref = item.metadata_json.get("ref")
    if isinstance(metadata_ref, str) and metadata_ref.strip():
        keys.add(metadata_ref.strip())
    return keys


def _string_or_none(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        stripped = value.strip()
        return stripped or None
    return str(value)
