"""Source-grounded entity identities; contextual mentions never become global aliases."""

import hashlib
import ipaddress
import json
import re
import uuid
from dataclasses import dataclass
from functools import lru_cache
from importlib.metadata import version
from pathlib import Path
from typing import Any

from app.schemas.video_graph import ExtractedEntity
from app.services.video_graph_semantics import normalized_name

ENTITY_NAMESPACE = uuid.UUID("a37984c5-3e93-43a7-8051-2503bfc69e68")
QUALITY_VERSION = "video-graph-v2"
GENERIC_NAMES = frozenset(
    "drone|drones|aircraft|the drone|this drone|it|they|them|the app|app|camera|receiver|"
    "transmitter|motor|motors|battery|batteries|gimbal|propeller|propellers|screws|screen|"
    "remote|remote control|remote controller|flight controller|computer|smartphone|"
    "controller|gps module|button|buttons|power button|led|leds|sensor|antenna|antennas|"
    "joystick|joysticks|camera cover|gimbal protector|cable|usb cable|usb-c cable|"
    "无人机|無人機|飞机|飛機|电池|電池|相机|相機|云台|雲台|"
    "摄像头|螺旋桨|螺旋槳|电机|電機|飞控|飛控|遥控器|遙控器|它|他|这个|這個|"
    "设备|設備|公司|the company|the aircraft|the camera|army|military|formation|"
    "anti-drone formation|counter-uav formation|反无人机方队|反無人機方隊|"
    "军队|軍隊|部队|部隊".split("|")
)
NAME_ALIASES = {
    ("organization", "大疆"): "DJI",
    ("organization", "大疆创新"): "DJI",
    ("organization", "大疆創新"): "DJI",
    ("software", "dji fly app"): "DJI Fly",
    ("software", "dji fly"): "DJI Fly",
    ("software", "betaflight"): "Betaflight",
    ("software", "ardupilot"): "ArduPilot",
    ("software", "px4"): "PX4",
    ("protocol", "mavlink"): "MAVLink",
    ("technology", "gps"): "GPS",
    ("technology", "gnss"): "GNSS",
    ("technology", "rtk"): "RTK",
    ("technology", "slam"): "SLAM",
}
for _names, _canonical in (
    ("中国|中國|中华人民共和国|中華人民共和國|china", "中国"),
    ("美国|美國|美利坚合众国|united states|united states of america|usa", "美国"),
    ("英国|英國|united kingdom", "英国"),
    ("乌克兰|烏克蘭|ukraine|україна|украина", "乌克兰"),
    ("俄罗斯|俄羅斯|russia|russian federation|россия", "俄罗斯"),
    ("法国|法國|france", "法国"),
    ("德国|德國|germany", "德国"),
    ("日本|japan", "日本"),
    ("加拿大|canada", "加拿大"),
    ("伊朗|iran", "伊朗"),
):
    for _name in _names.split("|"):
        NAME_ALIASES["country", _name] = _canonical


def stable_hash(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def is_quality_version(version: str) -> bool:
    match = re.match(r"^video-graph-v(\d+)(?:\D|$)", version.split(":", 1)[0])
    return match is not None and int(match[1]) >= 2


@lru_cache(maxsize=1)
def policy_id() -> str:
    """Invalidate cached builds when executable extraction/identity/schema rules change."""
    here = Path(__file__).resolve().parent
    paths = [
        here / name
        for name in (
            "video_graph.py",
            "video_graph_identity.py",
            "video_graph_quality.py",
            "video_graph_semantics.py",
            "video_graph_grounding.py",
            "video_graph_terms.py",
            "video_graph_terms.json",
            "video_graph_normalization.py",
            "video_graph_usage.py",
            "video_graph_request_checkpoint.py",
        )
    ]
    paths.append(here.parent / "schemas" / "video_graph.py")
    dependency = version("opencc-python-reimplemented").encode()
    payload = b"\n".join([*(p.read_bytes() for p in paths), dependency])
    return hashlib.sha256(payload).hexdigest()[:12]


def extraction_version(
    base: str, model: str, effort: str = "none", extraction_effort: str = "none"
) -> str:
    if base != "video-graph-v1" and not is_quality_version(base):
        raise ValueError("抽取版本须为 video-graph-v2 或更高版本，可追加规则修订后缀")
    version = f"{base}:{model}"
    if is_quality_version(base):
        version += f":{policy_id()}-{extraction_effort}-{effort}"
    if len(version) > 160:
        raise ValueError("抽取版本和模型名称合计不能超过 160 个字符")
    return version


def compact_name(value: str) -> str:
    return re.sub(r"[\W_]+", "", normalized_name(value))


def identity_name(value: str, kind: str, context: str = "") -> str:
    from app.services.video_graph_terms import canonical_term

    cleaned = " ".join(value.split())
    if kind == "component":
        axes = {"one": "1", "two": "2", "three": "3", "four": "4", "five": "5", "six": "6"}
        cleaned = re.sub(
            r"\b(one|two|three|four|five|six)[ -]*axis\b",
            lambda match: axes[match[1].casefold()] + "-axis", cleaned, flags=re.I,
        )
        cleaned = re.sub(r"\b(\d+)\s*[- ]?\s*(?:in|inch|inches)\b", r"\1-inch", cleaned, flags=re.I)
    if kind in {"product", "component", "software", "technology", "method", "fault", "protocol"}:
        cleaned = re.sub(r"^(?:the|a|an|this|that)\s+", "", cleaned, flags=re.I)
    if kind == "fault":
        cleaned = re.sub(r"\s+(?:anymore|currently|now)$", "", cleaned, flags=re.I)
    if kind == "product":
        cleaned = re.sub(r"^(?:大疆(?:创新|創新)?|dji(?:['’]s)?)\s*", "DJI ", cleaned, flags=re.I)
        base = re.sub(r"\s+(?:air unit|drone|aircraft|controller)$", "", cleaned, flags=re.I)
        model = re.fullmatch(
            r"(?:DJI\s+)?(?:O\s*\d+\s*(?:Lite|Pro)?|RC[\s\w-]*\d[\w-]*|"
            r"(?:Neo|Mini|Air|Mavic|Avata|Matrice|Inspire|Phantom|Dock)"
            r"\s*\d*(?:\s*(?:S|K|Pro|Classic|Cine|Enterprise|Zoom|SE|M|T))*)", base, re.I,
        )
        if base != cleaned and model:
            cleaned = base
        for word, digit in (
            ("one", "1"),
            ("two", "2"),
            ("three", "3"),
            ("four", "4"),
            ("five", "5"),
            ("six", "6"),
            ("seven", "7"),
            ("eight", "8"),
            ("nine", "9"),
            ("ten", "10"),
        ):
            cleaned = re.sub(rf"\b{word}\b", digit, cleaned, flags=re.I)
        air_unit = re.fullmatch(r"(?:DJI\s+)?O\s*(\d+)\s*(Lite|Pro)?", cleaned, re.I)
        if air_unit and re.search(r"DJI|大疆", context + " " + cleaned, re.I):
            return f"DJI O{air_unit[1]}" + (f" {air_unit[2].title()}" if air_unit[2] else "")
    alias = NAME_ALIASES.get((kind, normalized_name(cleaned)))
    if alias:
        return alias
    # Product generations stay in the key; Neo and Neo 2 must never collapse.
    match = re.fullmatch(
        r"(?:the\s+|original\s+)?(?:dji\s+|大疆\s*)?"
        r"(neo|mini|air|mavic|avata|matrice|inspire|phantom|dock)"
        r"\s*(\d*(?:\s*(?:s|k|pro|classic|cine|enterprise|zoom|se|m|t))*)",
        cleaned,
        re.IGNORECASE,
    )
    if kind == "product" and match and re.search(r"DJI|大疆", context + " " + cleaned, re.I):
        family = match[1].capitalize()
        suffix = re.sub(r"\s+", "", match[2]).upper()
        for token in ("ENTERPRISE", "CLASSIC", "CINE", "ZOOM", "PRO", "SE"):
            suffix = suffix.replace(token, " " + token.title())
        suffix = suffix.strip()
        return f"DJI {family}" + (f" {suffix}" if suffix else "")
    return canonical_term(cleaned, kind)


def is_generic(value: str) -> bool:
    return normalized_name(value) in GENERIC_NAMES


def model_vector(value: str) -> tuple[str, str] | None:
    """Recognize complete model names only; numbers from neighboring prose are not generations."""
    normalized = identity_name(value, "product", "DJI")
    match = re.fullmatch(
        r"DJI (Neo|Mini|Air|Mavic|Avata|Matrice|Inspire|Phantom|Dock|N|O)\s*"
        r"(\d+(?:[SKMT])?(?:\s+(?:Pro|Lite|Classic|Cine|Enterprise|Zoom|SE))*)?",
        normalized, re.I,
    )
    return (match[1].casefold(), re.sub(r"\s+", "", match[2] or "").casefold()) if match else None


def is_attribute_value(value: str) -> bool:
    # Whole measurement values only: 5G is a protocol, Mini 4K a model;
    # neither may be rejected just because it contains a numeric unit-like suffix.
    if re.fullmatch(
        r"\d+(?:\.\d+)?\s*(?:g|kg|grams?|fps|MP|GB|TB|px|年|月|天)|"
        r"\d+(?:\.\d+)?\s*[Kk](?:\s*\d+\s*(?:fps|p))?|"
        r"\d{3,5}\s*[x×]\s*\d{3,5}", value.strip(),
    ):
        return True
    try:
        ipaddress.ip_address(value.strip())
        return True
    except ValueError:
        return bool(
            re.fullmatch(
                r"(?:[+-]?\d+(?:\.\d+)?|[$€£¥￥]\s*[\d,.]+(?:\s*(?:million|billion|thousand|万|亿))?"
                r"|[\d,.]+\s*(?:元|美元|人民币|美金)"
                r"|(?:点|\.)?[a-zA-Z0-9_.-]+文件"
                r"|[\w.-]*\.(?:bin|hex|ihx|apj|px4|elf))",
                value.strip(),
                re.I,
            )
        ) or bool(
            re.fullmatch(
                r"\d+(?:\.\d+)?\s*(?:%|mm|cm|km|m|hz|mhz|ghz|v|mah|秒|分钟)", value.strip(), re.I
            )
        )


def neighbor_context(material: Any, chunk: Any) -> str:
    chunks = material.extraction_chunks
    index = next(i for i, c in enumerate(chunks) if c.id == chunk.id)
    return "\n".join(
        c.text[:500] for c in chunks[max(0, index - 1) : index + 2] if c.id != chunk.id
    )


def input_hash(material: Any, chunk: Any, context: str | None = None) -> str:
    return stable_hash(
        {
            "video": str(material.id),
            "title": material.title[:1000],
            "summary": material.summary[:2000],
            "text": chunk.text,
            "kind": chunk.evidence_type,
            "start": chunk.start_seconds,
            "end": chunk.end_seconds,
            "neighbors": neighbor_context(material, chunk) if context is None else context,
        }
    )


@dataclass(frozen=True)
class EntityIdentity:
    id: uuid.UUID
    name: str
    scope_key: str
    alias_scope: str


def entity_identity(entity: ExtractedEntity, material: Any) -> EntityIdentity:
    if not entity.canonical_name or not entity.identity_scope:
        raise ValueError("v2 实体缺少规范身份和作用域")
    # The reviewer must explicitly resolve the identity. Unrelated brand mentions
    # elsewhere in a long video must never manufacture a brand/model association.
    context = entity.identity_quote or ""
    name = identity_name(entity.canonical_name, entity.entity_type)
    if is_attribute_value(name):
        raise ValueError("属性值不能作为图谱实体")
    if entity.identity_scope == "global" and is_generic(name):
        raise ValueError("泛称不能作为全局实体")
    if (entity.identity_scope == "global" and entity.entity_type == "product"
        and re.fullmatch(r"[A-Z]{1,2}-?\d{1,3}(?:\s+(?:Pro|Lite))?", name, re.I)):
        raise ValueError("短型号缺少明确制造商，不能作为全局实体")
    if (
        entity.identity_scope == "global"
        and entity.entity_type == "product"
        and identity_name(name, "product", "DJI") != name
    ):
        raise ValueError("短型号缺少明确制造商，不能作为全局实体")
    scope = "v2:global" if entity.identity_scope == "global" else f"v2:video:{material.id}"
    alias_scope = scope
    if scope == "v2:global" and (
        is_generic(entity.surface_text)
        or compact_name(identity_name(entity.surface_text, entity.entity_type, context))
        != compact_name(name)
    ):
        alias_scope = f"v2:video:{material.id}"
    key = f"{scope}:{entity.entity_type}:{normalized_name(name)}"
    return EntityIdentity(uuid.uuid5(ENTITY_NAMESPACE, key), name, scope, alias_scope)
