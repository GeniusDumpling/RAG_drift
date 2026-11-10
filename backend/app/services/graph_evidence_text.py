"""Render complete graph assertions with conditions within a shared budget."""

import json

from app.schemas.search import EvidenceObject


def graph_evidence_text(item: EvidenceObject, budget: int) -> str:
    blocks = []
    for entry in item.graph_evidence:
        prefix = (
            f"图谱关系线索: {entry.subject} — {entry.predicate} — {entry.object}\n"
            f"陈述状态: {entry.assertion}（原文陈述，未独立事实验证）\n"
            f"适用条件: {json.dumps(entry.qualifiers, ensure_ascii=False)}\n"
            f"来源定位: {entry.video_url}\n原文引句: "
        )
        available = budget - len(prefix) - 12
        if available < 40:
            continue
        quote = entry.quote[:available]
        if len(quote) < len(entry.quote):
            quote += "…（引句截断）"
        block = prefix + quote + "\n"
        if len(block) <= budget:
            blocks.append(block)
            budget -= len(block)
    return "".join(blocks)
