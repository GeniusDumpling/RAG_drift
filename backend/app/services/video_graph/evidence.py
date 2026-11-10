"""Source evidence presentation shared by graph browsing and retrieval."""

import json
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from app.schemas.video_graph import GraphEvidenceRead


def timestamp_url(url: str, seconds: float | None) -> str:
    parsed = urlsplit(url)
    if seconds is None or parsed.hostname not in {
        "youtube.com",
        "www.youtube.com",
        "m.youtube.com",
        "youtu.be",
    }:
        return url
    params = [(k, v) for k, v in parse_qsl(parsed.query) if k not in {"t", "start"}]
    params.append(("t", str(int(seconds))))
    return urlunsplit(
        (parsed.scheme, parsed.netloc, parsed.path, urlencode(params), parsed.fragment)
    )


def evidence_context(entry: GraphEvidenceRead, index: int) -> str:
    conditions = (
        "\n适用条件: " + json.dumps(entry.qualifiers, ensure_ascii=False)
        if entry.qualifiers
        else ""
    )
    return (
        f"[{index}] {entry.title or '视频'}\nURL: {entry.video_url}\n"
        f"陈述状态: {entry.assertion}（字幕陈述，未独立事实验证）"
        f"{conditions}\n原文: {entry.quote}"
    )
