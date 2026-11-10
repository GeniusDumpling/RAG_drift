"""Immutable video source material; PostgreSQL is used only for business source rows."""

import hashlib
import json
import math
import re
import uuid
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.content import ContentChunk, ContentItem

ENTITY_NAMESPACE = uuid.UUID("a37984c5-3e93-43a7-8051-2503bfc69e68")
LEASE_SECONDS = 300


class LeaseLostError(RuntimeError):
    pass


def digest(value: Any) -> str:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode()).hexdigest()


def locate_quote(text: str, quote: str) -> tuple[int, int] | None:
    """Allow whitespace changes only; return offsets into the original stored text."""
    parts = quote.strip().split()
    if not parts:
        return None
    match = re.search(r"\s+".join(re.escape(part) for part in parts), text)
    return (match.start(), match.end()) if match else None


@dataclass(frozen=True)
class VideoChunk:
    id: uuid.UUID
    text: str
    evidence_type: str
    start_seconds: float | None
    end_seconds: float | None


@dataclass(frozen=True)
class VideoMaterial:
    id: uuid.UUID
    source_key: str
    title: str
    url: str
    summary: str
    caption_source: str | None
    caption_language: str | None
    chunks: tuple[VideoChunk, ...]

    @property
    def fingerprint(self) -> str:
        return digest(
            {
                "source": self.source_key,
                "title": self.title,
                "url": self.url,
                "summary": self.summary,
                "caption_source": self.caption_source,
                "caption_language": self.caption_language,
                "chunks": [
                    {
                        "id": str(c.id),
                        "text": c.text,
                        "kind": c.evidence_type,
                        "start": c.start_seconds,
                        "end": c.end_seconds,
                    }
                    for c in self.chunks
                ],
            }
        )

    @property
    def extraction_chunks(self) -> tuple[VideoChunk, ...]:
        transcripts = tuple(c for c in self.chunks if c.evidence_type == "transcript_segment")
        return transcripts or tuple(c for c in self.chunks if c.evidence_type == "video_summary")


def _seconds(value: Any) -> float | None:
    try:
        number = float(value)
        return number if math.isfinite(number) and number >= 0 else None
    except (ValueError, TypeError):
        return None


def load_material(
    session: Session, content_id: uuid.UUID, *, lock: bool = False
) -> VideoMaterial | None:
    item = (
        session.scalar(select(ContentItem).where(ContentItem.id == content_id).with_for_update())
        if lock
        else session.get(ContentItem, content_id)
    )
    if item is None or item.item_type != "video_description":
        return None
    chunk_query = (
        select(ContentChunk)
        .where(
            ContentChunk.content_item_id == content_id,
            ContentChunk.embed_status != "obsolete",
        )
        .order_by(ContentChunk.chunk_index)
    )
    rows = session.scalars(chunk_query.with_for_update() if lock else chunk_query).all()
    chunks = []
    for row in rows:
        meta = row.chunk_metadata_json or {}
        kind = meta.get("evidence_type")
        if kind not in {"transcript_segment", "video_summary"} or not row.display_text.strip():
            continue
        start, end = _seconds(meta.get("start_seconds")), _seconds(meta.get("end_seconds"))
        if start is not None and end is not None and end < start:
            start = end = None
        chunks.append(VideoChunk(row.id, row.display_text, kind, start, end))
    meta = item.metadata_json or {}
    source_key = f"youtube:{meta['video_id']}" if meta.get("video_id") else item.canonical_url
    return VideoMaterial(
        item.id,
        source_key,
        item.title or "",
        item.canonical_url,
        item.cleaned_text,
        meta.get("caption_source"),
        meta.get("caption_language"),
        tuple(chunks),
    )


def chunk_fingerprint(chunk: VideoChunk) -> str:
    return digest(
        {
            "text": chunk.text,
            "evidence_type": chunk.evidence_type,
            "start_seconds": chunk.start_seconds,
            "end_seconds": chunk.end_seconds,
        }
    )
