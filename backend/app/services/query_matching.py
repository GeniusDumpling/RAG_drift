"""Deterministic lexical matching shared by SQL candidate ranking and Python scoring."""

import re
from collections.abc import Sequence

_VARIANTS = "pro|cine|classic|enterprise|advanced|plus|max|ultra|mini|air|rtk"
_MODEL_RE = re.compile(
    rf"(?<![a-z0-9])(?:[a-z][a-z-]*(?:\s+(?:{_VARIANTS}))?\s+\d[a-z0-9]*"
    rf"(?:[.-][a-z0-9]+)*|[a-z][a-z-]*\d[a-z0-9]*(?:[.-][a-z0-9]+)*)"
    rf"(?:\s+(?:{_VARIANTS}))*(?![a-z0-9])",
    re.IGNORECASE,
)
_TOKENS = re.compile(r"[a-z0-9]+(?:[.-][a-z0-9]+)*|[\u3400-\u9fff]+", re.IGNORECASE)
_WEAK_TERMS = frozenset(_VARIANTS.split("|"))
_STOP_WORDS = frozenset(
    (
        "how do i the this is for a an to of about tell me please introduce explain what which "
        "are does can in on with and or its it "
        "介绍一下 介绍 请 请问 一下 有什么 哪些 怎么 如何 它 呢"
    ).split()
)


def model_phrases(text: str) -> list[str]:
    """Keep model numbers with their family and variant, including in Chinese questions."""
    return list(dict.fromkeys(
        " ".join(m.group().casefold().split()) for m in _MODEL_RE.finditer(text)
    ))


def lexical_terms(keyword_terms: Sequence[str], raw_query: str) -> list[str]:
    candidates = model_phrases(raw_query)
    for term in keyword_terms:
        tokens = _TOKENS.findall(term)
        if len(tokens) > 1 and " " in term.strip():
            candidates.append(" ".join(tokens))
        candidates.extend(tokens)
    candidates.extend(_TOKENS.findall(raw_query))
    normalized = (" ".join(t.casefold().split()) for t in candidates)
    return list(dict.fromkeys(t for t in normalized if t and t not in _STOP_WORDS))


def term_pattern(term: str) -> str:
    # ASCII word boundaries also work next to Chinese text. PostgreSQL and Python
    # both understand this pattern, so SQL preselection cannot disagree with scoring.
    body = r"\s+".join(re.escape(part) for part in term.split())
    if re.search(r"[a-z0-9]", term, re.IGNORECASE):
        return rf"(^|[^a-z0-9]){body}([^a-z0-9]|$)"
    return body


def matches_term(text: str | None, term: str) -> bool:
    return bool(text and re.search(term_pattern(term), text, re.IGNORECASE))


def term_weights(terms: Sequence[str]) -> list[float]:
    phrases = {phrase for term in terms for phrase in model_phrases(term)}
    return [
        3.0 if term in phrases else
        0.05 if term.isdecimal() and len(terms) > 1 else
        0.15 if term in _WEAK_TERMS else 1.0
        for term in terms
    ]
