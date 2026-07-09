from __future__ import annotations

import re
from typing import Any, TypedDict

VENUE_TIER = {
    "transactions": 100,
    "proceedings of the ieee": 95,
    "journal": 80,
    "letters": 75,
    "magazine": 60,
    "access": 50,
    "conference": 30,
    "symposium": 30,
    "workshop": 20,
}


class RankedPaper(TypedDict):
    record: dict[str, Any]
    candidate_rank: int
    selected_rank: int
    score: int
    score_detail: dict[str, int]


def pick_top(
    records: list[dict[str, Any]],
    *,
    keywords: list[str],
    year_from: int,
    top_n: int,
) -> list[RankedPaper]:
    scored: list[RankedPaper] = []
    for candidate_rank, record in enumerate(records, 1):
        score, detail = score_record(record, keywords)
        scored.append(
            {
                "record": record,
                "candidate_rank": candidate_rank,
                "selected_rank": 0,
                "score": score,
                "score_detail": detail,
            }
        )
    recent = [row for row in scored if row["score_detail"]["year"] >= year_from]
    pool = recent or scored
    pool.sort(key=lambda row: int(row["score"]), reverse=True)
    selected = pool[:top_n]
    for selected_rank, row in enumerate(selected, 1):
        row["selected_rank"] = selected_rank
    return selected


def score_record(record: dict[str, Any], keywords: list[str]) -> tuple[int, dict[str, int]]:
    publication = str(
        record.get("publicationTitle") or record.get("publisher") or record.get("contentType") or ""
    ).casefold()
    venue = max((value for key, value in VENUE_TIER.items() if key in publication), default=10)
    year = _year(record.get("publicationYear") or record.get("publicationDate"))
    citations = min(max(_integer(record.get("citationCount")), 0), 9999)
    haystack = " ".join(
        [str(record.get("articleTitle") or ""), str(record.get("abstract") or "")]
    ).casefold()
    keyword_hits = sum(1 for keyword in keywords if keyword.casefold() in haystack)
    score = venue * 1_000_000_000 + year * 100_000 + citations * 10 + keyword_hits
    return score, {
        "venue_tier": venue,
        "year": year,
        "citation_count": citations,
        "keyword_hits": keyword_hits,
    }


def _year(value: Any) -> int:
    match = re.search(r"\b(19|20)\d{2}\b", str(value or ""))
    return int(match.group(0)) if match else 0


def _integer(value: Any) -> int:
    try:
        return int(str(value or 0).replace(",", ""))
    except ValueError:
        return 0
