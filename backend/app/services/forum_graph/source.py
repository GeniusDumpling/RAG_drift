"""Read-only immutable topic material. Evidence anchors are raw post offsets, not embeddings."""

import html
import re
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urlsplit
from uuid import UUID

from pydantic import Field
from sqlalchemy import Column, Integer, MetaData, String, Table, Uuid, inspect, select

from app.models.content import ContentChunk, ContentItem, RawPage
from app.models.control import SourceSite
from app.services.knowledge_graph.schema import digest

from .schema import Record, Span

FORUMS = {"ardupilot", "px4"}
BASES = {"ardupilot": "https://discuss.ardupilot.org", "px4": "https://discuss.px4.io"}
SOURCE_VERSION = "forum-source-v1"


class SourceNotReady(ValueError):
    pass


class Binding(Record):
    chunk_id: str
    raw_start: int
    raw_end: int
    display_start: int
    display_text: str


class Post(Record):
    id: int
    number: int
    author: str
    author_id: str
    text: str
    version: int | None = None
    published_at: str | None = None
    updated_at: str | None = None
    reply_to: int | None = None  # Actual API reply target, never defaulted to root.
    quotes: list[dict] = Field(default_factory=list)
    raw_page_id: str
    url: str
    accepted: bool = False
    indexable: bool = False
    chunks: list[Binding] = Field(default_factory=list)
    post_type: int = 1
    available: bool = True

    @property
    def text_hash(self):
        return digest(self.text)

    @property
    def semantic(self):
        return dict(
            id=self.id,
            number=self.number,
            author=self.author_id,
            text=self.text,
            reply_to=self.reply_to,
            quotes=self.quotes,
            post_type=self.post_type,
            available=self.available,
        )


class Material(Record):
    content_item_id: str
    source_site_id: str
    forum: str
    base_url: str
    topic_id: int
    title: str
    url: str
    category_path: list[dict] = Field(default_factory=list)
    posts: list[Post]

    @property
    def external_id(self):
        return f"{urlsplit(self.base_url).netloc.lower()}:{self.topic_id}"

    @property
    def source_id(self):
        return "forum:" + self.external_id

    @property
    def semantic_hash(self):
        return digest(
            [
                SOURCE_VERSION,
                self.external_id,
                self.title,
                self.category_path,
                [p.semantic for p in self.posts],
            ]
        )

    @property
    def fingerprint(self):
        # Fetch timestamps/snapshot IDs and popularity do not invalidate a build.
        return digest(
            [
                self.semantic_hash,
                self.content_item_id,
                [
                    dict(
                        post=p.id,
                        version=p.version,
                        accepted=p.accepted,
                        updated_at=p.updated_at,
                        indexable=p.indexable,
                        chunks=[c.model_dump() for c in p.chunks],
                    )
                    for p in self.posts
                ],
            ]
        )

    def post(self, post_id):
        return next(p for p in self.posts if p.id == post_id)


def code_ranges(text):
    ranges, opened, offset = [], None, 0
    for line in text.splitlines(keepends=True):
        match = re.match(r"^ {0,3}(`{3,}|~{3,})", line)
        if match:
            if opened is None:
                opened = (offset, match[1])
            elif match[1][0] == opened[1][0] and len(match[1]) >= len(opened[1]):
                ranges.append(dict(start=opened[0], end=offset + len(line), kind="code"))
                opened = None
        offset += len(line)
    if opened:
        ranges.append(dict(start=opened[0], end=len(text), kind="code"))
    return ranges


def quote_ranges(text, topic_id):
    rows = []
    codes = code_ranges(text)
    for match in re.finditer(r"\[quote(?:=([^\]]*))?\].*?\[/quote\]", text, re.I | re.S):
        if any(c["start"] <= match.start() < c["end"] for c in codes):
            continue
        attrs = match[1] or ""
        number = re.search(r"\bpost:\s*(\d+)", attrs)
        topic = re.search(r"\btopic:\s*(\d+)", attrs)
        rows.append(
            dict(
                start=match.start(),
                end=match.end(),
                target_number=(
                    int(number[1]) if number and (not topic or int(topic[1]) == topic_id) else None
                ),
            )
        )
    for match in re.finditer(r"(?m)^[ \t]*>[^\n]*(?:\n[ \t]*>[^\n]*)*", text):
        if any(c["start"] <= match.start() < c["end"] for c in codes):
            continue
        rows.append(dict(start=match.start(), end=match.end(), target_number=None))
    return sorted(rows, key=lambda row: row["start"])


class CookedText(HTMLParser):
    """The collector's text view, retaining quote provenance from Discourse HTML."""

    def __init__(self, topic_id):
        super().__init__()
        self.parts, self.quotes, self.stack = [], [], []
        self.skip, self.topic_id = 0, topic_id

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag in {"script", "style"}:
            self.skip += 1
        if tag in {"br", "p", "div", "li", "pre"}:
            self.parts.append("\n")
        if tag == "blockquote" or (tag == "aside" and "quote" in attrs.get("class", "").split()):
            number, topic = attrs.get("data-post"), attrs.get("data-topic")
            self.stack.append(
                (
                    tag,
                    sum(map(len, self.parts)),
                    int(number)
                    if number and number.isdigit() and (not topic or topic == str(self.topic_id))
                    else None,
                )
            )

    def handle_endtag(self, tag):
        if tag in {"script", "style"}:
            self.skip = max(0, self.skip - 1)
        if tag in {"p", "div", "li", "pre"}:
            self.parts.append("\n")
        if self.stack and self.stack[-1][0] == tag:
            _, start, number = self.stack.pop()
            self.quotes.append(
                dict(start=start, end=sum(map(len, self.parts)), target_number=number)
            )

    def handle_data(self, data):
        if not self.skip:
            self.parts.append(data)

    def result(self):
        raw = "".join(self.parts)
        text = html.unescape(raw)
        trim = len(text) - len(text.lstrip())
        rows = []
        for q in self.quotes:
            start = max(0, len(html.unescape(raw[: q["start"]])) - trim)
            end = min(len(text.strip()), len(html.unescape(raw[: q["end"]])) - trim)
            if end > start:
                rows.append(dict(start=start, end=end, target_number=q["target_number"]))
        return text.strip(), rows


def raw_post_view(post, topic_id):
    if isinstance(post.get("raw"), str):
        text = post["raw"].strip()
        return text, quote_ranges(text, topic_id)
    parser = CookedText(topic_id)
    parser.feed(str(post.get("cooked") or ""))
    parser.close()
    return parser.result()


def validate_archives(session, material):
    """Check immutable snapshots too; fresh text matching cannot bless a missing archive."""
    pages = {}
    for post in material.posts:
        if post.raw_page_id not in pages:
            raw = session.get(RawPage, UUID(post.raw_page_id), populate_existing=True)
            if raw is None or str(raw.source_site_id) != material.source_site_id:
                raise SourceNotReady("证据原始快照不存在或来源错误")
            if raw.body_hash != digest(raw.raw_json) or raw.raw_json.get("complete") is not True:
                raise SourceNotReady("证据快照损坏")
            topic = raw.raw_json["topic"]
            if int(topic["id"]) != material.topic_id:
                raise SourceNotReady("证据快照不属于指定主题")
            pages[post.raw_page_id] = {int(p["id"]): p for p in topic["post_stream"]["posts"]}
        original = pages[post.raw_page_id].get(post.id)
        if original is None or raw_post_view(original, material.topic_id)[0] != post.text:
            raise SourceNotReady("证据快照原文已变化")


def validate_span(material, span, *, authored=False):
    span = Span.model_validate(span)
    try:
        post = material.post(span.post_id)
    except StopIteration as exc:
        raise ValueError("证据不属于本主题") from exc
    if not post.available:
        raise ValueError("证据来源已隐藏或删除")
    if span.end is None or post.text[span.start : span.end] != span.quote:
        matches = list(re.finditer(re.escape(span.quote), post.text))
        if len(matches) != 1:
            raise ValueError("证据无法在指定原帖唯一定位")
        span = span.model_copy(update={"start": matches[0].start(), "end": matches[0].end()})
    if authored and any(span.start < q["end"] and span.end > q["start"] for q in post.quotes):
        raise ValueError("引用他人的正文不能当作当前作者的独立发言")
    return span


def map_span(material, span):
    span = validate_span(material, span)
    result = []
    for binding in material.post(span.post_id).chunks:
        start, end = max(span.start, binding.raw_start), min(span.end, binding.raw_end)
        if start >= end:
            continue
        offset = binding.display_start + start - binding.raw_start
        quote = material.post(span.post_id).text[start:end]
        if binding.display_text[offset : offset + len(quote)] != quote:
            raise ValueError("当前 chunk 与原帖映射不符")
        result.append(
            dict(
                chunk_id=binding.chunk_id,
                char_start=offset,
                char_end=offset + len(quote),
                quote=quote,
                post_start=start,
                post_end=end,
            )
        )
    if not result or min(r["post_start"] for r in result) > span.start:
        return []
    covered = span.start
    for row in sorted(result, key=lambda r: r["post_start"]):
        if row["post_start"] > covered:
            return []
        covered = max(covered, row["post_end"])
    return result if covered >= span.end else []


def _bindings(chunks, raw, body):
    if not body.endswith(raw):
        raise SourceNotReady("逐帖正文与加工正文不一致")
    raw_offset = len(body) - len(raw)
    rows = []
    for chunk in chunks:
        meta = chunk.chunk_metadata_json
        a, b = meta.get("post_char_start"), meta.get("post_char_end")
        if a is None or b is None:
            continue
        start, end = max(a, raw_offset) - raw_offset, min(b, len(body)) - raw_offset
        if start >= end:
            continue
        needle = raw[start:end]
        # Contextual lead chunks contain generated title/summary; select only
        # a unique occurrence of the actual raw body range.
        where = [m.start() for m in re.finditer(re.escape(needle), chunk.display_text)]
        if len(where) != 1:
            continue
        rows.append(
            Binding(
                chunk_id=str(chunk.id),
                raw_start=start,
                raw_end=end,
                display_start=where[0],
                display_text=chunk.display_text,
            )
        )
    return rows


def load_material(session, content_id, *, lock=False, require_tasks=True):
    query = select(ContentItem).where(ContentItem.id == UUID(str(content_id)))
    if lock:
        query = query.with_for_update()
    item = session.scalar(query.execution_options(populate_existing=True))
    if item is None:
        return None
    meta = item.metadata_json
    if item.item_type != "thread" or meta.get("forum_item_layout") != "topic-v1":
        raise SourceNotReady("来源不是已归并的论坛主题")
    source = session.get(SourceSite, item.source_site_id)
    if (
        source.config_json.get("kind") != "discourse"
        or source.config_json.get("forum") not in FORUMS
    ):
        raise SourceNotReady("只处理 ArduPilot/PX4 论坛来源")
    if source.base_url.rstrip("/") != BASES[source.config_json["forum"]]:
        raise SourceNotReady("论坛地址不是规范来源")
    raw = session.get(RawPage, item.raw_page_id, populate_existing=True)
    if raw is None or raw.source_site_id != item.source_site_id:
        raise SourceNotReady("来源快照绑定错误")
    payload = raw.raw_json
    if raw.body_hash != digest(payload):
        raise SourceNotReady("完整主题快照校验失败")
    if payload.get("complete") is not True:
        raise SourceNotReady("只有完整主题快照可以构图")
    topic = payload["topic"]
    if int(topic["id"]) != int(meta["topic_id"]):
        raise SourceNotReady("主题 ID 与快照不符")
    if require_tasks:
        connection = session.connection()
        schema = connection.get_execution_options().get("schema_translate_map", {}).get(None)
        if not inspect(connection).has_table("forum_topic_tasks", schema=schema):
            raise SourceNotReady("缺少论坛加工状态，无法确认来源现时性")
        task = Table(
            "forum_topic_tasks",
            MetaData(),
            Column("source_site_id", Uuid()),
            Column("topic_id", Integer()),
            Column("snapshot_id", Uuid()),
            Column("process_status", String()),
        )
        row = session.execute(
            select(task.c.snapshot_id, task.c.process_status).where(
                task.c.source_site_id == item.source_site_id,
                task.c.topic_id == int(meta["topic_id"]),
            )
        ).first()
        if not row or row[0] != item.raw_page_id or row[1] != "done":
            raise SourceNotReady("最新快照尚未完成文本加工")
    api_posts = topic["post_stream"]["posts"]
    if {p["id"] for p in api_posts} != set(topic["post_stream"]["stream"]):
        raise SourceNotReady("快照缺少可见帖子正文")
    chunks = session.scalars(
        select(ContentChunk)
        .where(
            ContentChunk.content_item_id == item.id,
            ContentChunk.embed_status != "obsolete",
        )
        .order_by(ContentChunk.chunk_index)
    ).all()
    by_post = {}
    for chunk in chunks:
        if chunk.chunk_metadata_json.get("forum_item_layout") != "topic-v1":
            raise SourceNotReady("chunk 尚未迁移逐帖来源")
        by_post.setdefault(int(chunk.chunk_metadata_json["post_id"]), []).append(chunk)
    posts = []
    base = source.base_url.rstrip("/")
    for p in sorted(api_posts, key=lambda p: int(p["post_number"])):
        state = meta.get("posts", {}).get(str(p["id"]))
        if state is None or state.get("source_visibility") != "visible":
            raise SourceNotReady("逐帖处理状态与快照不符")
        text, quotes = raw_post_view(p, int(topic["id"]))
        if text != state["raw_text"]:
            raise SourceNotReady("逐帖原文与 API 快照不符")
        # Cooked-only posts use the collector's persisted text view; immutable
        # HTML snapshot and mapping version remain part of source audit.
        number = int(p["post_number"])
        if int(state["post_number"]) != number:
            raise SourceNotReady("楼层绑定错误")
        posts.append(
            Post(
                id=int(p["id"]),
                number=number,
                text=text,
                author=str(p.get("username") or "unknown"),
                author_id=str(p.get("user_id") or p.get("username") or "unknown"),
                version=p.get("version"),
                published_at=p.get("created_at"),
                updated_at=p.get("updated_at"),
                reply_to=p.get("reply_to_post_number"),
                quotes=quotes,
                raw_page_id=str(raw.id),
                url=f'{base}/t/{topic["id"]}/{number}',
                accepted=bool(p.get("accepted_answer")),
                indexable=bool(state.get("rag_eligible")),
                post_type=int(p.get("post_type") or 1),
                available=not bool(p.get("hidden") or p.get("deleted_at")),
                chunks=_bindings(by_post.get(p["id"], []), text, state["cleaned_text"]),
            )
        )
    if (
        len({p.id for p in posts}) != len(posts)
        or len({p.number for p in posts}) != len(posts)
        or not any(p.number == 1 for p in posts)
    ):
        raise SourceNotReady("主帖缺失或帖子 ID 重复")
    return Material(
        content_item_id=str(item.id),
        source_site_id=str(source.id),
        forum=source.config_json["forum"],
        base_url=base,
        topic_id=int(topic["id"]),
        title=item.title or "",
        url=item.canonical_url,
        category_path=payload.get("category_path", []),
        posts=posts,
    )


def inventory(session, *, forums=None, content_ids=None):
    query = (
        select(ContentItem.id)
        .join(SourceSite)
        .where(
            ContentItem.item_type == "thread",
            ContentItem.metadata_json["forum_item_layout"].as_string() == "topic-v1",
            SourceSite.config_json["kind"].as_string() == "discourse",
            SourceSite.config_json["forum"].as_string().in_(forums or sorted(FORUMS)),
        )
    )
    if content_ids:
        query = query.where(ContentItem.id.in_([UUID(str(v)) for v in content_ids]))
    ids = session.scalars(query.order_by(ContentItem.updated_at.desc(), ContentItem.id)).all()
    if content_ids and set(map(str, ids)) != set(map(str, content_ids)):
        raise SourceNotReady("指定来源不存在或不是受支持的论坛主题")
    return ids


def save_json(path, value):
    import json
    import os
    import tempfile

    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=path.name + ".", dir=path.parent)
    try:
        with os.fdopen(fd, "w") as stream:
            json.dump(value, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)
