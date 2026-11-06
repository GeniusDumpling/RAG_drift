"""Provider-reported usage only; missing usage is unknown, never zero-cost."""

import logging
from collections import Counter

logger = logging.getLogger(__name__)


def request_stage(phase, payload):
    if phase == "review" and "entities" in payload:
        return "relation_review" if payload.get("relations") else "entity_review"
    if phase == "extraction":
        return "assessment" if "transcript_sample" in payload else "entity_extraction"
    return phase


class TokenUsage:
    def __init__(self):
        self.phases = {}

    def attempt(self, phase):
        self.phases.setdefault(phase, Counter())["http_attempts"] += 1

    def replay(self, phase):
        self.phases.setdefault(phase, Counter())["checkpoint_replays"] += 1

    def record(self, phase, usage):
        row = self.phases.setdefault(phase, Counter())
        row["http_responses"] += 1
        if not isinstance(usage, dict) or any(
            type(usage.get(k)) is not int or usage[k] < 0
            for k in ("prompt_tokens", "completion_tokens", "total_tokens")
        ):
            row["unknown_usage_responses"] += 1
            return
        values = {k: usage[k] for k in ("prompt_tokens", "completion_tokens", "total_tokens")}
        details = usage.get("prompt_tokens_details") or {}
        cached = details.get("cached_tokens") if isinstance(details, dict) else None
        if type(cached) is int and 0 <= cached <= usage["prompt_tokens"]:
            values["cached_tokens"] = cached
            values["uncached_prompt_tokens"] = usage["prompt_tokens"] - cached
            values["cache_measured_responses"] = 1
        details = usage.get("completion_tokens_details") or {}
        reasoning = details.get("reasoning_tokens") if isinstance(details, dict) else None
        if type(reasoning) is int and reasoning >= 0:
            values["reasoning_tokens"] = reasoning
        row.update(values)
        row["measured_responses"] += 1
        # No source text, provider URL, credential or generated answer in telemetry.
        logger.info("video_graph_usage phase=%s usage=%s", phase, values)

    def snapshot(self):
        return {phase: dict(row) for phase, row in self.phases.items()}


def usage_delta(before, after):
    return {
        phase: {key: value - before.get(phase, {}).get(key, 0) for key, value in row.items()}
        for phase, row in after.items()
    }


def merge_usage(first, second):
    phases = set(first) | set(second)
    return {phase: {
        key: first.get(phase, {}).get(key, 0) + second.get(phase, {}).get(key, 0)
        for key in sorted(set(first.get(phase, {})) | set(second.get(phase, {})))
    } for phase in sorted(phases)}
