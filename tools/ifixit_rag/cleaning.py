from __future__ import annotations

import re
from html import unescape
from html.parser import HTMLParser
from typing import Any

_MARKUP = re.compile(r"\[([a-zA-Z_]+)\|([^\[\]]*)\]")
_URL = re.compile(r"https?://[^\s<>\[\]\"']+")
_VIDEO_HOST = re.compile(r"(?:youtu\.be|youtube\.com|vimeo\.com)", re.I)
_HTML_TAGS = {
    "p",
    "div",
    "a",
    "br",
    "span",
    "strong",
    "em",
    "b",
    "i",
    "pre",
    "code",
    "li",
    "ul",
    "ol",
    "script",
    "style",
    "h1",
    "h2",
    "h3",
}

# Retrieval vocabulary only; these terms are not translations of source evidence.
TERMS = {
    "gimbal": "云台",
    "gps": "GPS 定位模块",
    "propeller": "螺旋桨",
    "battery": "电池",
    "motor": "电机",
    "motherboard": "主板",
    "mainboard": "主板",
    "main board": "主板",
    "camera": "相机",
    "esc": "电子调速器 电调",
    "electronic speed controller": "电子调速器 电调",
    "remote controller": "遥控器",
    "landing gear": "起落架",
    "arm": "机臂",
    "ribbon cable": "排线",
    "calibrate": "校准",
    "calibration": "校准",
    "replacement": "更换",
    "disassembly": "拆卸",
    "teardown": "拆解",
}


class _TextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=False)
        self.parts: list[str] = []
        self.suppressed = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in {"script", "style"}:
            self.suppressed += 1
        if tag in {"br", "p", "div", "li", "pre", "h1", "h2", "h3"}:
            self.parts.append("\n")
        if tag == "a" and not self.suppressed:
            for key, value in attrs:
                if key == "href" and value:
                    self.parts.append(f" ({value}) ")
        if tag not in _HTML_TAGS and not self.suppressed:
            self.parts.append(self.get_starttag_text())

    def handle_endtag(self, tag: str) -> None:
        if tag in {"script", "style"}:
            self.suppressed = max(0, self.suppressed - 1)
        if tag in {"p", "div", "li", "pre", "h1", "h2", "h3"}:
            self.parts.append("\n")
        if tag not in _HTML_TAGS and not self.suppressed:
            self.parts.append(f"</{tag}>")

    def handle_data(self, data: str) -> None:
        if not self.suppressed:
            self.parts.append(data)

    def handle_entityref(self, name: str) -> None:
        self.handle_data(f"&{name};")

    def handle_charref(self, name: str) -> None:
        self.handle_data(f"&#{name};")


def clean_text(value: str | None) -> tuple[str, list[dict[str, Any]], list[str]]:
    refs: list[dict[str, Any]] = []
    warnings: list[str] = []

    def replace(match: re.Match[str]) -> str:
        kind, rest = match.groups()
        parts = rest.split("|")
        if kind == "product" and len(parts) >= 2:
            refs.append({"type": kind, "product_id": parts[0], "label": parts[1]})
            return parts[1]
        if kind == "link" and len(parts) >= 2:
            refs.append({"type": kind, "url": parts[0], "label": parts[1]})
            return f"{parts[1]} ({parts[0]})"
        if kind == "video" and parts[0].startswith(("http://", "https://")):
            refs.append({"type": "video", "url": parts[0]})
            return ""  # A URL alone is not a transcript or repair evidence.
        if kind == "guide" and len(parts) >= 2:
            refs.append({"type": kind, "guide_id": parts[0], "label": parts[1]})
            return parts[1]
        warnings.append("unknown_markup")
        return match.group(0)

    text = _MARKUP.sub(replace, value or "")
    # Only parse HTML-looking tags; e.g. command arguments '<port>' must survive.
    if re.search(r"</?(?:" + "|".join(sorted(_HTML_TAGS)) + r")\b", text):
        parser = _TextExtractor()
        parser.feed(text)
        text = "".join(parser.parts)
    text = unescape(text)
    text = re.sub(r"(\*{2,3}|'{2})(.+?)\1", r"\2", text)
    text = re.sub(r"(?m)^={2,}\s*(.*?)\s*={2,}$", r"\1", text)
    text = text.replace("\r\n", "\n").replace("\r", "\n").replace("\xa0", " ")
    text = "\n".join(line.rstrip() for line in text.splitlines())
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    for url in _URL.findall(text):
        if _VIDEO_HOST.search(url) and not any(r.get("url") == url for r in refs):
            refs.append({"type": "video", "url": url.rstrip(".,)")})
    return text, refs, sorted(set(warnings))


def retrieval_terms(text: str) -> list[str]:
    return sorted(
        {
            translation
            for term, translation in TERMS.items()
            if re.search(r"\b" + re.escape(term) + r"s?\b", text, re.I)
        }
    )


def device_key(value: str) -> str:
    return " ".join(value.split()).casefold()
