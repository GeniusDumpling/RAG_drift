"""Source-bound name/attribute separation and local entity identity normalization."""

import re

from app.schemas.video_graph import ExtractedEntity
from app.services.video_graph.identity import compact_name, is_generic, stable_hash
from app.services.video_graph.terms import source_terms

_NUMBER = r"\d+(?:\.\d+)?(?:/\d+(?:\.\d+)?)?"
_MEASURE = re.compile(
    rf"(?P<size>{_NUMBER}\s*[- ]?\s*(?:英寸|吋|inches\b|inch\b|in\b))|"
    rf"(?P<focal>{_NUMBER}\s*mm(?![a-zA-Z]))|(?P<aperture>f\s*/?\s*\d+(?:\.\d+)?)|"
    rf"(?P<pixels>{_NUMBER}\s*(?:MP\b|megapixels?\b|万像素|萬像素))|"
    r"(?P<count>[一二两兩三四五六七八九十\d]+(?:块|塊|个|個|枚|片))|"
    r"(?P<format>\d+\s*[Kk](?![a-zA-Z]))|(?P<fps>\d+\s*(?:fps\b|帧|幀))",
    re.I,
)
_CORE = re.compile(
    r"(?P<core>(?:方形)?(?:传感器|傳感器)|感光元件|"
    r"(?:广角|廣角|中焦|长焦|長焦)?(?:镜头|鏡頭)|"
    r"(?:(?:wide[- ]angle|telephoto|medium[- ]telephoto)\s+)?(?:lens|sensor|camera))$",
    re.I,
)
_ATTRIBUTE_KEYS = {
    "size": "sensor_size",
    "focal": "focal_length",
    "aperture": "aperture",
    "pixels": "resolution",
    "count": "count",
    "format": "video_resolution",
    "fps": "frame_rate",
}
_CONNECTOR = re.compile(
    r"(?:\s|[,，、/&]|['’]s|的|和|及|与|與|各自|分别|分別|均有|都有|各有|有|"
    r"\b(?:and|each|both|have|has|include|includes|their|own|respective|a|an|the)\b)*$",
    re.I,
)
_SHARED = re.compile(r"\bshare(?:d|s)?\b|共用|共享|同一个|同一個", re.I)
_MULTIPLE = re.compile(
    r"\b(?:two|three|both|multiple|several|another|second)\b.{0,30}\b(?:drones?|aircraft|units?)\b|"
    r"两[台架]|兩[台架]|多[台架]|另一[台架]|第二[台架]",
    re.I,
)


def core_name(entity):
    """Strip only fully parsed measurements or a narrow ordinary controller modifier."""
    surface = entity.surface_text
    if entity.entity_type == "component":
        match = _CORE.search(surface)
        if match:
            prefix = surface[: match.start()]
            measures = list(_MEASURE.finditer(prefix))
            remaining = _MEASURE.sub("", prefix)
            remaining = re.sub(
                r"等效|equivalent|的|\b(?:a|an|the)\b|[\s,-]", "", remaining, flags=re.I
            )
            if measures and not remaining:
                attrs = {}
                for item in measures:
                    key = _ATTRIBUTE_KEYS[item.lastgroup]
                    if key in attrs and attrs[key] != item.group():
                        return surface, {}  # Multiple configurations need separate attribution.
                    attrs[key] = item.group()
                return match["core"], attrs
    if entity.entity_type == "technology":
        terms = [term for term in source_terms(surface) if term["entity_type"] == "technology"]
        if len(terms) == 1:
            core = terms[0]["surface_text"]
            start = surface.index(core)
            decoration = surface[:start] + surface[start + len(core) :]
            measures = list(_MEASURE.finditer(decoration))
            remaining = _MEASURE.sub("", decoration)
            remaining = re.sub(
                r"的|视频|視頻|影片|\b(?:videos?|footage)\b|[\s,-]", "", remaining, flags=re.I
            )
            if measures and not remaining:
                attrs = {}
                for item in measures:
                    key = _ATTRIBUTE_KEYS[item.lastgroup]
                    if key in attrs and attrs[key] != item.group():
                        return surface, {}
                    attrs[key] = item.group()
                return core, attrs
    if entity.entity_type == "software":
        match = re.fullmatch(r"(?:(?:the|drone|aircraft)(?:['’]s)?\s+)*(firmware)", surface, re.I)
        if match:
            return match[1], {}
    if entity.entity_type == "product":
        match = re.fullmatch(
            r"(?:传统的?|傳統的?|traditional\s+)(遥控器|遙控器|remote controller)", surface, re.I
        )
        if match:
            return match[1], {}
    return surface, {}


def product_owners(entities):
    """Only reviewed, non-generic devices supply owner identities; no title-only new entities."""
    owners = {}
    for entity in entities:
        if entity.entity_type != "product" or is_generic(entity.canonical_name or ""):
            continue
        canonical = entity.canonical_name or entity.surface_text
        # A canonical generic with an ordinary adjective is not a model anchor.
        if re.search(r"传统|傳統|\btraditional\b", canonical, re.I):
            continue
        key = compact_name(canonical)
        row = owners.setdefault(key, {"entity": entity, "forms": set()})
        row["forms"].update({entity.surface_text, canonical})
        row["forms"].update(
            re.sub(r"^(?:DJI|大疆)\s*", "", form, flags=re.I) for form in list(row["forms"])
        )
    return list(owners.values())


def _pattern(form):
    text = r"\s*".join(re.escape(word) for word in form.split())
    return (
        r"(?<![a-zA-Z0-9])" + text + r"(?![a-zA-Z0-9])"
        r"(?!\s*(?:Pro|Lite|Classic|Cine|Enterprise|Zoom|SE)\b)"
    )


def named_owners(text, owners):
    matches = []
    for index, owner in enumerate(owners):
        for form in owner["forms"]:
            if not form:
                continue
            for match in re.finditer(_pattern(form), text, re.I):
                matches.append((match.start(), match.end(), index))
    matches.sort(key=lambda row: (-(row[1] - row[0]), row[0]))
    kept = []
    for start, end, index in matches:
        if any(start < b and a < end for a, b, _ in kept):
            continue
        kept.append((start, end, index))
    return sorted(kept)


def source_owners(core, source, owners):
    """Explicit possessives/enumerations only, within one source line/statement."""
    associations = []
    for occurrence in re.finditer(re.escape(core), source, re.I):
        start = max(source.rfind("\n", 0, occurrence.start()) + 1, occurrence.start() - 220)
        prefix = source[start : occurrence.start()]
        if _SHARED.search(prefix + source[occurrence.end() : occurrence.end() + 40]):
            continue
        spans = named_owners(prefix, owners)
        for position in range(len(spans)):
            first = spans[position][0]
            tail = prefix[first:]
            for a, b, _ in reversed(spans[position:]):
                tail = tail[: a - first] + " " * (b - a) + tail[b - first :]
            # Permit a coordinated earlier part on the same line: A's wide and tele lens.
            tail = re.sub(r"广角|廣角|中焦|长焦|長焦|\bwide[- ]angle\b", "", tail, flags=re.I)
            tail = _MEASURE.sub("", tail)
            if _CONNECTOR.fullmatch(tail):
                indices = tuple(dict.fromkeys(index for _, _, index in spans[position:]))
                quote = source[start + first : occurrence.end()]
                associations.append((indices, quote))
                break
    return associations


def _new_name(entity, core, owner):
    if owner:
        # Preserve a reviewed alias (e.g. an acronym) instead of expanding it back
        # to the literal long form when adding/standardizing the owner prefix.
        canonical = entity.canonical_name or ""
        references = named_owners(canonical, [owner])
        if references and references[0][0] == 0:
            label = canonical[references[0][1] :].strip()
            label = re.sub(r"^(?:的|['’]s)\s*", "", label)
            if label and not re.match(r"[,，、/&]|and\b|和|及|与|與", label, re.I):
                core = core_name(entity.model_copy(update={"surface_text": label}))[0]
        if core in {"广角", "廣角", "中焦", "长焦", "長焦"} and re.search(
            r"镜头|鏡頭",
            entity.canonical_name or "",
        ):
            core += "镜头" if "镜头" in entity.canonical_name else "鏡頭"
        return owner["entity"].canonical_name + " " + core
    canonical = entity.canonical_name or entity.surface_text
    # Keep an already resolved proper name; only replace the corresponding literal name.
    if compact_name(canonical) in {
        compact_name(entity.surface_text),
        compact_name(core),
        compact_name(entity.surface_text.replace("的", "")),
    }:
        return core
    return canonical


def _forms_equal_name(entity, owner):
    canonical = entity.canonical_name or ""
    found = named_owners(canonical, [owner])
    return bool(found and found[0][0] == 0)


def normalize_entity_review(material, chunk, context, extraction):
    """Normalize approved entities before any relation is generated; retain literal aliases."""
    from app.services.video_graph.review import (
        result_hash,
        validate_identity_proof,
        validate_review,
    )

    validate_review(chunk, extraction, material)
    if extraction.relations:
        raise ValueError("实体归一必须位于关系生成之前")
    owners = product_owners(extraction.entities)
    rejected = list(extraction.review.rejected)
    plans = []
    for entity in extraction.entities:
        inner = entity.surface_text
        prefixes = named_owners(inner, owners) if entity.entity_type == "component" else []
        if len(prefixes) == 1 and prefixes[0][0] == 0:
            suffix = inner[prefixes[0][1] :]
            stripped = re.sub(r"^(?:\s*的|\s*['’]s|\s+)", "", suffix)
            if stripped and (stripped != suffix or _CORE.fullmatch(suffix)):
                inner = stripped
        core, measurements = core_name(entity.model_copy(update={"surface_text": inner}))
        attrs = dict(entity.attributes)
        for key, value in measurements.items():
            if key in attrs and attrs[key] != value:
                rejected.append(
                    {
                        "kind": "attribute",
                        "key": entity.key,
                        "reason": "参数分离与已有属性冲突，不覆盖已有来源值",
                    }
                )
            elif len(attrs) < 12 or key in attrs:
                attrs[key] = value
        selected, quote = [], entity.identity_quote
        owner_quotes = {}
        if entity.entity_type == "component":
            associations = source_owners(core, chunk.text, owners)
            declared = {
                i
                for i, owner in enumerate(owners)
                if named_owners(entity.canonical_name or "", [owner])
            }
            candidates = [
                (indices, evidence)
                for indices, evidence in associations
                if not declared or declared.intersection(indices)
            ]
            explicit = {i for indices, _ in associations for i in indices}
            if (
                len(explicit) == 1
                and len(list(re.finditer(re.escape(core), chunk.text, re.I))) == 1
            ):
                candidates = associations
                declared = explicit
            if candidates:
                indices = list(
                    dict.fromkeys(
                        i
                        for indices, _ in candidates
                        for i in indices
                        if not declared or i in declared
                    )
                )
                selected = [owners[i] for i in indices]
                quote = max((evidence for _, evidence in candidates), key=len)
                for group, evidence in candidates:
                    for i in group:
                        owner_quotes[owners[i]["entity"].key] = evidence
            elif len(declared) == 1:
                selected = [owners[next(iter(declared))]]
        elif (
            entity.entity_type == "software"
            and core.casefold() == "firmware"
            and not _MULTIPLE.search(chunk.text)
        ):
            literal = compact_name(entity.canonical_name or "")
            declared = [
                owner
                for owner in owners
                if _forms_equal_name(entity, owner)
                and literal
                in {
                    compact_name(owner["entity"].canonical_name + " firmware"),
                    compact_name(owner["entity"].canonical_name + " drone firmware"),
                }
            ]
            if len(declared) == 1:
                selected = declared
            elif len(owners) == 1 and literal in {
                compact_name(core),
                compact_name(entity.surface_text),
            }:
                selected = owners
        if not selected:
            selected = [None]
        variants = []
        for owner in selected:
            changes = {"canonical_name": _new_name(entity, core, owner), "attributes": attrs}
            if owner and entity.entity_type in {"component", "software"}:
                changes["identity_scope"] = "video"
            if owner and owner["entity"].key in owner_quotes:
                changes.update(
                    identity_quote=owner_quotes[owner["entity"].key],
                    identity_source="current_chunk",
                )
            if len(selected) > 1:
                changes.update(
                    key="owned_" + stable_hash([entity.key, owner["entity"].key])[:16],
                    identity_quote=owner_quotes.get(owner["entity"].key, quote),
                    identity_source="current_chunk",
                    attributes={},
                )
            try:
                normalized = ExtractedEntity.model_validate({**entity.model_dump(), **changes})
                validate_identity_proof(normalized, material, chunk, context)
            except ValueError:
                if len(selected) > 1:
                    continue
                normalized = entity
                core = entity.surface_text
            variants.append((normalized, core))
        if len(variants) != len(selected):
            plans.append((entity, entity.surface_text))
            rejected.append(
                {
                    "kind": "identity",
                    "key": entity.key,
                    "reason": "所有者拆分未全部通过来源校验，保留原审核身份",
                }
            )
            continue
        plans.extend(variants)
        if len(selected) > 1:
            rejected.append(
                {
                    "kind": "identity_split",
                    "key": entity.key,
                    "reason": "原文明示多个设备各自的同名部件，按所有者分别保留",
                }
            )
            if attrs:
                rejected.append(
                    {
                        "kind": "attribute",
                        "key": entity.key,
                        "reason": "所有者未分别归属的参数不复制到多个部件实例",
                    }
                )
    # A local generic already resolved to one named global device can share that identity.
    globals_by_name = {
        compact_name(e.canonical_name): e
        for e, _ in plans
        if e.entity_type == "product" and e.identity_scope == "global"
    }
    if not _MULTIPLE.search(chunk.text):
        for index, (entity, core) in enumerate(plans):
            anchor = globals_by_name.get(compact_name(entity.canonical_name))
            if (
                anchor
                and entity.identity_scope == "video"
                and entity.entity_type == "product"
                and is_generic(entity.surface_text)
            ):
                plans[index] = (entity.model_copy(update={"identity_scope": "global"}), core)
    entities, seen = [], set()
    for entity, core in plans:
        signature = (
            entity.canonical_name,
            entity.entity_type,
            entity.identity_scope,
            entity.surface_text,
            stable_hash(entity.attributes),
        )
        if signature not in seen:
            entities.append(entity)
            seen.add(signature)
        if core != entity.surface_text:
            signature = (
                entity.canonical_name,
                entity.entity_type,
                entity.identity_scope,
                core,
                stable_hash(entity.attributes),
            )
            if signature not in seen:
                entities.append(
                    entity.model_copy(
                        update={
                            "key": "core_" + stable_hash(signature)[:16],
                            "surface_text": core,
                        }
                    )
                )
                seen.add(signature)
        evidence = entity.identity_quote or ""
        references = named_owners(evidence, owners)
        if entity.entity_type == "component" and len(references) == 1 and references[0][0] == 0:
            suffix = evidence[references[0][1] :]
            possessive = re.fullmatch(r"\s*(?:的|['’]s)\s*(.+)", suffix)
            if (
                possessive
                and core_name(
                    entity.model_copy(
                        update={"surface_text": possessive[1]},
                    )
                )[0]
                == core
            ):
                signature = (
                    entity.canonical_name,
                    entity.entity_type,
                    entity.identity_scope,
                    evidence,
                    stable_hash(entity.attributes),
                )
                if signature not in seen:
                    entities.append(
                        entity.model_copy(
                            update={
                                "key": "alias_" + stable_hash(signature)[:16],
                                "surface_text": evidence,
                            }
                        )
                    )
                    seen.add(signature)
    if len(entities) > 60:
        rejected.append(
            {
                "kind": "identity",
                "key": "normalization",
                "reason": "归一后提及超出上限，保留原审核结果",
            }
        )
        entities = extraction.entities
    result = extraction.model_copy(update={"entities": entities})
    result.review = extraction.review.model_copy(
        update={"result_hash": result_hash(result), "rejected": rejected}
    )
    validate_review(chunk, result, material)
    return result
