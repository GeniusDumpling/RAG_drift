"""Deterministic post-generation validation of [n] citation markers.

Parses bracketed citation markers from the generated answer and validates them
against the evidence list actually provided to the model. Results are recorded
in the generation trace; the answer text itself is left untouched.
"""

import re
from dataclasses import dataclass

# Matches "[1]", "[1, 2]", "[1][3]" style citations but not markdown links
# like "[text](url)" (bracket content must be digits separated by commas).
_CITATION_PATTERN = re.compile(r"\[(\d{1,3}(?:\s*,\s*\d{1,3})*)\]")


@dataclass(frozen=True)
class CitationCheckResult:
    cited_indices: list[int]
    out_of_range_indices: list[int]
    uncited_evidence_indices: list[int]
    marker_count: int

    @property
    def status(self) -> str:
        if self.out_of_range_indices:
            return "invalid_citations"
        if not self.cited_indices:
            return "no_citations"
        return "ok"

    def to_trace(self) -> dict:
        return {
            "status": self.status,
            "marker_count": self.marker_count,
            "cited_indices": self.cited_indices,
            "out_of_range_indices": self.out_of_range_indices,
            "uncited_evidence_indices": self.uncited_evidence_indices,
        }


def validate_citations(answer: str, evidence_count: int) -> CitationCheckResult:
    """Validate that every [n] marker in *answer* refers to provided evidence."""
    if evidence_count < 0:
        raise ValueError("evidence_count must be non-negative")

    cited: list[int] = []
    out_of_range: list[int] = []
    marker_count = 0
    for match in _CITATION_PATTERN.finditer(answer):
        for raw_number in match.group(1).split(","):
            marker_count += 1
            number = int(raw_number.strip())
            if 1 <= number <= evidence_count:
                if number not in cited:
                    cited.append(number)
            elif number not in out_of_range:
                out_of_range.append(number)

    uncited = [index for index in range(1, evidence_count + 1) if index not in cited]
    return CitationCheckResult(
        cited_indices=cited,
        out_of_range_indices=out_of_range,
        uncited_evidence_indices=uncited,
        marker_count=marker_count,
    )
