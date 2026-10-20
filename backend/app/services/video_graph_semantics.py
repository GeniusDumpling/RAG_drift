"""Offline type rules for new video extractions; never reclassify stored entities.

The geography vocabulary is deliberately conservative and incomplete. Exact names
catch obvious classification mistakes; unknown and ambiguous names still require
contextual classification by the extractor. No substring-based geocoding is used.
"""

import unicodedata

from app.schemas.video_graph import EntityType, ExtractedEntity, ExtractedRelation, Predicate

ENTITY_TYPE_DEFINITIONS: dict[EntityType, str] = {
    "organization": "有名称的公司、政府机关、军队、研究机构或其他组织；国家本身不是组织",
    "product": "具体产品或型号，不用制造商名称代替产品名称",
    "component": "具体硬件部件或模块，不把国家、地区当作组件",
    "software": "有名称的软件、固件、操作系统或软件平台",
    "protocol": "通信、接口或数据交换协议",
    "technology": "定位、通信、感知等技术，不把采用技术的设备与技术混淆",
    "method": "明确的操作、检测、维修或反制方法",
    "fault": "具体故障、失效或异常，不把地名当作故障",
    "country": "国家实体，如乌克兰、Ukraine、法国；不包括政府、军队等具名组织",
    "region": "非国家的具名地理区域，包括省州、城市、洲和跨国区域，如基辅、东欧",
}

# Exact, unambiguous names only. In particular, Georgia, Jordan, Chad, Turkey,
# Amazon and Java are not forced to a type by a context-free English lookup.
COUNTRY_NAMES = frozenset(
    "乌克兰|ukraine|україна|украина|俄罗斯|russia|россия|russian federation|"
    "中国|中华人民共和国|china|"
    "美国|美利坚合众国|united states|united states of america|英国|united kingdom|"
    "法国|france|德国|germany|日本|japan|韩国|south korea|朝鲜|north korea|"
    "加拿大|canada|澳大利亚|australia|新西兰|new zealand|印度|india|巴基斯坦|pakistan|"
    "以色列|israel|伊朗|iran|土耳其|波兰|poland|瑞典|sweden|芬兰|finland|"
    "挪威|norway|丹麦|denmark|西班牙|spain|意大利|italy|荷兰|netherlands|"
    "瑞士|switzerland|巴西|brazil|墨西哥|mexico|沙特阿拉伯|saudi arabia|"
    "阿联酋|united arab emirates|叙利亚|syria|伊拉克|iraq|黎巴嫩|lebanon|"
    "也门|yemen|阿富汗|afghanistan|白俄罗斯|belarus|爱沙尼亚|estonia|"
    "拉脱维亚|latvia|立陶宛|lithuania|捷克|czechia|czech republic|罗马尼亚|romania|"
    "新加坡|singapore|马来西亚|malaysia|印度尼西亚|indonesia|越南|vietnam|"
    "泰国|thailand|菲律宾|philippines|南非|south africa|埃及|egypt|"
    "肯尼亚|kenya|尼日利亚|nigeria|约旦|乍得|格鲁吉亚".split("|")
)
REGION_NAMES = frozenset(
    "欧洲|europe|亚洲|asia|非洲|africa|北美洲|north america|南美洲|south america|"
    "大洋洲|oceania|南极洲|antarctica|东欧|eastern europe|西欧|western europe|"
    "东亚|east asia|东南亚|southeast asia|中东|middle east|亚太地区|asia-pacific|"
    "基辅|kyiv|kiev|顿巴斯|donbas|donbass|克里米亚|crimea|"
    "黑海|black sea|波罗的海|baltic sea|乌克兰东部|eastern ukraine".split("|")
)

TECHNICAL_TYPES: frozenset[EntityType] = frozenset(
    {"product", "component", "software", "protocol", "technology", "method"}
)
GEOGRAPHIC_TYPES: frozenset[EntityType] = frozenset({"country", "region"})
RELATION_TYPE_RULES: dict[Predicate, tuple[frozenset[EntityType], frozenset[EntityType]]] = {
    "uses": (TECHNICAL_TYPES | {"organization"}, TECHNICAL_TYPES),
    "contains": (TECHNICAL_TYPES | {"organization"}, TECHNICAL_TYPES | {"organization"}),
    "compatible_with": (TECHNICAL_TYPES, TECHNICAL_TYPES),
    "applied_to": (
        frozenset({"method", "technology", "software", "protocol"}),
        TECHNICAL_TYPES | {"organization", "fault"},
    ),
    "reports_fault": (
        frozenset({"product", "component", "software", "protocol", "technology"}),
        frozenset({"fault"}),
    ),
    "mitigates": (TECHNICAL_TYPES, frozenset({"fault"})),
    "used_in": (TECHNICAL_TYPES, GEOGRAPHIC_TYPES),
    "located_in": (GEOGRAPHIC_TYPES | {"organization"}, GEOGRAPHIC_TYPES),
}


class EntitySemanticError(ValueError):
    """A mention contradicts an unambiguous known geographic type."""


class RelationSemanticError(ValueError):
    """A relation has incompatible endpoint roles."""


def normalized_name(value: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", value).split()).casefold()


def semantic_instructions() -> str:
    definitions = "；".join(f"{kind}：{text}" for kind, text in ENTITY_TYPE_DEFINITIONS.items())
    roles = "；".join(
        f"{predicate}：{','.join(sorted(subjects))} -> {','.join(sorted(objects))}"
        for predicate, (subjects, objects) in RELATION_TYPE_RULES.items()
    )
    return (
        f"实体类型定义：{definitions}。关系两端类型约束：{roles}。"
        "used_in 表示设备、软件、技术或方法在某国家/地区明确被使用或部署，"
        "located_in 表示组织或地理实体的明确位置/包含归属；两者方向均指向国家/地区，"
        "quote 必须包含该地理对象的逐字原名。购买、提及、制造商国籍不能推导为 used_in。"
        "国家参与采购或行动时，不能为适配 uses 而把国家改标 organization。"
        "只在原文明确出现完整组织名称时抽取政府/军队，例如乌克兰政府是 organization，"
        "乌克兰是 country，东欧是 region，欧盟是 organization。"
        "同名词须结合上下文判断，例如 Jordan 可以是品牌，Java 可以是软件名称；"
        "不要按地名子串判断组织或产品。无法判断类型或超出类型范围时省略该实体及关联关系，"
        "不要强行归类，也不要把规范全称补入 surface_text。"
    )


def validate_entity_semantics(entity: ExtractedEntity) -> None:
    name = normalized_name(entity.surface_text)
    expected = "country" if name in COUNTRY_NAMES else "region" if name in REGION_NAMES else None
    if expected is not None and entity.entity_type != expected:
        raise EntitySemanticError(f"已知地理实体类型冲突，必须使用 {expected}")


def validate_relation_semantics(
    relation: ExtractedRelation, subject: ExtractedEntity, obj: ExtractedEntity
) -> None:
    subject_types, object_types = RELATION_TYPE_RULES[relation.predicate]
    if subject.entity_type not in subject_types or obj.entity_type not in object_types:
        raise RelationSemanticError(f"关系 {relation.predicate} 的主语或宾语类型不符合约束")
    if (
        relation.predicate == "located_in"
        and subject.entity_type == "country"
        and obj.entity_type == "country"
    ):
        raise RelationSemanticError("located_in 不支持国家到国家的位置归属")
