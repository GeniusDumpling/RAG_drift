"""Parse verification responses while keeping every quote tied to a cited document."""

import json
import re
from urllib.parse import urldefrag

from llm_supplier_analysis import normalized_quote

FIELD_NAMES = {
    "验证结论": "verdict",
    "可信度": "confidence",
    "供应内容/模块": "supply",
    "机型": "models",
    "证据年份": "evidence_years",
    "关系类型": "relation_type",
    "依据（引用原文）": "quote",
    "依据(引用原文)": "quote",
    "来源URL": "url",
    "来源（网页原始URL）": "url",
}


def source_urls(value):
    """Separate adjacent URLs, including Chinese separators, without changing URL paths."""
    values = value if isinstance(value, list) else [value]
    found = []
    for item in values:
        for match in re.findall(r'https?://[^\s<>`\[\]"“”；;，、]+', str(item)):
            url = urldefrag(match.rstrip(").,;:：。"))[0]
            if url not in found:
                found.append(url)
    return found


def parse_response(raw):
    value = raw.strip()
    if value.startswith("```"):
        value = re.sub(r"^```(?:json|markdown)?\s*\n?", "", value, flags=re.I)
        value = re.sub(r"\n?```\s*$", "", value)
    if value.startswith("{"):
        parsed = json.loads(value)
        evidence = parsed.get("evidence", [])
        if not isinstance(evidence, list) or not all(isinstance(e, dict) for e in evidence):
            raise ValueError("Evidence must be a list of source/quote objects")
        parsed["candidates"] = [
            {"urls": source_urls(e.get("url", "")), "quote": e.get("quote", ""), "strict": True}
            for e in evidence
        ]
        return parsed

    # A field ends at the next recognized header, not at a newline within its value.
    names = "|".join(re.escape(name) for name in FIELD_NAMES)
    header = re.compile(
        rf"^\s*(?:[-*+]\s+|\d+[.)、]\s*)?(?:\*\*)?({names})(?:\*\*)?\s*[：:]\s*(?:\*\*)?[ \t]*",
        flags=re.M,
    )
    matches = list(header.finditer(value))
    parsed, fields = {}, []
    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(value)
        field = FIELD_NAMES[match.group(1)]
        content = value[match.end() : end].strip()
        if field == "quote":
            content = re.sub(r"^\s*>[ \t]?", "", content, flags=re.M)
        fields.append((field, content))
        parsed[field] = content
    candidates, pending = [], None
    for field, content in fields:
        if field == "quote":
            if pending is not None:
                raise ValueError("Evidence quote has no corresponding source URL")
            pending = content
        elif field == "url" and pending is not None:
            candidates.append({"urls": source_urls(content), "quote": pending, "strict": False})
            pending = None
    if pending is not None:
        candidates.append(
            {"urls": source_urls(parsed.get("url", "")), "quote": pending, "strict": False}
        )
    parsed["candidates"] = candidates
    return parsed


def match_evidence(candidates, docs):
    """Require every supplied quote to occur in one of its explicitly cited source texts."""
    matched = []
    for candidate in candidates:
        urls = candidate["urls"]
        if candidate["strict"] and len(urls) != 1:
            raise ValueError("Each structured evidence item must cite exactly one source URL")
        raw_quote = candidate["quote"]
        if not isinstance(raw_quote, str) or not raw_quote.strip():
            raise ValueError("Evidence quote is empty")
        quote = raw_quote.strip().strip('“”"')
        if not normalized_quote(quote):
            raise ValueError("Evidence quote is empty")
        sources = [d for d in docs if urldefrag(d["url"])[0] in urls]
        direct = next(
            (d for d in sources if normalized_quote(quote) in normalized_quote(d["body"])), None
        )
        if direct:
            matched.append({"url": direct["url"], "quote": quote})
            continue
        # Legacy prose may wrap several independent verbatim quotes in one field.
        parts = re.findall(r'“([^“”]+)”|"([^"\n]+)"', raw_quote) if not candidate["strict"] else []
        quotes = [left or right for left, right in parts]
        if not quotes or any(len(normalized_quote(q)) < 8 for q in quotes):
            raise ValueError("LLM verdict has no matching source URL and verbatim quote")
        for part in quotes:
            doc = next(
                (d for d in sources if normalized_quote(part) in normalized_quote(d["body"])), None
            )
            if doc is None:
                raise ValueError("LLM verdict has no matching source URL and verbatim quote")
            matched.append({"url": doc["url"], "quote": part})
    return matched
