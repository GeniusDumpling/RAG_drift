"""Public Discourse transport and complete topic downloads (no database imports)."""

from __future__ import annotations

import json
import random
import time
from collections.abc import Callable
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

FORUMS = {
    "ardupilot": "https://discuss.ardupilot.org",
    "px4": "https://discuss.px4.io",
}
ALIASES = {"dronecode": "px4"}
USER_AGENT = "IntelligenceRAGForumCollector/2.0 (public Discourse API)"


class DiscourseError(RuntimeError):
    def __init__(self, message: str, *, status: int | None = None, retry_after: float = 0):
        super().__init__(message)
        self.status = status
        self.retry_after = retry_after


def canonical_forums(values: list[str]) -> list[str]:
    result = []
    for value in values:
        name = ALIASES.get(value, value)
        if name not in FORUMS:
            raise ValueError(f"Unknown forum: {value}")
        if name not in result:
            result.append(name)
    return result


def iso_to_dt(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed.astimezone(UTC)
    except (ValueError, TypeError):
        return None


def retry_after_seconds(value: str | None) -> float:
    if not value:
        return 0
    try:
        return max(0, float(value))
    except ValueError:
        try:
            return max(0, (parsedate_to_datetime(value) - datetime.now(UTC)).total_seconds())
        except (ValueError, TypeError):
            return 0


class DiscourseClient:
    def __init__(
        self,
        base: str,
        *,
        delay: float = 1.0,
        timeout: float = 30,
        retries: int = 3,
        max_retry_wait: float = 60,
        opener: Callable = urlopen,
        sleep: Callable = time.sleep,
    ):
        self.base = base.rstrip("/")
        self.delay = delay
        self.timeout = timeout
        self.retries = retries
        self.max_retry_wait = max_retry_wait
        self.opener = opener
        self.sleep = sleep
        self._last_request = 0.0

    def get(self, path: str) -> dict[str, Any]:
        if not path.startswith("/") or path.startswith("//"):
            raise ValueError("API paths must be relative to the configured forum")
        for attempt in range(self.retries + 1):
            self.sleep(max(0, self.delay - (time.monotonic() - self._last_request)))
            self._last_request = time.monotonic()
            request = Request(
                self.base + path,
                headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
            )
            wait = 0.0
            status = None
            try:
                with self.opener(request, timeout=self.timeout) as response:
                    payload = json.loads(response.read().decode("utf-8"))
                if not isinstance(payload, dict) or payload.get("errors"):
                    raise DiscourseError(f"Invalid API response for {path}")
                return payload
            except HTTPError as exc:
                status = exc.code
                wait = retry_after_seconds((exc.headers or {}).get("Retry-After"))
                exc.close()
                error = DiscourseError(f"HTTP {status}: {path}", status=status, retry_after=wait)
                if status != 429 and status not in (500, 502, 503, 504):
                    raise error from exc
            except (URLError, TimeoutError, OSError, ValueError) as exc:
                reason = getattr(exc, "reason", exc)
                error = DiscourseError(
                    f"Request failed for {path}: {type(exc).__name__}: {str(reason)[:200]}"
                )
            if attempt == self.retries or wait > self.max_retry_wait:
                raise error
            self.sleep(max(wait, min(2**attempt + random.random(), self.max_retry_wait)))
        raise AssertionError("unreachable")


def fetch_topic(
    client: DiscourseClient,
    topic_id: int,
    *,
    archive: Callable[[str, dict[str, Any]], Any] | None = None,
    batch_size: int = 20,
) -> dict[str, Any]:
    """Download every currently visible ID; never treat a truncated topic as complete."""
    path = f"/t/{topic_id}.json?include_raw=true"
    topic = client.get(path)
    if archive:
        archive(path, topic)
    if int(topic.get("id") or 0) != topic_id:
        raise DiscourseError(f"Unexpected topic identity for {topic_id}")
    stream = topic.get("post_stream", {})
    ids = stream.get("stream")
    if not isinstance(ids, list) or not ids:
        raise DiscourseError(f"Missing visible post stream for topic {topic_id}")
    ids = list(dict.fromkeys(int(pid) for pid in ids))
    posts = {int(p["id"]): p for p in stream.get("posts", []) if isinstance(p, dict)}
    missing = [pid for pid in ids if pid not in posts]
    for start in range(0, len(missing), batch_size):
        batch = missing[start : start + batch_size]
        query = urlencode([("post_ids[]", pid) for pid in batch] + [("include_raw", "true")])
        path = f"/t/{topic_id}/posts.json?{query}"
        response = client.get(path)
        if archive:
            archive(path, response)
        posts.update(
            (int(p["id"]), p)
            for p in response.get("post_stream", {}).get("posts", [])
            if isinstance(p, dict)
        )
    missing = [pid for pid in ids if pid not in posts]
    if missing:
        raise DiscourseError(f"Incomplete topic {topic_id}; missing post IDs: {missing[:20]}")
    ordered = [posts[pid] for pid in ids]
    if not any(int(p.get("post_number") or 0) == 1 for p in ordered):
        raise DiscourseError(f"Topic {topic_id} has no visible root post")
    for post in ordered:
        if int(post.get("topic_id") or topic_id) != topic_id:
            raise DiscourseError(f"Foreign post in topic {topic_id}")
        if not isinstance(post.get("raw"), str) and not isinstance(post.get("cooked"), str):
            raise DiscourseError(f"Missing body for post {post['id']}")
    return {**topic, "post_stream": {"stream": ids, "posts": ordered}}
