"""Small domain vocabulary: source-only proposals, never answers or product facts."""

import json
import re
import unicodedata
from pathlib import Path

from app.schemas.video_graph import ExtractedEntity

TERMS_PATH = Path(__file__).with_suffix(".json")
TERMS = json.loads(TERMS_PATH.read_text())

ENTITY_SCOPE_RULES = (
    "\n口径：先识别对象，再判断是否有本段检索价值。具体介绍、比较或维修的型号、部件、功能、故障可保留；"
    "仅顺带点名的背景、工具、媒体、人物、笼统套装和普通操作不自动保留。"
    "尺寸/像素/帧率/焦距是属性；实体名取完整核心词，不带数值、量词和普通修饰。"
    "同一型号的不同叫法合并，不同型号的同名部件分别保留；对比中省略的部件名只在规范名中补全，"
    "原文提及仍取实际字样。词表仅作候选和拼写/类型参考，不证明型号已发布或事实成立；未知词照常识别。\n"
)


def term_key(value):
    return "".join(unicodedata.normalize("NFKC", value).casefold().split())


def source_terms(text, *, limit=24):
    """Prefer complete names; do not match a model prefix inside another generation/variant."""
    matches = []
    for term in TERMS:
        for alias in [term["name"], *term["aliases"]]:
            pattern = r"\s*".join(re.escape(word) for word in alias.split())
            suffix = r"(?![a-zA-Z0-9])"
            if term["type"] == "product":
                suffix += r"(?!\s*\d)"
                suffix += r"(?!\s*(?:Pro|Lite|Classic|Cine|Enterprise|Zoom|SE)\b)"
                suffix += r"(?!\s*(?:系列|series\b))"
            for match in re.finditer(r"(?<![a-zA-Z0-9])" + pattern + suffix, text, re.I):
                matches.append((match.start(), match.end(), term, match.group()))
    matches.sort(key=lambda row: (-(row[1] - row[0]), row[0], row[2]["name"]))
    selected, seen = [], set()
    for start, end, term, surface in matches:
        identity = (term["name"], term["type"])
        if identity in seen or any(start < b and a < end and (
                                      term["type"] == old["type"]
                                      or (term["type"] == "organization"
                                          and old["type"] in {"product", "product_family"}))
                                   for a, b, old, _ in selected):
            continue
        seen.add(identity)
        selected.append((start, end, term, surface))
    selected.sort(key=lambda row: row[0])
    return [{"surface_text": surface, "canonical_name": term["name"], "entity_type": term["type"]}
            for _, _, term, surface in selected[:limit]]


def seed_term_mentions(candidate, text):
    """A dictionary match only proposes a candidate; the normal independent review decides."""
    entities = list(candidate.entities)
    seen = {(term_key(e.surface_text), e.entity_type) for e in entities}
    keys = {e.key for e in entities}
    for index, row in enumerate(source_terms(text)):
        signature = (term_key(row["surface_text"]), row["entity_type"])
        if len(entities) == 60:
            break
        if signature in seen:
            continue
        key = f"term_{index}"
        while key in keys:
            key += "_"
        entities.append(ExtractedEntity(key=key, surface_text=row["surface_text"],
                                        entity_type=row["entity_type"]))
        keys.add(key)
        seen.add(signature)
    return candidate.model_copy(update={"entities": entities})


def canonical_term(value, kind):
    """Exact spelling aliases only; do not manufacture a missing product brand."""
    for term in TERMS:
        if term["type"] != kind:
            continue
        if any(term_key(value) == term_key(alias) for alias in [term["name"], *term["aliases"]]):
            if term["name"] == "firmware":
                return value
            if term["name"].startswith("DJI ") and not value.casefold().startswith("dji "):
                return value
            return term["name"]
    return value


def confirmed_type(surface, proposed):
    """User-confirmed whole-device/firmware types; never a substring-based reclassification."""
    value = re.sub(r"^(?:the|a|an|this|that)\s+", "", surface.strip(), flags=re.I)
    if term_key(value) in {"firmware", "固件", "韌體"}:
        return "software"
    if term_key(value) in {"remotecontroller", "remotecontrol", "motioncontroller", "goggles",
                           "遥控器", "遙控器", "飞行眼镜", "飛行眼鏡", "护目镜", "護目鏡"}:
        return "product"
    name = re.sub(r"\s*(?:遥控器|遙控器|remote controller|controller)$", "", value, flags=re.I)
    for term in TERMS:
        if (term["type"] == "product" and re.match(r"^(?:RC\b|RC-|Goggles\b)", term["name"])
            and any(term_key(name) == term_key(alias)
                    for alias in [term["name"], *term["aliases"]])):
            return "product"
    return proposed
