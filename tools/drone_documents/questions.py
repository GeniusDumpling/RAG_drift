"""Deterministic smoke questions grounded in each bundle; no model-specific fixtures."""

import re
from pathlib import Path

from .common import model_name, read_json, read_jsonl


def evaluation_questions(root: Path, questions_file: Path | None = None) -> list[str]:
    if questions_file is not None:
        questions = read_json(questions_file)
        if (
            not isinstance(questions, list)
            or not questions
            or any(not isinstance(q, str) or not q.strip() for q in questions)
        ):
            raise ValueError("问题文件须为非空 JSON 字符串数组")
        return list(dict.fromkeys(q.strip() for q in questions))
    manifest = read_json(root / "manifest.json")
    name = model_name(root, manifest)
    elements = read_jsonl(root / "elements.jsonl")
    text = "\n".join(e["text"] for e in elements if e["element_type"] in {"paragraph", "heading"})
    questions = []
    parameters = [
        e["metadata_json"].get("parameter_name", "")
        for e in elements
        if e["element_type"] == "parameter"
    ]
    selected = list(
        dict.fromkeys(p for p in parameters if re.search(r"重量|飞行时间|载重|容量", p))
    )
    if selected:
        questions.append(f"{name} 的{'、'.join(selected[:3])}是多少？请区分配置并说明测试条件。")
    for pattern, question in [
        (r"飞行前|起飞前|作业前", f"{name} 的飞行或作业前检查有哪些要求？"),
        (r"返航", f"文档对 {name} 的返航操作及注意事项有哪些说明？"),
        (r"电池.*充电|充电.*电池", f"{name} 相关电池的充电和使用有哪些注意事项？"),
        (r"螺旋桨|桨叶", f"{name} 的螺旋桨或桨叶安装与检查有哪些要求？"),
    ]:
        if re.search(pattern, text):
            questions.append(question)
    for doc in read_jsonl(root / "documents.jsonl"):
        if len(questions) >= 5:
            break
        if any(e["processing_id"] == doc["processing_id"] and e["text"].strip() for e in elements):
            questions.append(f"《{doc['title']}》提供了哪些使用要求或技术信息？请注明适用范围。")
    if not questions:
        raise ValueError("数据包没有可用于检索验证的中文内容；请先检查解析结果。")
    return questions[:5]
