from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from . import VERSION
from .common import digest


@dataclass
class Budget:
    max_tokens: int = 480
    max_chars: int = 1600
    tokenizer: Any = None
    tokenizer_name: str | None = None

    def count(self, text: str) -> int:
        if self.tokenizer is not None:
            return len(self.tokenizer.encode(text, add_special_tokens=True, truncation=False))
        # Conservative preparation bound, explicitly not a measured model token count.
        return len(text.encode("utf-8")) + 2

    @property
    def method(self) -> str:
        return "tokenizer" if self.tokenizer is not None else "utf8_bytes_estimate"

    def fits(self, text: str) -> bool:
        return len(text) <= self.max_chars and self.count(text) <= self.max_tokens


def load_budget(
    *,
    max_tokens: int,
    max_chars: int,
    tokenizer_path: str | None = None,
) -> Budget:
    if max_tokens < 16 or max_chars < 32:
        raise ValueError("max_tokens must be >= 16 and max_chars must be >= 32")
    budget = Budget(max_tokens=max_tokens, max_chars=max_chars)
    if tokenizer_path:
        from transformers import AutoTokenizer

        tokenizer = AutoTokenizer.from_pretrained(tokenizer_path, local_files_only=True)
        budget.tokenizer = tokenizer
        budget.tokenizer_name = tokenizer_path
        if tokenizer.model_max_length < 1_000_000:
            budget.max_tokens = min(max_tokens, tokenizer.model_max_length)
    return budget


def _split_ranges(text: str, prefix: str, budget: Budget) -> list[tuple[int, int]]:
    """Split only at lines/sentences; keep an unbreakable unit and flag it later."""
    if budget.fits(prefix + text):
        return [(0, len(text))]
    boundaries = [0]
    boundaries.extend(m.end() for m in re.finditer(r"\n+|(?<=[.!?。！？])\s+", text))
    boundaries.append(len(text))
    ranges: list[tuple[int, int]] = []
    start = boundaries[0]
    end = start
    for next_end in sorted(set(boundaries[1:])):
        if end > start and not budget.fits(prefix + text[start:next_end]):
            ranges.append((start, end))
            start = end
        end = next_end
    if end > start:
        ranges.append((start, end))
    return ranges


def build_chunks(guide: dict[str, Any], budget: Budget) -> list[dict[str, Any]]:
    prefix = (
        f"Device: {guide['category_raw']}\nGuide: {guide['title']}\n"
        f"Subject: {guide['subject_raw']}\n"
    )
    chunks = []
    for section in guide["sections"]:
        step = section.get("step_order")
        context = prefix + (f"Step: {step}\n" if step is not None else "")
        text = section["text"]
        # Avoid separating a safety note from the operation it constrains. An oversized
        # safety-bearing step is kept whole and quarantined from vector indexing.
        if section.get("has_safety_notes"):
            ranges = [(0, len(text))]
        else:
            ranges = _split_ranges(text, context, budget)
        for part, (start, end) in enumerate(ranges):
            body = text[start:end]
            if not body.strip():
                continue
            embed = context + body
            indexable = budget.fits(embed)
            key = f"{guide['document_id']}:{section['key']}:part:{part}"
            metadata = {
                "logical_chunk_key": key,
                "chunk_type": section["chunk_type"],
                "chunker_version": VERSION,
                "embed_text_hash": digest(embed.encode("utf-8")),
                "guide_id": guide["guide_id"],
                "document_id": guide["document_id"],
                "device_key": guide["device_key"],
                "source_url": guide["source_url"],
                "source_path": guide["source_path"],
                "source_revision": guide["source_revision"],
                "step_order": step,
                "step_occurrence_ids": section.get("step_occurrence_ids", []),
                "media_ref_ids": section.get("media_ref_ids", []),
                "continuation": part > 0,
                "part_index": part,
                "part_count": len(ranges),
                "quality_flags": guide["quality_flags"],
                "content_class": guide["content_class"],
                "has_video_reference": guide["has_video_reference"],
                "offset_basis": "cleaned_text",
                "field_ref": section["key"],
                "token_count_method": budget.method,
                "tokenizer": budget.tokenizer_name,
                "indexable": indexable,
                "index_error": None if indexable else "oversized_atomic_content",
                "chunk_owner": "ifixit_rag",
            }
            chunk = {
                "logical_chunk_key": key,
                "document_id": guide["document_id"],
                "chunk_index": len(chunks),
                "chunk_type": section["chunk_type"],
                "step_occurrence_ids": metadata["step_occurrence_ids"],
                "display_text": body,
                "embed_text": embed,
                "char_start": section["char_start"] + start,
                "char_end": section["char_start"] + end,
                "token_count": budget.count(embed) if budget.tokenizer is not None else None,
                "estimated_tokens": budget.count(embed),
                "metadata": metadata,
            }
            chunk["chunk_hash"] = digest(chunk)
            chunks.append(chunk)
    return chunks
