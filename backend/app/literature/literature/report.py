from __future__ import annotations

from typing import Any


def render_report(
    *,
    query: str,
    directions: list[dict[str, Any]],
    results: list[dict[str, Any]],
) -> str:
    lines = [
        "# IEEE 无人机文献研究报告",
        "",
        f"- 原始查询：{query}",
        f"- 研究方向数：{len(directions)}",
        f"- 入选文献数：{len(results)}",
        "",
        "## 研究方向",
        "",
        "| ID | 方向 | IEEE 检索式 |",
        "| --- | --- | --- |",
    ]
    for direction in directions:
        lines.append(
            f"| {direction.get('id', '')} | {_cell(direction.get('title_zh'))} | "
            f"`{_cell(direction.get('query'))}` |"
        )
    lines.extend(["", "## 入选论文与结论", ""])
    for index, result in enumerate(results, 1):
        paper = result.get("paper") or {}
        analysis = result.get("analysis") or {}
        lines.extend(
            [
                f"### {index}. {paper.get('articleTitle') or paper.get('title') or 'Untitled'}",
                "",
                f"- 方向：{result.get('direction_id', '')}",
                f"- 出版物：{paper.get('publicationTitle') or '未知'}",
                f"- 年份：{paper.get('publicationYear') or '未知'}",
                f"- DOI：{paper.get('doi') or '无'}",
                f"- IEEE 文档号：{paper.get('articleNumber') or paper.get('arnumber') or '无'}",
                "",
                "**匹配方式**",
                "",
                str(analysis.get("match_how") or "未生成。"),
                "",
                "**可用价值**",
                "",
                str(analysis.get("match_use") or "未生成。"),
                "",
                "**结论**",
                "",
                str(analysis.get("conclusion") or "未生成。"),
                "",
            ]
        )
        hits = analysis.get("hits")
        if isinstance(hits, list) and hits:
            lines.extend(
                [
                    "| 匹配词 | 等级 | 来源 | 页码 | 已校验 | 证据 |",
                    "| --- | --- | --- | --- | --- | --- |",
                ]
            )
            for hit in hits:
                if not isinstance(hit, dict):
                    continue
                lines.append(
                    f"| {_cell(hit.get('matched_term'))} | {_cell(hit.get('level'))} | "
                    f"{_cell(hit.get('evidence_source'))} | {_cell(hit.get('page_number'))} | "
                    f"{'是' if hit.get('verified') else '否'} | {_cell(hit.get('evidence'))} |"
                )
            lines.append("")
    lines.extend(
        [
            "## 可溯源说明",
            "",
            "本报告对应的 IEEE 原始 JSON、论文元数据、PDF、按页正文、分析 JSON "
            "和证据记录均存储在 PostgreSQL 文献任务表中。",
            "前端制品链接通过任务 ID、论文 ID、SHA-256 与原始 URL 建立追溯链。",
            "",
        ]
    )
    return "\n".join(lines)


def _cell(value: Any) -> str:
    return str(value if value is not None else "").replace("|", "\\|").replace("\n", " ")
