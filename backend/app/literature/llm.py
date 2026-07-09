from __future__ import annotations

import json
import re
import unicodedata
from typing import Any

import httpx

from app.core.config import Settings

UAV_SCOPE = '("UAV" OR "drone" OR "quadrotor" OR "multirotor")'


class LiteratureLLM:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    async def generate_directions(
        self, query: str, *, direction_count: int
    ) -> list[dict[str, Any]]:
        if self.settings.literature_llm_provider == "fake":
            return _fallback_directions(query, direction_count)
        prompt = (
            "你是无人机领域文献检索专家。根据用户关键词生成 IEEE Xplore 检索方向。"
            '只输出 JSON 对象，格式为 {"directions":[...]}; 每个方向必须含 id、'
            "title_zh、title_en、query、matched_terms、rationale_zh。query 必须是英文布尔检索式，"
            f"并用 {UAV_SCOPE} 收口到无人机领域。严禁虚构论文。"
        )
        payload = await self._chat_json(
            system=prompt,
            user=f"用户输入：{query}\n方向数量：{direction_count}",
            temperature=0.2,
        )
        directions = payload.get("directions")
        if not isinstance(directions, list):
            raise RuntimeError("DeepSeek 未返回 directions 数组。")
        return _sanitize_directions(directions, query, direction_count)

    async def analyze_paper(
        self,
        *,
        user_query: str,
        direction: dict[str, Any],
        paper: dict[str, Any],
        fulltext_excerpt: str,
    ) -> dict[str, Any]:
        if self.settings.literature_llm_provider == "fake":
            return _fallback_analysis(user_query, paper)
        system = (
            "你是严谨的无人机论文证据分析器。只能依据给出的标题、摘要和正文节选，输出 JSON。"
            "字段必须包含 abstract_zh、keywords_zh、match_how、match_use、conclusion、hits。"
            "hits 每项包含 matched_term、level(exact/similar/keyword)、evidence、"
            "evidence_source(title/abstract/body)、section_name。"
            "evidence 必须是输入原文的连续子串；"
            "没有证据就不要生成 hit，不得把关键词共现冒充数值完全匹配。"
        )
        paper_payload = {
            "title": paper.get("articleTitle") or paper.get("title"),
            "abstract": paper.get("abstract") or "",
            "publication": paper.get("publicationTitle"),
            "year": paper.get("publicationYear"),
            "doi": paper.get("doi"),
        }
        user = (
            f"用户研究关键词：{user_query}\n"
            f"研究方向：{json.dumps(direction, ensure_ascii=False)}\n"
            f"论文元数据：{json.dumps(paper_payload, ensure_ascii=False)}\n"
            f"正文节选：\n{fulltext_excerpt or '（未提供正文，只能引用标题或摘要）'}"
        )
        return await self._chat_json(system=system, user=user, temperature=0.1)

    async def _chat_json(self, *, system: str, user: str, temperature: float) -> dict[str, Any]:
        if not self.settings.deepseek_api_key:
            raise RuntimeError(
                "缺少 DEEPSEEK_API_KEY；请配置密钥，或将 LITERATURE_LLM_PROVIDER=fake "
                "用于仅验证任务链路。"
            )
        url = f"{self.settings.deepseek_base_url.rstrip('/')}/chat/completions"
        headers = {
            "Authorization": f"Bearer {self.settings.deepseek_api_key}",
            "Content-Type": "application/json",
        }
        body = {
            "model": self.settings.deepseek_model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "temperature": temperature,
            "response_format": {"type": "json_object"},
        }
        timeout = httpx.Timeout(connect=10, read=600, write=30, pool=10)
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.post(url, headers=headers, json=body)
            response.raise_for_status()
            payload = response.json()
        try:
            content = payload["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise RuntimeError("DeepSeek 响应缺少 choices[0].message.content。") from exc
        parsed = _parse_json_object(str(content))
        if not isinstance(parsed, dict):
            raise RuntimeError("DeepSeek 返回的内容不是 JSON 对象。")
        return parsed


def verify_analysis(
    analysis: dict[str, Any],
    *,
    title: str,
    abstract: str,
    pages: list[dict[str, Any]],
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    raw_hits = analysis.get("hits")
    if not isinstance(raw_hits, list):
        raw_hits = []
    evidence_rows: list[dict[str, Any]] = []
    normalized_hits: list[dict[str, Any]] = []
    for item in raw_hits:
        if not isinstance(item, dict):
            continue
        evidence = str(item.get("evidence") or "").strip()[:2000]
        source, page_number, char_start = _locate_evidence(evidence, title, abstract, pages)
        verified = source is not None
        level = str(item.get("level") or "keyword").lower()
        if level not in {"exact", "similar", "keyword"}:
            level = "keyword"
        if not verified:
            level = "keyword"
            evidence = ""
            source = "unverified"
            page_number = None
            char_start = None
        matched_term = str(item.get("matched_term") or "相关关键词").strip()[:500]
        row = {
            "matched_term": matched_term,
            "match_level": level,
            "evidence_text": evidence,
            "evidence_source": source,
            "page_number": page_number,
            "section_name": str(item.get("section_name") or "").strip()[:120] or None,
            "char_start": char_start,
            "char_end": char_start + len(evidence) if char_start is not None else None,
            "verified": verified,
        }
        evidence_rows.append(row)
        normalized_hits.append(
            {
                "matched_term": matched_term,
                "level": level,
                "evidence": evidence,
                "evidence_source": source,
                "page_number": page_number,
                "verified": verified,
            }
        )
    normalized = dict(analysis)
    normalized["hits"] = normalized_hits
    normalized["_verification"] = {
        "total": len(normalized_hits),
        "verified": sum(1 for row in evidence_rows if row["verified"]),
        "downgraded": sum(1 for row in evidence_rows if not row["verified"]),
    }
    return normalized, evidence_rows


def _locate_evidence(
    evidence: str,
    title: str,
    abstract: str,
    pages: list[dict[str, Any]],
) -> tuple[str | None, int | None, int | None]:
    if not evidence:
        return None, None, None
    needle = _normalize(evidence)
    candidates: list[tuple[str, int | None, str]] = [
        ("title", None, title),
        ("abstract", None, abstract),
    ]
    candidates.extend(
        ("body", _safe_page_number(page.get("page")), str(page.get("text") or ""))
        for page in pages
        if isinstance(page, dict)
    )
    for source, page_number, text in candidates:
        if needle and needle in _normalize(text):
            raw_index = text.casefold().find(evidence.casefold())
            return source, page_number, raw_index if raw_index >= 0 else None
    return None, None, None


def _parse_json_object(content: str) -> dict[str, Any]:
    content = content.strip()
    if content.startswith("```"):
        content = re.sub(r"^```(?:json)?\s*", "", content, flags=re.I)
        content = re.sub(r"\s*```$", "", content)
    try:
        value = json.loads(content)
    except json.JSONDecodeError:
        start, end = content.find("{"), content.rfind("}")
        if start < 0 or end <= start:
            raise RuntimeError("模型输出无法解析为 JSON。") from None
        value = json.loads(content[start : end + 1])
    if not isinstance(value, dict):
        raise RuntimeError("模型输出不是 JSON 对象。")
    return value


def _sanitize_directions(rows: list[Any], query: str, direction_count: int) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for index, row in enumerate(rows[:direction_count], 1):
        if not isinstance(row, dict):
            continue
        search_query = str(row.get("query") or "").strip()
        if not search_query:
            continue
        if not any(term in search_query.casefold() for term in ("uav", "drone", "quadrotor")):
            search_query = f"{UAV_SCOPE} AND ({search_query})"
        terms = row.get("matched_terms")
        result.append(
            {
                "id": f"D{len(result) + 1}",
                "title_zh": str(row.get("title_zh") or f"研究方向 {index}").strip(),
                "title_en": str(row.get("title_en") or "UAV research direction").strip(),
                "query": search_query,
                "matched_terms": [str(term) for term in terms] if isinstance(terms, list) else [],
                "rationale_zh": str(row.get("rationale_zh") or "").strip(),
            }
        )
    if len(result) < direction_count:
        fallbacks = _fallback_directions(query, direction_count)
        result.extend(fallbacks[len(result) : direction_count])
    for index, row in enumerate(result, 1):
        row["id"] = f"D{index}"
    return result


def _fallback_directions(query: str, count: int) -> list[dict[str, Any]]:
    terms = _keywords(query) or [query.strip()]
    rows = []
    for index in range(count):
        primary = terms[index % len(terms)]
        secondary = terms[(index + 1) % len(terms)] if len(terms) > 1 else ""
        term_query = f'"{primary}"'
        if secondary and secondary != primary:
            term_query += f' AND "{secondary}"'
        rows.append(
            {
                "id": f"D{index + 1}",
                "title_zh": f"{primary} 无人机研究",
                "title_en": f"UAV research: {primary}",
                "query": f"{UAV_SCOPE} AND {term_query}",
                "matched_terms": [primary] + ([secondary] if secondary else []),
                "rationale_zh": "基于用户关键词生成的可复现检索方向。",
            }
        )
    return rows


def _fallback_analysis(query: str, paper: dict[str, Any]) -> dict[str, Any]:
    title = str(paper.get("articleTitle") or paper.get("title") or "该论文")
    return {
        "abstract_zh": "未启用 LLM，保留英文摘要供人工核验。",
        "keywords_zh": _keywords(query),
        "match_how": f"标题《{title}》由本地排序器选中；当前为 fake 分析模式。",
        "match_use": "可用于验证任务、检索、PDF 与数据库可溯源链路，不能替代正式语义分析。",
        "conclusion": "链路验证完成；启用 DeepSeek 后重新运行以获得正式结论。",
        "hits": [],
    }


def _keywords(query: str) -> list[str]:
    return [
        item for item in dict.fromkeys(re.split(r"[\s,，;；/]+", query.strip())) if len(item) >= 2
    ][:12]


def _normalize(value: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", value).casefold().split())


def _safe_page_number(value: Any) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None
