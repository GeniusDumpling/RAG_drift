"""Bounded, completely covered post windows and explicit parent/quote dependencies."""

import re

from pydantic import Field

from app.services.knowledge_graph.schema import digest

from .schema import Assessment, Record
from .source import code_ranges


class Window(Record):
    id: str
    post_id: int
    start: int
    end: int
    text: str
    context: list[dict] = Field(default_factory=list)
    context_incomplete: bool = False

    def payload(self, material):
        post = material.post(self.post_id)
        return dict(
            source=material.external_id,
            title=material.title,
            category_path=material.category_path,
            current=dict(
                post_id=post.id,
                post_number=post.number,
                author=post.author_id,
                start=self.start,
                end=self.end,
                text=self.text,
                quoted_ranges=[
                    q for q in post.quotes if q["start"] < self.end and q["end"] > self.start
                ],
                blocks=[
                    dict(
                        start=max(b["start"], self.start),
                        end=min(b["end"], self.end),
                        kind=b["kind"],
                        continued=b["start"] < self.start or b["end"] > self.end,
                    )
                    for b in code_ranges(post.text)
                    if b["start"] < self.end and b["end"] > self.start
                ],
            ),
            context=self.context,
            context_incomplete=self.context_incomplete,
        )

    def fingerprint(self, material):
        return digest(self.payload(material))


def make_windows(material, *, max_chars=4000, context_chars=12000):
    if max_chars < 100 or context_chars < 100:
        raise ValueError("讨论窗口预算过小")
    by_number = {p.number: p for p in material.posts}
    root = by_number[1]
    result = []
    for post in material.posts:
        parents, seen = [], {post.id}
        targets = ([post.reply_to] if post.reply_to else []) + [
            q["target_number"] for q in post.quotes if q["target_number"]
        ]
        targets.append(1)
        # Follow the actual reply chain, not arbitrary neighboring messages.
        for number in targets:
            parent = by_number.get(number)
            while parent and parent.id not in seen:
                seen.add(parent.id)
                parents.append(parent)
                parent = by_number.get(parent.reply_to) if parent.reply_to else None
        if post.id != root.id and root not in parents:
            parents.append(root)
        if root in parents:
            parents.remove(root)
            parents.insert(0, root)
        spans = code_ranges(post.text) + post.quotes
        start = 0
        while start < len(post.text) or (start == 0 and not post.text):
            end = min(start + max_chars, len(post.text))
            if end < len(post.text):
                protected = next((b for b in spans if b["start"] < end < b["end"]), None)
                if (
                    protected
                    and start < protected["start"]
                    and protected["end"] - protected["start"] <= max_chars
                ):
                    end = protected["start"]
                elif protected is None:
                    breaks = [m.end() for m in re.finditer(r"\n\s*\n", post.text[start:end])]
                    if breaks and breaks[-1] >= max_chars // 3:
                        end = start + breaks[-1]
            context, remaining, incomplete = [], context_chars, False
            for parent in parents:
                if remaining <= 0:
                    incomplete = True
                    break
                allowance = (
                    min(remaining, context_chars // 2)
                    if parent.id == root.id and len(parents) > 1
                    else remaining
                )
                text = parent.text[: max(0, allowance)]
                context.append(
                    dict(
                        post_id=parent.id,
                        post_number=parent.number,
                        author=parent.author_id,
                        text=text,
                        start=0,
                        end=len(text),
                        text_hash=parent.text_hash,
                        reply_to=parent.reply_to,
                        quoted_ranges=[q for q in parent.quotes if q["start"] < len(text)],
                        truncated=len(text) != len(parent.text),
                    )
                )
                remaining -= len(text)
                incomplete |= len(text) != len(parent.text)
            text = post.text[start:end]
            result.append(
                Window(
                    id=digest([post.id, start, text]),
                    post_id=post.id,
                    start=start,
                    end=end,
                    text=text,
                    context=context,
                    context_incomplete=incomplete,
                )
            )
            if end == len(post.text):
                break
            start = end
    return result


def in_window(window, span):
    if span.post_id == window.post_id:
        return window.start <= span.start < span.end <= window.end
    return any(
        c["post_id"] == span.post_id and c["start"] <= span.start < span.end <= c["end"]
        for c in window.context
    )


def prefilter(material, window):
    """Only obvious non-content. Mixed technical/promotion is left to assessment."""
    post = material.post(window.post_id)
    text = window.text.strip()
    if post.post_type != 1 or not post.available or not text:
        return Assessment(
            uav_technical_related=None,
            has_graph_value=False,
            decision="skip",
            reason_code="no_usable_information",
            reason="空白或系统发言",
        )
    if re.fullmatch(
        r"(?i)(?:thanks?(?: you)?|thank you|bump|following|谢谢|感谢|顶)[!！.,。\s]*", text
    ):
        return Assessment(
            uav_technical_related=None,
            has_graph_value=False,
            decision="skip",
            reason_code="no_usable_information",
            reason="纯感谢或顶帖，无新增信息",
        )
    if any(window.start >= q["start"] and window.end <= q["end"] for q in post.quotes):
        return Assessment(
            uav_technical_related=None,
            has_graph_value=False,
            decision="skip",
            reason_code="duplicate",
            reason="窗口仅包含引用，不是新的独立证据",
        )
    without_media = re.sub(r"!\[[^]]*\]\([^)]*\)|https?://\S+", "", text).strip()
    if not without_media or re.fullmatch(r"\[[^]]*\]\([^)]*\)", text):
        return Assessment(
            uav_technical_related=None,
            has_graph_value=None,
            decision="defer",
            reason_code="unparsed_attachment",
            reason="只有尚未解析的附件或外部链接",
        )
    return None


def aggregate(windows, assessments):
    expected = {w.id for w in windows}
    if set(assessments) != expected:
        raise ValueError("前置判断未覆盖全部窗口，不能跳过主题")
    values = [Assessment.model_validate(assessments[w.id]) for w in windows]
    decision = (
        "extract"
        if any(v.decision == "extract" for v in values)
        else "defer"
        if any(v.decision == "defer" for v in values)
        else "skip"
    )
    return dict(
        decision=decision,
        coverage=dict(complete=True, windows=sorted(expected)),
        extract_windows=[
            w.id for w, v in zip(windows, values, strict=True) if v.decision == "extract"
        ],
        deferred_windows=[
            w.id for w, v in zip(windows, values, strict=True) if v.decision == "defer"
        ],
    )
