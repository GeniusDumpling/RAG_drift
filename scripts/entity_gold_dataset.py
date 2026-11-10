#!/usr/bin/env python3
"""Check and render the small entity annotation set; no model or database access."""

import argparse
import json
from collections import Counter
from pathlib import Path

DEFAULT_DATASET = Path("scripts/evaluation_data/entity_gold_v3.json")
TYPE_LABELS = {
    "product": "产品/设备",
    "component": "硬件部件",
    "organization": "组织",
    "software": "软件",
    "technology": "技术/功能",
    "method": "方法/操作",
    "fault": "故障",
    "person": "人物",
    "country": "国家",
    "region": "地区",
    "protocol": "协议",
    "data": "数据",
    "document": "资料",
    "product_family": "产品系列",
    "event": "活动",
}


def case_text(case):
    text = case["text"]
    if isinstance(text, str):
        return text
    if isinstance(text, list) and all(isinstance(line, str) for line in text):
        return "\n".join(text)
    raise ValueError(f"{case['id']}: text需要是字符串或逐行字符串数组")


def validate(data):
    if set(data) != {"version", "status", "cases"}:
        raise ValueError("数据集只需version、status、cases三个字段")
    if data["status"] not in {"assistant_draft", "human_reviewed"}:
        raise ValueError("未知复核状态")
    cases = data["cases"]
    if len(cases) != 20 or Counter(c["language"] for c in cases) != {"zh": 10, "en": 10}:
        raise ValueError("需要20段字幕，中文和英文各10段")
    for key in ["id"]:
        if len({c[key] for c in cases}) != len(cases):
            raise ValueError(f"重复的{key}")
    for key in ["url", "chunk_id"]:
        if len({c["source"][key] for c in cases}) != len(cases):
            raise ValueError(f"重复的来源{key}")
    for case in cases:
        if set(case) != {"id", "language", "models", "source", "text", "entities", "notes"}:
            raise ValueError(f"{case['id']}: case字段不符合简化格式")
        text = case_text(case)
        if not text.strip() or not case["models"]:
            raise ValueError(f"{case['id']}: 必须有原文和具体无人机型号")
        source = case["source"]
        if set(source) != {"title", "url", "chunk_id", "start_seconds", "end_seconds"}:
            raise ValueError(f"{case['id']}: 来源字段不完整")
        if not 0 <= source["start_seconds"] <= source["end_seconds"]:
            raise ValueError(f"{case['id']}: 来源时间范围错误")
        identities = set()
        for entity in case["entities"]:
            if set(entity) != {"name", "type", "mentions"}:
                raise ValueError(f"{case['id']}: 每个实体只需name、type、mentions")
            if not entity["name"].strip() or entity["type"] not in TYPE_LABELS:
                raise ValueError(f"{case['id']}: 实体名称或类型错误")
            if not entity["mentions"] or any(
                not mention.strip() or mention not in text for mention in entity["mentions"]
            ):
                raise ValueError(f"{case['id']}: 实体提及必须能在正文找到")
            identity = (entity["name"], entity["type"])
            if identity in identities:
                raise ValueError(f"{case['id']}: 重复实体")
            identities.add(identity)
        # models describes the source, not a second list of gold answers.
        # Human review may omit a passing mention or rename its canonical form.
    return {
        "version": data["version"],
        "status": data["status"],
        "cases": len(cases),
        "languages": dict(Counter(c["language"] for c in cases)),
        "entities": sum(len(c["entities"]) for c in cases),
        "validation": "来源和格式检查通过；不代表语义已人工确认或模型质量通过",
    }


def cell(text):
    return text.replace("|", "\\|").replace("\n", " ⏎ ")


def render(data):
    reviewed = data["status"] == "human_reviewed"
    status = "用户已确认实体答案人工复核完成。" if reviewed else "助手初标，待人工复核。"
    lines = [
        "# 无人机实体金标准集" if reviewed else "# 无人机实体金标准候选集",
        "",
        f"中文10段、英文10段；每段正文均涉及具体无人机型号。{status}",
        "每段先读原文，再核对实体是否漏标、多标或分错类型。JSON是编辑入口，本文件由脚本生成。",
        "数值参数不单独作为实体；普通动作不逐句拆成方法。未核实来源说法的现实真伪。",
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
            "| 实体名称 | 类型 | 原文提及 |",
            "|---|---|---|",
        ]
        for entity in case["entities"]:
            lines.append(
                f"| {cell(entity['name'])} | {TYPE_LABELS[entity['type']]} | "
                f"{cell('；'.join(entity['mentions']))} |"
            )
        if case["notes"]:
            lines += ["", "复核说明：", "", *[f"- {note}" for note in case["notes"]]]
        lines.append("")
    return "\n".join(lines)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["validate", "render"])
    parser.add_argument("--dataset", type=Path, default=DEFAULT_DATASET)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    data = json.loads(args.dataset.read_text())
    report = validate(data)
    result = (
        render(data)
        if args.command == "render"
        else json.dumps(report, ensure_ascii=False, indent=2)
    ) + "\n"
    if args.output:
        if args.output.resolve() == args.dataset.resolve():
            parser.error("输出文档不能覆盖标注JSON")
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(result)
    else:
        print(result, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
