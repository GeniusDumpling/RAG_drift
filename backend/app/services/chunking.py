import hashlib
from dataclasses import dataclass
from typing import Any

CHUNKER_VERSION = "2026-06-14-v2"

_CONTEXTUAL_ARTICLE_TYPES = {"article", "doc_page", "release_note", "thread"}


@dataclass(frozen=True)
class BuiltChunk:
    chunk_index: int
    chunk_type: str
    section_path: str | None
    display_text: str
    embed_text: str
    start_char: int
    end_char: int
    token_count: int
    chunk_metadata_json: dict[str, Any]


def build_chunks(
    *,
    item_type: str,
    title: str | None,
    cleaned_text: str,
    summary_text: str | None,
    tags: list[str],
    thread_title: str | None = None,
    max_chars: int = 1200,
) -> list[BuiltChunk]:
    if max_chars < 1:
        raise ValueError("max_chars must be at least 1")

    normalized_text = cleaned_text.strip()
    normalized_tags = [tag.strip() for tag in tags if tag.strip()]
    if item_type == "comment":
        return _build_contextual_comment_chunks(
            item_type=item_type,
            cleaned_text=normalized_text,
            tags=normalized_tags,
            thread_title=thread_title,
            max_chars=max_chars,
        )

    if item_type in _CONTEXTUAL_ARTICLE_TYPES:
        return _build_contextual_article_chunks(
            item_type=item_type,
            title=title,
            cleaned_text=normalized_text,
            summary_text=summary_text,
            tags=normalized_tags,
            max_chars=max_chars,
        )

    return _build_contextual_article_chunks(
        item_type=item_type,
        title=title,
        cleaned_text=normalized_text,
        summary_text=summary_text,
        tags=normalized_tags,
        max_chars=max_chars,
    )


def _build_contextual_comment_chunks(
    *,
    item_type: str,
    cleaned_text: str,
    tags: list[str],
    thread_title: str | None,
    max_chars: int,
) -> list[BuiltChunk]:
    chunks: list[BuiltChunk] = []
    if not cleaned_text:
        ranges = [(0, "")]
    else:
        ranges = [
            (start, cleaned_text[start : start + max_chars])
            for start in range(0, len(cleaned_text), max_chars)
        ]

    for chunk_index, (start, comment_text) in enumerate(ranges):
        chunk_type = (
            "comment_contextual"
            if chunk_index == 0
            else "comment_contextual_continued"
        )
        comment_label = "Comment" if chunk_index == 0 else "Comment continued"
        chunks.append(
            _make_chunk(
                chunk_index=chunk_index,
                chunk_type=chunk_type,
                section_path=None,
                display_text=comment_text,
                embed_text=_join_labeled_parts(
                    [
                        ("Thread", thread_title),
                        ("Tags", _format_tags(tags)),
                        (comment_label, comment_text),
                    ]
                ),
                start_char=start,
                end_char=start + len(comment_text),
                metadata={
                    "item_type": item_type,
                    "chunk_type": chunk_type,
                    "thread_title": thread_title,
                    "tags": tags,
                },
            )
        )

    return chunks


def _build_contextual_article_chunks(
    *,
    item_type: str,
    title: str | None,
    cleaned_text: str,
    summary_text: str | None,
    tags: list[str],
    max_chars: int,
) -> list[BuiltChunk]:
    lead_start, lead_end, lead = _lead_segment(cleaned_text, max_chars)
    chunks = [
        _make_chunk(
            chunk_index=0,
            chunk_type="title_lead",
            section_path=None,
            display_text=_join_unlabeled_parts([title, summary_text, lead]),
            embed_text=_join_labeled_parts(
                [
                    ("Title", title),
                    ("Summary", summary_text),
                    ("Tags", _format_tags(tags)),
                    ("Lead", lead),
                ]
            ),
            start_char=lead_start,
            end_char=lead_end,
            metadata={
                "item_type": item_type,
                "chunk_type": "title_lead",
                "tags": tags,
            },
        )
    ]

    body_start = _skip_whitespace(cleaned_text, lead_end)
    for start in range(body_start, len(cleaned_text), max_chars):
        body_text = cleaned_text[start : start + max_chars]
        if not body_text:
            continue
        chunks.append(
            _make_chunk(
                chunk_index=len(chunks),
                chunk_type="body_section",
                section_path=None,
                display_text=body_text,
                embed_text=_join_labeled_parts(
                    [
                        ("Title", title),
                        ("Tags", _format_tags(tags)),
                        ("Body", body_text),
                    ]
                ),
                start_char=start,
                end_char=start + len(body_text),
                metadata={
                    "item_type": item_type,
                    "chunk_type": "body_section",
                    "tags": tags,
                },
            )
        )

    return chunks


def _make_chunk(
    *,
    chunk_index: int,
    chunk_type: str,
    section_path: str | None,
    display_text: str,
    embed_text: str,
    start_char: int,
    end_char: int,
    metadata: dict[str, Any],
) -> BuiltChunk:
    return BuiltChunk(
        chunk_index=chunk_index,
        chunk_type=chunk_type,
        section_path=section_path,
        display_text=display_text,
        embed_text=embed_text,
        start_char=start_char,
        end_char=end_char,
        token_count=max(1, len(embed_text.split())),
        chunk_metadata_json={
            **metadata,
            "section_path": section_path,
            "start_char": start_char,
            "end_char": end_char,
            "chunker_version": CHUNKER_VERSION,
            "embed_text_hash": _embed_text_hash(embed_text),
        },
    )


def _lead_segment(text: str, max_chars: int) -> tuple[int, int, str]:
    for paragraph in text.split("\n\n"):
        stripped = paragraph.strip()
        if stripped:
            start_char = text.find(stripped)
            lead = stripped[:max_chars]
            return start_char, start_char + len(lead), lead
    return 0, 0, ""


def _skip_whitespace(text: str, start_char: int) -> int:
    while start_char < len(text) and text[start_char].isspace():
        start_char += 1
    return start_char


def _embed_text_hash(embed_text: str) -> str:
    return hashlib.sha256(embed_text.encode("utf-8")).hexdigest()


def _format_tags(tags: list[str]) -> str | None:
    if not tags:
        return None
    return ", ".join(tags)


def _join_labeled_parts(parts: list[tuple[str, str | None]]) -> str:
    lines = [f"{label}: {value.strip()}" for label, value in parts if value and value.strip()]
    return "\n".join(lines) or "[empty]"


def _join_unlabeled_parts(parts: list[str | None]) -> str:
    values = [value.strip() for value in parts if value and value.strip()]
    return "\n\n".join(values) or "[empty]"
