"""Source-claim contracts, exact span restoration and contextual semantic guards."""

import re
from functools import lru_cache
from typing import Literal

from opencc import OpenCC

from app.schemas.video_graph import ExtractedEntity, ExtractedRelation, Predicate

_SCRIPT_CONVERTER = OpenCC("t2s")
_SCRIPT_CONVERTER.convert("")  # Initialize dictionaries before worker threads use them.


@lru_cache(maxsize=2048)
def script_form(value: str) -> str:
    """Comparison only; source_span always returns the original source characters."""
    return _SCRIPT_CONVERTER.convert(value)


def source_span(text: str, quote: str, *, ignore_case: bool = False) -> str | None:
    """Restore source characters after typography or unique Chinese script matching."""
    if not quote.strip():
        return None
    punctuation = str.maketrans({"‐": "-", "‑": "-", "–": "-", "—": "-", "−": "-",
                               "‘": "'", "’": "'", "“": '"', "”": '"'})
    def normalize(value):
        value = value.translate(punctuation)
        chars, positions = [], []
        index = 0
        while index < len(value):
            if value[index].isspace():
                end = index + 1
                while end < len(value) and value[end].isspace():
                    end += 1
                before = chars[-1] if chars else ""
                after = value[end] if end < len(value) else ""
                # CJK has no word-space requirement; keep Latin word boundaries.
                if before and after and not any("\u3400" <= c <= "\u9fff"
                                                for c in (before, after)):
                    chars.append(" ")
                    positions.append(index)
                index = end
                continue
            chars.append(value[index])
            positions.append(index)
            index += 1
        return "".join(chars), positions

    normalized, positions = normalize(text)
    needle, _ = normalize(quote)
    pattern = re.escape(needle)
    matches = list(re.finditer(pattern, normalized, re.I if ignore_case else 0))
    if not matches and re.search(r"[\u3400-\u9fff]", needle):
        simplified, simple_needle = script_form(normalized), script_form(needle)
        # Length-changing conversions cannot reuse the original position map.
        if len(simplified) == len(normalized) and len(simple_needle) == len(needle):
            matches = list(re.finditer(re.escape(simple_needle), simplified,
                                       re.I if ignore_case else 0))
            if len(matches) != 1:
                return None
    if not matches:
        return None
    if ignore_case and len(matches) != 1:
        originals = {text[positions[m.start()]:positions[m.end() - 1] + 1] for m in matches}
        if len(originals) != 1:
            return None
    match = matches[0]
    return text[positions[match.start()]:positions[match.end() - 1] + 1]


ClaimBasis = Literal[
    "composition", "capability", "org_structure", "use", "data_use", "application",
    "compatibility", "installation", "package", "ownership", "manufacture", "development",
    "distribution", "event_organization", "affiliation", "connection", "control", "failure",
    "mitigation", "deployment", "location", "model_lineup", "analogy", "influence", "parameter",
    "none", "equipment",
]
Modality = Literal[
    "actual", "capability", "instruction", "conditional", "hypothetical", "design_goal", "planned",
]
BASIS_BY_PREDICATE: dict[Predicate, frozenset[str]] = {
    "uses": frozenset({"use", "data_use"}),
    "contains": frozenset({"composition", "capability", "org_structure"}),
    "compatible_with": frozenset({"compatibility"}),
    "applied_to": frozenset({"application"}),
    "supports": frozenset({"capability"}),
    "connected_to": frozenset({"connection"}),
    "controls": frozenset({"control"}),
    "reports_fault": frozenset({"failure"}),
    "mitigates": frozenset({"mitigation"}),
    "used_in": frozenset({"deployment"}),
    "located_in": frozenset({"location"}),
    "manufactured_by": frozenset({"manufacture"}),
    "developed_by": frozenset({"development"}),
    "installed_on": frozenset({"installation"}),
    "distributed_via": frozenset({"distribution"}),
    "organizes": frozenset({"event_organization"}),
    "affiliated_with": frozenset({"affiliation"}),
    "packaged_with": frozenset({"package"}),
    "offered_by": frozenset({"ownership"}),
    "has_model": frozenset({"model_lineup"}),
    "equipped_with": frozenset({"equipment"}),
}
SCOPED_MODALITIES = frozenset({"conditional", "hypothetical", "design_goal", "planned"})


def predicate_basis(predicate, subject, obj):
    """Internal validator dispatch, derived from the reviewed predicate and types."""
    if predicate == "uses":
        return "data_use" if obj.entity_type == "data" else "use"
    if predicate == "contains":
        if subject.entity_type == "organization":
            return "org_structure" if obj.entity_type == "organization" else "composition"
        if obj.entity_type in {"software", "protocol", "technology", "method"}:
            return "capability"
        return "composition"
    return next(iter(BASIS_BY_PREDICATE[predicate]))


def grounding_instructions() -> str:
    return (
        "\n先独立判断原文实际动作，再判断谓词，不从候选倒推支持。\n"
        "共现、类比、影响/对抗、参数不能强改成兼容/应用/组成。"
        "包装随附不等于机体组成，品牌归属不证明制造或开发。\n"
        "modality=actual事实/capability能力/instruction步骤/conditional条件/"
        "hypothetical假设/design_goal目标/planned计划。后四项必须给scope_quote："
        "当前字幕中连续的范围原文，最长500字符；不把加装、假设、计划和目标变成既成事实。\n"
        "actual包含原文陈述的组成/品牌/兼容/功能事实；事实出现在教程中不自动变成instruction，"
        "instruction用于方法应用或安装操作建议。只对尚待检验的功能/配置用hypothetical，"
        "同一段中已实际执行的其他模式不一起降为假设。\n"
    )


def packaging_context(text: str, quote: str) -> bool:
    """Find an unboxing list, stopping at an explicit return to device description."""
    position = text.find(quote)
    if position < 0:
        return False
    prefix = text[max(0, position - 800):position + len(quote)]
    openers = list(re.finditer(
        r"also included|(?:in|inside) (?:the )?(?:box|package)|"
        r"(?:box|package) (?:contains|includes)|随(?:附|盒)|包装(?:内|中|清单)", prefix, re.I,
    ))
    if not openers:
        return False
    tail = prefix[openers[-1].end():]
    return not re.search(r"going back|next up|back to|回到|接下来|接著|接着", tail, re.I)


def composition_direction(relation, subject, obj, basis):
    """An explicit 'parts constitute whole' clause entails whole -> parts."""
    if (relation.predicate != "contains" or relation.assertion != "asserted"
        or basis not in {"composition", "org_structure"}):
        return relation, ""
    quote = re.sub(r"\s+", "", relation.quote)
    first = re.escape(re.sub(r"\s+", "", subject.surface_text))
    second = re.escape(re.sub(r"\s+", "", obj.surface_text))
    if re.search(r"并非|並非|不能说|不能說|不构成|不構成|does not|not constitute",
                 relation.quote, re.I):
        return relation, ""
    if re.search(first + r"(?:构成|構成|组成|組成)" + second, quote):
        return relation.model_copy(update={"subject": relation.object,
                                            "object": relation.subject}), "constituent_to_whole"
    return relation, ""


def military_inventory(relation, subject, obj, source):
    """Recognize a positive inventory list of a military unit, never a company's catalog."""
    if subject.entity_type != "organization" or obj.entity_type in {
        "organization", "person", "event", "document", "data", "country", "region",
    }:
        return False
    name = subject.surface_text
    if not re.search(r"方队|方隊|部队|部隊|军队|軍隊|军团|軍團|(?:旅|营|營)$|"
                     r"\b(?:formation|brigade|battalion|army)\b", name, re.I):
        return False
    if re.search(r"公司|集团|集團|company|corporation|\binc\b|\bltd\b", name, re.I):
        return False
    position = source.find(relation.quote)
    if position < 0:
        return False
    context = source[max(0, position - 120):position + len(relation.quote)]
    if not re.search(r"阅兵|閱兵|军|軍|military|parade|armed forces", context, re.I):
        return False
    compact = re.sub(r"\s+", "", relation.quote)
    start = compact.find(re.sub(r"\s+", "", name))
    object_at = compact.find(re.sub(r"\s+", "", obj.surface_text),
                             start + len(re.sub(r"\s+", "", name)))
    if start < 0 or object_at < start:
        return False
    link = compact[start + len(re.sub(r"\s+", "", name)):object_at]
    return (bool(re.search(r"包括|配备|配備|装备|裝備|equippedwith|includes", link, re.I))
            and not re.search(r"不|未|没有|沒有|不是|not", link, re.I))


def project_inventory(relation, subject, obj, basis, source):
    if (relation.predicate == "contains" and relation.assertion == "asserted"
        and basis in {"composition", "org_structure"}):
        if subject.entity_type != "organization" and obj.entity_type == "organization":
            reverse = relation.model_copy(update={"subject": relation.object,
                                                   "object": relation.subject})
            if military_inventory(reverse, obj, subject, source):
                relation, subject, obj = reverse, obj, subject
        if not military_inventory(relation, subject, obj, source):
            return relation, basis, ""
        position = source.find(relation.quote)
        context = source[max(0, position - 120):position + len(relation.quote)]
        scope = context if len(context) <= 500 else relation.quote
        if len(scope) > 500:
            return relation, basis, ""
        qualifiers = {**relation.qualifiers, "scope": scope}
        projected = relation.model_copy(update={"predicate": "equipped_with",
                                                 "qualifiers": qualifiers})
        return projected, "equipment", "military_inventory"
    return relation, basis, ""


def validate_support(
    relation: ExtractedRelation,
    subject: ExtractedEntity,
    obj: ExtractedEntity,
    *,
    basis: str,
    modality: str,
    scope_quote: str | None,
    source: str,
) -> ExtractedRelation:
    if basis not in BASIS_BY_PREDICATE[relation.predicate]:
        raise ValueError("审核的原文关系类别与谓词不一致")
    if relation.predicate == "equipped_with":
        scope = relation.qualifiers.get("scope", "")
        if not military_inventory(relation, subject, obj, source) or not source_span(source, scope):
            raise ValueError("装备投影缺少明确军事单位装备列举及范围依据")
    if modality not in Modality.__args__:
        raise ValueError("关系缺少有效的事实适用范围")
    if basis == "data_use" and obj.entity_type != "data":
        raise ValueError("被分析的数据不能强标技术或设备")
    if obj.entity_type == "data" and basis != "data_use":
        raise ValueError("使用数据需明确标记data_use依据")
    if basis == "org_structure" and (
        subject.entity_type != "organization" or obj.entity_type != "organization"
    ):
        raise ValueError("组织结构关系不能代替产品归属")
    if basis == "composition" and subject.entity_type == "organization":
        raise ValueError("组织不是硬件组成的主体")
    if relation.predicate == "packaged_with" and not packaging_context(source, relation.quote):
        if not re.search(r"included|supplied|comes? with|packaged with|shipped with|bundled with|"
                         r"包装|包裝|随附|隨附|附带|附帶|盒内|盒內",
                         relation.quote, re.I):
            raise ValueError("包装关系缺少包装随附依据")
    if relation.predicate == "supports" and modality in {"actual", "capability"} and re.search(
        r"^(?:the\s+)?goal\s+is|^目的是|^目标是|^目標是", relation.quote.strip(), re.I,
    ):
        raise ValueError("设计目标不能当作已实现的功能能力")
    if relation.predicate == "contains":
        membership = re.search(
            r"\b(?:contains?|includes?|has|have|inside|within|houses?)\b|"
            r"包含|包括|内部|內部|装有|裝有|构成|構成|组成|組成", relation.quote, re.I,
        )
        if not membership and re.search(r"变成|變成|成为|成為", relation.quote):
            raise ValueError("对象本身成为某系统不是包含另一个系统")
        if subject.entity_type == obj.entity_type == "component":
            mounted = re.search(
                r"\b(?:on|onto)\s+(?:a |an |the )?" + re.escape(obj.surface_text),
                relation.quote, re.I,
            )
            direct_membership = re.search(
                r"\b(?:contains?|includes?|has|have|houses?)\s+(?:a |an |the )?"
                + re.escape(obj.surface_text), relation.quote, re.I,
            )
            if mounted and not direct_membership:
                raise ValueError("部件安装在另一部件上不能证明前者包含后者")
        if re.search(r"protect(?:s|ion)?|保护|保護", relation.quote, re.I) and not re.search(
            r"\b(?:contains?|includes?|inside|within|houses?)\b|包含|包括|内部|內部|装有|裝有",
            relation.quote, re.I,
        ):
            # Protection alone is not meronymy. Explicit containment is still eligible.
            first = re.escape(subject.surface_text)
            second = re.escape(obj.surface_text)
            if re.search(first + r".{0,100}(?:protect|保护|保護).{0,100}" + second,
                         relation.quote, re.I | re.S):
                raise ValueError("保护关系不能证明保护物包含被保护对象")
        if subject.entity_type == "component" and obj.entity_type == "product" and not re.search(
            r"\b(?:contains?|includes?|has|have|inside|within)\b|内|內|包含|包括|装有|裝有|"
            r"容纳|容納|收纳|收納", relation.quote, re.I,
        ):
            raise ValueError("部件包含整机需要明确容纳或组成证据，重量描述不能证明包含")
        if packaging_context(source, relation.quote) and not re.search(
            r"inside (?:the )?(?:drone|aircraft|body)|installed (?:inside|in)|"
            r"机体内|机身内|內部|内部",
            relation.quote, re.I,
        ):
            raise ValueError("包装随附物不能作为机体组成，需使用packaged_with")
        if re.search(r"remaining battery|battery level|剩余电量|剩餘電量", relation.quote, re.I):
            if not re.search(
                r"battery compartment|remove|insert|install|connect|电池仓|電池倉|取出|装入|裝入",
                relation.quote, re.I,
            ):
                raise ValueError("UI电量读数不能证明物理电池组成")
        if re.search(
            r"再装上.*?(?:或者|或是)|(?:can|could|may).{0,40}(?:install|add)|"
            r"(?:install|add).{0,80}\bor\b", relation.quote, re.I | re.S,
        ) and modality == "actual":
            raise ValueError("可选加装示例不能成为无条件组成关系")
    if relation.predicate == "compatible_with":
        analogy = re.search(r"simulat|same functionality|same function|功能相同|功能类似|模拟|模擬",
                            relation.quote, re.I)
        compatibility = re.search(
            r"compatib|works? with|pair(?:ed|ing)?|built for|兼容|相容|配对|配對|支持",
            relation.quote,
            re.I,
        )
        if analogy and not compatibility:
            raise ValueError("功能模拟或类比不是兼容证据")
        if re.search(r"carry over|transfer.{0,30}(?:result|setting)|结果.*适用|結果.*適用",
                     relation.quote, re.I) and not compatibility:
            raise ValueError("参数区间的结果迁移不能证明两个方法互相兼容")
    if relation.predicate == "developed_by" and re.search(
        r"推出|发布|發佈|宣布|launch|announc", relation.quote, re.I,
    ) and not re.search(r"研发|研發|开发|開發|创造|創造|develop|invent|creat",
                        relation.quote, re.I):
        raise ValueError("发布或推出不等于开发创造")
    if relation.predicate == "manufactured_by" and not re.search(
        r"manufactur|made by|built by|produc(?:ed|tion)|制造|製造|生产|生產|厂商|廠商",
        relation.quote, re.I,
    ):
        raise ValueError("制造关系缺少明确制造或生产依据，品牌归属不能代替制造事实")
    if relation.predicate == "mitigates" and relation.assertion == "negated" and re.search(
        r"\b(?:all|every|always)\b|所有|全部|完全", relation.quote, re.I,
    ) and re.search(r"harmless|safe|eliminat|无害|無害|消除|杜绝|杜絕", relation.quote, re.I):
        raise ValueError("不能完全消除所有后果不等于不能减轻后果，不得丢失量词或强化否定")
    if relation.predicate == "applied_to" and re.search(
        r"挑战|挑戰|面临|面臨|challenge|threat", relation.quote, re.I,
    ) and not re.search(r"应用|應用|用于|用於|appl(?:y|ied|ication)|used (?:in|for)",
                        relation.quote, re.I):
        raise ValueError("影响或挑战关系不能代替应用关系")
    if relation.predicate == "applied_to" and obj.entity_type == "method" and not re.search(
        r"\b(?:appl\w*|for|using|via|through)\b|用于|用於|应用|應用|通过|透過|用来|用來",
        relation.quote, re.I,
    ):
        raise ValueError("操作流程中两个步骤先后出现不足以证明方法应用于另一方法")
    qualifiers = dict(relation.qualifiers)
    previous_modality = qualifiers.get("modality", "actual")
    if previous_modality in SCOPED_MODALITIES and modality not in SCOPED_MODALITIES:
        qualifiers.pop("scope", None)
    if modality == "actual":
        qualifiers.pop("modality", None)
    if relation.predicate == "affiliated_with" and re.search(
        r"工作背景|曾任|曾在|前任|\b(?:formerly|previously|former)\b", relation.quote, re.I,
    ) and not re.search(r"现任|現任|目前任|\bcurrently\b", relation.quote, re.I):
        if len(relation.quote) > 500:
            raise ValueError("过去任职范围引文过长，需要审核给出更精确的时间依据")
        qualifiers.update(time="past", scope=relation.quote)
    if modality in SCOPED_MODALITIES:
        grounded_scope = source_span(source, scope_quote or "")
        if not grounded_scope or len(grounded_scope) > 500:
            raise ValueError("条件、假设、设计目标或计划缺少逐字范围依据")
        qualifiers["scope"] = grounded_scope
    if modality != "actual":
        qualifiers["modality"] = modality
    if len(qualifiers) > 12:
        raise ValueError("关系限定条件过多")
    assertion = relation.assertion
    if modality in {"hypothetical", "design_goal", "planned"} and assertion == "asserted":
        assertion = "uncertain"
    return relation.model_copy(update={"qualifiers": qualifiers, "assertion": assertion})
