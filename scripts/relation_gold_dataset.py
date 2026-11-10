#!/usr/bin/env python3
"""Validate/render relation annotations offline; never call a model or database."""

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "backend"))

from app.schemas.video_graph import ExtractedEntity, ExtractedRelation  # noqa: E402
from app.services.video_graph.semantics import (  # noqa: E402
    DERIVED_PREDICATES,
    PREDICATE_DEFINITIONS,
    validate_relation_semantics,
)

from scripts.entity_gold_dataset import (  # noqa: E402
    TYPE_LABELS,
    case_text,
    cell,
)
from scripts.entity_gold_dataset import (  # noqa: E402
    validate as validate_entities,
)

DEFAULT_DATASET = Path("scripts/evaluation_data/relation_gold_v3.json")
DEFAULT_ENTITIES = Path("scripts/evaluation_data/entity_gold_v3.json")
# The original entity v1 filename contains a dataset whose declared version is v2.
ENTITY_VERSIONS = {"relation-gold-v1": "entity-gold-v2", "relation-gold-v3": "entity-gold-v3"}
ASSERTIONS = {"asserted": "明确陈述", "uncertain": "不确定", "negated": "明确否定"}
MODALITIES = {
    "actual",
    "capability",
    "instruction",
    "conditional",
    "hypothetical",
    "design_goal",
    "planned",
}
SCOPED_MODALITIES = {"conditional", "hypothetical", "design_goal", "planned"}


def quote_text(relation):
    quote = relation["quote"]
    if isinstance(quote, str):
        return quote
    if isinstance(quote, list) and quote and all(isinstance(line, str) for line in quote):
        return "\n".join(quote)
    raise ValueError("quote需要是原文字符串或连续原文的逐行数组")


def validate(data, entity_data):
    validate_entities(entity_data)
    if set(data) != {"version", "status", "cases"} or data["version"] not in ENTITY_VERSIONS:
        raise ValueError("关系集需要已支持的version、status、cases")
    if entity_data["version"] != ENTITY_VERSIONS[data["version"]]:
        raise ValueError("关系集与实体集版本不配套，请同时指定对应的dataset和entities")
    if data["status"] not in {"assistant_draft", "human_reviewed"}:
        raise ValueError("未知关系复核状态")
    if [c["id"] for c in data["cases"]] != [c["id"] for c in entity_data["cases"]]:
        raise ValueError("关系集需要与实体集保持相同片段及顺序")
    entity_view = {
        **data,
        "cases": [{k: v for k, v in c.items() if k != "relations"} for c in data["cases"]],
    }
    validate_entities(entity_view)
    predicates, assertions = Counter(), Counter()
    empty_cases = []
    for case, base in zip(data["cases"], entity_data["cases"], strict=True):
        prefix = case["id"] + ": "
        for field in ("id", "language", "models", "source", "text", "entities"):
            if case[field] != base[field]:
                raise ValueError(prefix + f"{field}必须沿用实体集，不能改写")
        if not isinstance(case["notes"], list) or any(
            not isinstance(note, str) for note in case["notes"]
        ):
            raise ValueError(prefix + "notes需要是字符串数组")
        relations = case.get("relations")
        if not isinstance(relations, list):
            raise ValueError(prefix + "relations需要是数组，可为空")
        if not relations:
            empty_cases.append(case["id"])
        entities = {e["name"]: e for e in case["entities"]}
        if len(entities) != len(case["entities"]):
            raise ValueError(prefix + "实体名称需唯一，以便关系端点引用")
        seen = set()
        for relation in relations:
            required = {"subject", "predicate", "object", "assertion", "quote"}
            if not required <= set(relation) or set(relation) - required - {"qualifiers"}:
                raise ValueError(
                    prefix + "关系只需subject、predicate、object、assertion、quote及可选qualifiers"
                )
            subject, obj, predicate = (relation[k] for k in ("subject", "object", "predicate"))
            if subject not in entities or obj not in entities:
                raise ValueError(prefix + "关系端点必须引用本片段已有实体名称")
            if subject == obj:
                raise ValueError(prefix + "不能标注自指关系")
            if predicate not in PREDICATE_DEFINITIONS or predicate in DERIVED_PREDICATES:
                raise ValueError(prefix + "未知或仅用于规则派生的关系类型")
            if relation["assertion"] not in ASSERTIONS:
                raise ValueError(prefix + "未知assertion")
            quote = quote_text(relation)
            if not quote.strip() or quote not in case_text(case):
                raise ValueError(prefix + "quote必须是正文中的连续原文，不能拼接或改写")
            qualifiers = relation.get("qualifiers", {})
            if (
                not isinstance(qualifiers, dict)
                or set(qualifiers) - {"modality", "scope", "mode"}
                or any(not isinstance(v, str) or not v.strip() for v in qualifiers.values())
            ):
                raise ValueError(prefix + "qualifiers仅使用非空字符串modality、scope、mode")
            modality = qualifiers.get("modality", "actual")
            if modality not in MODALITIES:
                raise ValueError(prefix + "未知modality")
            if modality in SCOPED_MODALITIES and not qualifiers.get("scope"):
                raise ValueError(prefix + "条件或假设关系需要scope原文")
            for field in ("scope", "mode"):
                if field in qualifiers and qualifiers[field] not in quote:
                    raise ValueError(prefix + f"{field}必须逐字出现在本关系quote中")
            validate_relation_semantics(
                ExtractedRelation(
                    subject=subject,
                    object=obj,
                    predicate=predicate,
                    assertion=relation["assertion"],
                    quote=quote,
                    confidence=1.0,
                ),
                ExtractedEntity(
                    key="subject", surface_text=subject, entity_type=entities[subject]["type"]
                ),
                ExtractedEntity(key="object", surface_text=obj, entity_type=entities[obj]["type"]),
            )
            endpoints = (
                tuple(sorted((subject, obj)))
                if predicate in {"compatible_with", "connected_to"}
                else (subject, obj)
            )
            identity = (
                endpoints,
                predicate,
                relation["assertion"],
                tuple(sorted(qualifiers.items())),
            )
            if identity in seen:
                raise ValueError(prefix + "重复关系（兼容和连接也检查反向重复）")
            seen.add(identity)
            predicates[predicate] += 1
            assertions[relation["assertion"]] += 1
    return {
        "version": data["version"],
        "status": data["status"],
        "cases": len(data["cases"]),
        "languages": dict(Counter(c["language"] for c in data["cases"])),
        "entities": sum(len(c["entities"]) for c in data["cases"]),
        "relations": sum(predicates.values()),
        "predicates": dict(sorted(predicates.items())),
        "assertions": {k: assertions[k] for k in ASSERTIONS},
        "empty_cases": empty_cases,
        "validation": "格式、端点、类型、证据原文检查通过；不代表关系语义已人工确认或模型质量通过",
    }


def render(data):
    status = "已人工复核" if data["status"] == "human_reviewed" else "助手初标，待人工复核"
    count = sum(len(c["relations"]) for c in data["cases"])
    lines = [
        "# 无人机关系金标准集复核稿",
        "",
        f"状态：{status}。20个片段（中文、英文各10段），{count}条关系。",
        "",
        "沿用实体金标准集的原文、来源和实体答案；JSON是编辑入口，本文件由脚本生成。",
        "复核：是否漏标/多标，方向和谓词是否准确，原文是否支持，条件与不确定性是否保留。",
        "asserted仅表示来源明确陈述，不代表外部事实已核实。未标关系不等于明确否定。",
        "",
    ]
    for case in data["cases"]:
        source = case["source"]
        lines += [
            f"## {case['id']} · {'中文' if case['language'] == 'zh' else '英文'}",
            "",
            f"涉及型号：{'、'.join(case['models'])}。",
            "",
            f"[{source['title']}]({source['url']}) · "
            f"{source['start_seconds']}–{source['end_seconds']}秒",
            "",
            "```text",
            case_text(case),
            "```",
            "",
            "| 已确认实体 | 类型 | 原文提及 |",
            "|---|---|---|",
        ]
        for entity in case["entities"]:
            lines.append(
                f"| {cell(entity['name'])} | {TYPE_LABELS[entity['type']]} | "
                f"{cell('；'.join(entity['mentions']))} |"
            )
        lines += [
            "",
            "| 主体 | 关系 | 客体 | 断言 | 限定 | 证据原文 |",
            "|---|---|---|---|---|---|",
        ]
        for relation in case["relations"]:
            qualifier = "；".join(f"{k}={v}" for k, v in relation.get("qualifiers", {}).items())
            values = [
                relation["subject"],
                f"{relation['predicate']}（{PREDICATE_DEFINITIONS[relation['predicate']]}）",
                relation["object"],
                relation["assertion"],
                qualifier or "—",
                quote_text(relation),
            ]
            lines.append("| " + " | ".join(cell(v) for v in values) + " |")
        if not case["relations"]:
            lines += ["", "本段未标注关系，原因见下方说明。"]
        lines += ["", "复核说明：", "", *[f"- {note}" for note in case["notes"]], ""]
    return "\n".join(lines)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["validate", "render"])
    parser.add_argument("--dataset", type=Path, default=DEFAULT_DATASET)
    parser.add_argument("--entities", type=Path, default=DEFAULT_ENTITIES)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    data = json.loads(args.dataset.read_text())
    result = validate(data, json.loads(args.entities.read_text()))
    output = (
        render(data)
        if args.command == "render"
        else json.dumps(result, ensure_ascii=False, indent=2)
    ) + "\n"
    if args.output:
        if args.output.resolve() in {args.dataset.resolve(), args.entities.resolve()}:
            parser.error("输出不能覆盖关系或实体标注JSON")
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(output)
    else:
        print(output, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
