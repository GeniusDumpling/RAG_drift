"""Resolve exact evidence references and bind reviews to original pixels and source text."""

import unicodedata
import uuid

from app.schemas.video_graph import ExtractedEntity
from app.services.video_graph.identity import entity_identity, is_generic, stable_hash
from app.services.video_graph.material import ENTITY_NAMESPACE
from app.services.video_graph.semantics import normalized_name

from .schema import Entity, Fact
from .source import case_source


def normalized(text):
    return "".join(unicodedata.normalize("NFKC", text).casefold().split())


def resolve_evidence(bundle, case, evidence):
    manual, pages = case_source(bundle, case)
    by_page = {p["number"]: p for p in pages}
    resolved = []
    for item in evidence:
        if item.page not in by_page:
            raise ValueError("证据引用了当前材料范围外的页面")
        page = by_page[item.page]
        lines = {r["id"].rsplit(":", 1)[-1]: r for r in page["lines"]}
        regions = {r["id"].rsplit(":", 1)[-1]: r for r in page["regions"]}
        if (
            len(set(item.text_ids)) != len(item.text_ids)
            or len(set(item.region_ids)) != len(item.region_ids)
            or not set(item.text_ids) <= lines.keys()
            or not set(item.region_ids) <= regions.keys()
        ):
            raise ValueError("文本或图片证据ID不存在、重复或来自其他页面")
        if item.visual_label:
            label = normalized(item.visual_label)
            markers = [r for r in page["lines"] if normalized(r["text"]) == label]
            if markers:

                def inside_marker(marker, regions=regions, item=item):
                    b = marker["bbox"]
                    x, y = (b[0] + b[2]) / 2, (b[1] + b[3]) / 2
                    return any(
                        regions[r]["bbox"][0] <= x <= regions[r]["bbox"][2]
                        and regions[r]["bbox"][1] <= y <= regions[r]["bbox"][3]
                        for r in item.region_ids
                    )

                if not any(inside_marker(marker) for marker in markers):
                    raise ValueError("编号原文坐标不在所引用的图片区域内")
        text_rows = [lines[key] for key in item.text_ids]
        picture_rows = [regions[key] for key in item.region_ids]
        resolved.append(
            dict(
                page=item.page,
                page_id=page["id"],
                document_id=manual["id"],
                document_sha256=manual["sha256"],
                manual_version=manual["manual_version"],
                text_rows=text_rows,
                quote="\n".join(r["text"] for r in text_rows),
                regions=[
                    {**r, "asset_sha256": bundle["assets"][r["asset_id"]]["sha256"]}
                    for r in picture_rows
                ],
                visual_label=item.visual_label,
            )
        )
    return resolved


def validate_entity(bundle, case, entity):
    rows = resolve_evidence(bundle, case, entity.evidence)
    quote = "\n".join(row["quote"] for row in rows)
    if normalized(entity.surface_text) not in normalized(quote) and not any(
        r["regions"] for r in rows
    ):
        raise ValueError("实体名无原文依据且没有可独立视觉审核的图片证据")
    return rows


def validate_fact(bundle, case, fact, keys, measurements=None, sections=None):
    if fact.subject not in keys or (fact.object is not None and fact.object not in keys):
        raise ValueError("事实引用不存在或未审核的实体")
    rows = resolve_evidence(bundle, case, fact.evidence)
    if sections and fact.predicate in {"instruction", "installed_on", "located_on"}:
        from .layout import validate_section_links

        validate_section_links(rows, sections)
    text = "\n".join(row["quote"] for row in rows)
    pictures = any(row["regions"] for row in rows)
    if fact.predicate == "has_parameter" and not pictures:
        if normalized(fact.value) not in normalized(text):
            raise ValueError("参数值不能定位到引用的原文行")
        if normalized(fact.attribute) not in normalized(text):
            raise ValueError("参数名不能定位到引用的原文行")
    if fact.predicate == "indicates":
        if not pictures:
            raise ValueError("灯态图形不能用纯文本引用替代")
        # A LED value must be in the same drawn table, not merely on the page.
        # Parts legends and procedure paragraphs may legitimately sit outside a
        # drawing, so this layout condition applies specifically to state tables.
        found = False
        for row in rows:
            for line in row["text_rows"]:
                if normalized(fact.value) not in normalized(line["text"]):
                    continue
                box = line["bbox"]
                x, y = (box[0] + box[2]) / 2, (box[1] + box[3]) / 2
                if any(
                    r["bbox"][0] <= x <= r["bbox"][2] and r["bbox"][1] <= y <= r["bbox"][3]
                    for r in row["regions"]
                ):
                    found = True
        if not found:
            raise ValueError("灯态值原文行不属于所引用图片表格")
        for measurement in measurements or []:
            if normalized(measurement["value"]) != normalized(fact.value):
                continue
            if any(
                e.page == measurement["page"]
                and measurement["region_id"] in e.region_ids
                and measurement["value_line_id"] in e.text_ids
                for e in fact.evidence
            ):
                if fact.led_pattern != measurement["led_pattern"]:
                    raise ValueError("灯态模式与原始矢量/图标像素测量不符")
    return rows


def entity_record(manual, entity=None):
    if entity is None:
        name, kind, scope = manual["model"], "product", "v2:global"
    else:
        name, kind = entity.surface_text, entity.entity_type
        scope = (
            f"v2:product:{manual['model']}"
            if kind == "component" or is_generic(name)
            else "v2:global"
        )
    if scope == "v2:global":
        identity = entity_identity(
            ExtractedEntity(
                key="source",
                surface_text=name,
                entity_type=kind,
                canonical_name=name,
                identity_scope="global",
                identity_quote=name,
            ),
            None,
        )
        eid, canonical = str(identity.id), identity.name
    else:
        canonical = f"{manual['model']} {name}"
        eid = str(uuid.uuid5(ENTITY_NAMESPACE, f"{scope}:{kind}:{normalized_name(name)}"))
    return dict(
        id=eid,
        key=entity.key if entity else "e0",
        surface_text=name,
        canonical_name=canonical,
        entity_type=kind,
        scope_key=scope,
    )


def result_hash(result):
    return stable_hash({k: result[k] for k in ["entities", "facts", "rejected"]})


def input_hash(bundle, case, policy):
    return stable_hash([bundle["source_hash"], case, policy])


def validate_result(bundle, case, result, policy, *, root=None):
    receipt = result.get("review", {})
    if (
        receipt.get("policy") != policy
        or receipt.get("input_hash") != input_hash(bundle, case, policy)
        or receipt.get("result_hash") != result_hash(result)
    ):
        raise ValueError("图文审核凭据缺失、过期或与来源/结果不符")
    decisions = receipt.get("decisions", {})
    if receipt.get("decisions_hash") != stable_hash(decisions):
        raise ValueError("独立审核决定被更改")
    for section, key in (("entities", "key"), ("facts", "index")):
        items = decisions.get(section, [])
        if len({r[key] for r in items}) != len(items):
            raise ValueError("审核决定ID重复")
    if len({r["index"] for r in result["facts"]}) != len(result["facts"]):
        raise ValueError("事实索引重复")
    measured = decisions.get("measured_led_rows", [])
    sections = decisions.get("procedure_layout", [])
    if root is not None:
        from .layout import procedure_layout
        from .symbols import led_measurements

        if sections != procedure_layout(bundle, case, root):
            raise ValueError("操作章节范围与当前原始PDF不符")

        if measured != led_measurements(bundle, case, root):
            raise ValueError("灯态测量与当前原始PDF不符")
    entity_decisions = {r.get("key"): r for r in decisions.get("entities", [])}
    fact_decisions = {r.get("index"): r for r in decisions.get("facts", [])}
    manual, _ = case_source(bundle, case)
    if result["entities"][0] != entity_record(manual):
        raise ValueError("文档机型身份被更改")
    keys = {r["key"] for r in result["entities"]}
    if len(keys) != len(result["entities"]):
        raise ValueError("实体key重复")
    for row in result["entities"][1:]:
        if entity_decisions.get(row["key"], {}).get("accepted") is not True:
            raise ValueError("实体缺少独立审核支持")
        entity = Entity.model_validate(row["proposal"])
        expected = entity_record(manual, entity)
        if any(row[k] != v for k, v in expected.items()):
            raise ValueError("审核后的实体身份被更改")
        if row["source"] != validate_entity(bundle, case, entity):
            raise ValueError("实体证据绑定被更改")
    for row in result["facts"]:
        if fact_decisions.get(row["index"], {}).get("supported") is not True:
            raise ValueError("事实缺少独立审核支持")
        fact = Fact.model_validate(row["proposal"])
        if row["source"] != validate_fact(bundle, case, fact, keys, measured, sections):
            raise ValueError("关系图文证据绑定被更改")
    return result
