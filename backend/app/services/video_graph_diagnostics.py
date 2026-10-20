"""Bounded failure details without provider response bodies or credentials."""

from collections import Counter
from collections.abc import Callable
from typing import Any

import httpx
from pydantic import ValidationError

from app.schemas.video_graph import ChunkExtraction

RULES = {
    "模型返回了重复实体 key": "duplicate_entity_key",
    "实体提及无法在当前切片原文中定位": "entity_surface_not_found",
    "关系引用了不存在的实体": "relation_entity_not_found",
    "关系证据无法在当前字幕切片中定位": "relation_quote_not_found",
    "关系限定条件过长": "relation_qualifier_too_long",
    "worker 抽取版本与任务版本不一致，请使用 scan 对齐配置": "extractor_version_mismatch",
    "地理关系证据中必须包含宾语的原文名称": "geographic_object_not_in_quote",
    "located_in 不支持国家到国家的位置归属": "invalid_country_location",
}


def describe_graph_error(
    exc: Exception,
    *,
    stage: str,
    chunk: Any = None,
    chunk_index: int | None = None,
    extraction: ChunkExtraction | None = None,
    locate: Callable[[str, str], tuple[int, int] | None],
) -> dict[str, Any]:
    detail: dict[str, Any] = {
        "stage": stage,
        "error_type": type(exc).__name__,
        "code": "unclassified_error",
        "message": "未分类的处理错误",
    }
    if chunk is not None:
        detail.update(
            chunk_id=str(chunk.id), chunk_index=chunk_index,
            evidence_type=chunk.evidence_type, chunk_chars=len(chunk.text),
        )
    if isinstance(exc, ValidationError):
        detail.update(code="schema_validation_failed", message="模型输出不符合字段结构")
        detail["fields"] = [
            {"path": [str(p)[:80] for p in e["loc"]], "type": e["type"]}
            for e in exc.errors(include_url=False, include_input=False, include_context=False)[:10]
        ]
    elif isinstance(exc, ValueError):
        message = str(exc)
        if message in RULES:
            detail.update(code=RULES[message], message=message)
        elif message.startswith("已知地理实体类型冲突，必须使用 "):
            detail.update(code="entity_geographic_type_conflict", message=message[:100])
        elif message.startswith("关系 ") and message.endswith(" 的主语或宾语类型不符合约束"):
            detail.update(code="relation_endpoint_type_conflict", message=message[:150])
        else:
            detail.update(code="unclassified_value_error", message="未分类的值校验错误")
    else:
        chain: list[str] = []
        cause: BaseException | None = exc
        seen: set[int] = set()
        while cause is not None and id(cause) not in seen and len(chain) < 8:
            seen.add(id(cause))
            chain.append(type(cause).__name__)
            if isinstance(cause, httpx.HTTPStatusError):
                detail["http_status"] = cause.response.status_code
            cause = cause.__cause__
        detail["cause_types"] = chain
        if "HTTPStatusError" in chain:
            detail.update(code="model_http_error", message="模型接口返回 HTTP 错误")
        elif any(name.endswith("Timeout") for name in chain):
            detail.update(code="model_timeout", message="模型请求超时")
        elif "ConnectError" in chain:
            detail.update(code="model_connection_error", message="无法建立模型接口连接")
        elif "JSONDecodeError" in chain:
            detail.update(code="model_invalid_json", message="模型返回内容不是合法 JSON")
        elif stage in {"assessment", "extraction"}:
            detail.update(code="model_request_error", message="模型调用或响应解析失败")
    if extraction is None or chunk is None:
        return detail
    keys = {entity.key for entity in extraction.entities}
    code = detail["code"]
    if code == "duplicate_entity_key":
        detail["duplicate_keys"] = [
            key[:160] for key, count in Counter(e.key for e in extraction.entities).items()
            if count > 1
        ][:10]
    elif code == "entity_surface_not_found":
        for index, entity in enumerate(extraction.entities):
            if locate(chunk.text, entity.surface_text) is None:
                detail.update(
                    entity_index=index, entity_key=entity.key[:160],
                    surface_text=entity.surface_text[:255], entity_type=entity.entity_type,
                )
                break
    elif code in {
        "relation_entity_not_found", "relation_quote_not_found", "relation_qualifier_too_long"
    }:
        for index, relation in enumerate(extraction.relations):
            invalid = (
                (code == "relation_entity_not_found" and (
                    relation.subject not in keys or relation.object not in keys
                ))
                or (
                    code == "relation_quote_not_found"
                    and locate(chunk.text, relation.quote) is None
                )
                or (code == "relation_qualifier_too_long" and any(
                    len(k) > 80 or len(v) > 500 for k, v in relation.qualifiers.items()
                ))
            )
            if invalid:
                detail.update(
                    relation_index=index, subject=relation.subject[:160],
                    object=relation.object[:160], predicate=relation.predicate,
                )
                if code == "relation_quote_not_found":
                    detail["quote"] = relation.quote[:240]
                break
    return detail


def graph_error_summary(detail: dict[str, Any]) -> str:
    chunk = f"，切片 {detail['chunk_index']}" if detail.get("chunk_index") else ""
    return (
        f"{detail['error_type']}: {detail['message']}"
        f"（阶段 {detail['stage']}{chunk}，规则 {detail['code']}）；旧图谱保留，可重试"
    )
