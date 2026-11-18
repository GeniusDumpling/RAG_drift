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
    for entry in item.forum_graph_evidence:
        statement = (
            "案例背景（不作为其他楼层操作建议的直接证据）"
            if entry.role == "context"
            else f"{entry.subject} — {entry.predicate} — {entry.object or entry.value or ''}"
        )
        prefix = (
            f"论坛图谱证据（{entry.role}）: {statement}\n"
            f"发言行为: {entry.act}; 陈述: {entry.assertion}（来源陈述，不是通用验证结论）\n"
            f"场景与条件: {json.dumps(entry.qualifiers, ensure_ascii=False)}\n"
            f"反馈: {entry.outcome or '尚无明确验证反馈'}; 联合操作: {entry.combined_operations}\n"
            f"作者/楼层: {entry.author} / {entry.post_number}; 原文: {entry.post_url}\n引句: "
        )
        if len(prefix) + 40 > budget:
            continue
        quote = entry.quote[: budget - len(prefix) - 1]
        block = prefix + quote + "\n"
        blocks.append(block)
        budget -= len(block)
    return "".join(blocks)
