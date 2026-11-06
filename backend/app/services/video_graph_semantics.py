"""Offline type rules for new video extractions; never reclassify stored entities.

The geography vocabulary is deliberately conservative and incomplete. Exact names
catch obvious classification mistakes; unknown and ambiguous names still require
contextual classification by the extractor. No substring-based geocoding is used.
"""

import re
import unicodedata

from app.schemas.video_graph import EntityType, ExtractedEntity, ExtractedRelation, Predicate

ENTITY_TYPE_DEFINITIONS: dict[EntityType, str] = {
    "organization": "公司/机构/军队", "product": "整机/遥控器/飞行眼镜/工具",
    "component": "硬件部件", "software": "软件/固件/平台", "protocol": "通信协议",
    "technology": "技术/功能", "method": "操作/维修方法", "fault": "具体故障",
    "country": "国家", "region": "地理区域/城市", "person": "具名个人",
    "event": "具名活动", "data": "日志/轨迹等数据资源", "document": "说明资料",
    "product_family": "具名产品系列",
}

PREDICATE_DEFINITIONS: dict[Predicate, str] = {
    "uses": "使用", "contains": "组成/功能包含", "compatible_with": "可配用",
    "applied_to": "应用对象", "reports_fault": "设备出现异常，不要求报警", "mitigates": "缓解故障",
    "used_in": "使用地", "located_in": "所在地", "manufactured_by": "制造者",
    "developed_by": "开发者", "installed_on": "安装于", "distributed_via": "分发渠道",
    "organizes": "举办", "affiliated_with": "任职/隶属", "supports": "支持功能/协议",
    "connected_to": "连接", "controls": "操控设备/改变设置", "packaged_with": "包装随附",
    "offered_by": "所属公司/品牌", "has_model": "系列型号", "equipped_with": "单位装备",
}

# More precise selection guidance for the model; keep historical display labels stable.
PREDICATE_PROMPT_DEFINITIONS = {
    **PREDICATE_DEFINITIONS,
    "uses": "设备/部件→内置运行的软件/固件、所用技术或数据；外部操作/素材工作流用反向applied_to",
    "contains": "整体→自身部件/模块，组织→子机构；不是功能、系列或包装随附",
    "supports": "设备→支持的具名功能/模式/协议；手动教程动作不是内置功能",
    "compatible_with": "主设备→可配用的设备/配件，包括明确搭配或实际配套使用",
    "applied_to": "方法/外部工作流软件→应用设备，如首次使用App、教程操作、素材导入或后制",
    "reports_fault": "异常描述的设备→故障；保留If条件，不把推测失效部件换成异常主体",
    "mitigates": "技术/方法/部件→缓解的具体故障或风险，不表示完全杜绝",
    "installed_on": "部件→安装载体/位置，安装配对教程保留instruction",
    "connected_to": "对象→直接机械/电气/设备连接；配套使用不自动证明连接",
    "packaged_with": "产品→开箱/自带/随附物品；个人使用清单不证明原厂包装",
    "offered_by": "产品/系列→正文明确的品牌/所属公司，所有格、品牌加型号或推出可作依据",
    "has_model": "系列→其型号/代际；比较、整合或替代旧机型不自动产生归属",
}

RELATION_SELECTION_RULES = (
    "按原文选择最具体关系，不为同一事实重复输出近义边。"
    "品牌归属不提升为制造/开发；不从规范名里新增正文未表达的品牌事实。"
    "方法用于设备选applied_to；机体部件选contains；设备功能选supports；"
    "可搭配的完整遥控器/眼镜或可选配件选compatible_with。"
    "正文的‘厂牌的型号’、‘Vendor Model’或‘厂牌推出型号’已表达offered_by，"
    "不要求出现制造或组织隶属字样；系列更新介绍中列出的代际/高低配型号可has_model。"
    "设备经USB接电脑、程序导入/编辑该设备素材时，程序applied_to设备，不能把软件当USB硬件端点。"
    "外部App下载/安装/操作教程统一软件→applied_to→设备；设备内置固件仍设备→uses→固件，不互换。"
    "正文品牌+型号可表达产品归属；发表时间推测不自动把品牌归属变为推测。"
    "同一维修/拆解主体的物主词、拆换对象及连续指代可表达组成或连接，不要求字面出现包含；"
    "跨设备对比、共现和可选加装不据此补成确定事实。"
    "同规格或对比不证明同一物理部件；both/它等按完整上下文确定先行词，"
    "否定或插入比较句的最近型号不自动成为后句主体。\n"
)

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
    {"product", "component", "software", "protocol", "technology", "method", "product_family"}
)
GEOGRAPHIC_TYPES: frozenset[EntityType] = frozenset({"country", "region"})
# These are source-verified rule projections, not part of the LLM's candidate language.
DERIVED_PREDICATES = frozenset({"equipped_with"})
RELATION_TYPE_RULES: dict[Predicate, tuple[frozenset[EntityType], frozenset[EntityType]]] = {
    "uses": (TECHNICAL_TYPES | {"organization"}, TECHNICAL_TYPES | {"data"}),
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
    "manufactured_by": (frozenset({"product", "component"}), frozenset({"organization"})),
    "developed_by": (TECHNICAL_TYPES, frozenset({"organization", "person"})),
    "installed_on": (frozenset({"product", "component"}), TECHNICAL_TYPES),
    "distributed_via": (frozenset({"software"}), frozenset({"software", "organization"})),
    "organizes": (frozenset({"organization", "person"}), frozenset({"event"})),
    "affiliated_with": (frozenset({"person"}), frozenset({"organization"})),
    "supports": (TECHNICAL_TYPES, frozenset({"software", "protocol", "technology", "method"})),
    "connected_to": (TECHNICAL_TYPES, TECHNICAL_TYPES),
    "controls": (TECHNICAL_TYPES | {"person", "organization"}, TECHNICAL_TYPES),
    "packaged_with": (frozenset({"product"}), TECHNICAL_TYPES | {"document"}),
    "offered_by": (frozenset({"product", "component", "software", "product_family"}),
                   frozenset({"organization"})),
    "has_model": (frozenset({"product_family"}), frozenset({"product"})),
    "equipped_with": (frozenset({"organization"}), TECHNICAL_TYPES),
}


class EntitySemanticError(ValueError):
    """A mention contradicts an unambiguous known geographic type."""


class RelationSemanticError(ValueError):
    """A relation has incompatible endpoint roles."""


def relation_pair_question(first, second):
    """Ask about plausible roles, never assert an edge from type compatibility alone."""
    kinds = {first.entity_type, second.entity_type}
    if "organization" in kinds and kinds & {"product", "component", "software", "product_family"}:
        return "正文是否表达产品→offered_by→品牌？品牌+型号可作依据，不要求制造或组织隶属。"
    if kinds == {"product_family", "product"}:
        return "正文系列是否列出该型号/代际？有依据用系列→has_model→型号，比较不等于归属。"
    if kinds & {"software", "method"} and kinds & {"product", "component"}:
        return "区分外部方法/软件→applied_to→设备，与设备→uses→内置固件；不是软件与USB硬件连接。"
    if kinds == {"product", "component"}:
        return "区分整机→contains→自身部件、部件→installed_on→载体及产品→packaged_with→随附物；核对跨句归属。"
    if kinds == {"component"}:
        return "原文有控制controls、接线/拆焊connected_to、安装installed_on或组成contains吗？按角色定向。"
    if kinds == {"product"}:
        return "实际配用可compatible_with；个人携带清单不等于packaged_with，不能因非包装而忽略配用。"
    return ""


def canonicalize_relation_expression(relation, subject, obj, text):
    """Canonicalize an evidenced external workflow; preserve onboard use and all scope fields."""
    from app.services.video_graph_grounding import source_span

    if relation.predicate not in {"uses", "connected_to"}:
        return relation, ""
    if subject.entity_type in {"software", "method"} and obj.entity_type in {"product", "component"}:
        tool, device = subject, obj
    elif obj.entity_type in {"software", "method"} and subject.entity_type in {"product", "component"}:
        tool, device = obj, subject
    else:
        return relation, ""
    quote = source_span(text, relation.quote)
    if quote is None or re.search(
        r"\bfirmware\b|\bautopilot\b|固件|韌體|飞控固件|飛控韌體",
        tool.surface_text + " " + (tool.canonical_name or ""), re.I,
    ):
        return relation, ""
    external = re.search(
        r"\b(?:download\w*|install\w*)\b.{0,120}\bapp\b|"
        r"\bedit\w*\b.{0,80}\b(?:footage|video|clips?)\b|"
        r"\bimport\w*\b.{0,80}\b(?:footage|video|files?)\b|"
        r"后制|後製|后期|後期|剪辑|剪輯|匯入|导入|設備連接|设备连接",
        quote, re.I | re.S,
    )
    opened = re.search(
        r"(?:\bopen\w*\s+(?:the\s+)?|開啟\s*|开启\s*|打开\s*)"
        + re.escape(tool.surface_text), quote, re.I,
    )
    if not external and not opened:
        return relation, ""
    return relation.model_copy(update={
        "predicate": "applied_to", "subject": tool.key, "object": device.key,
    }), "external_workflow_to_device"


def normalized_name(value: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", value).split()).casefold()


def semantic_instructions(*, include_predicates: bool = True, include_types: bool = True) -> str:
    definitions = "；".join(f"{kind}={text}" for kind, text in ENTITY_TYPE_DEFINITIONS.items())
    rules = f"\n实体类型：{definitions}。\n" if include_types else "\n"
    if include_predicates:
        rules += "谓词：" + "；".join(
            f"{name}={meaning}" for name, meaning in PREDICATE_PROMPT_DEFINITIONS.items()
            if name not in DERIVED_PREDICATES
        ) + "。\n"
        rules += RELATION_SELECTION_RULES
    return rules + (
        "国名是country，乌克兰政府等明确机构是organization；不按地名子串猜类型。"
        "used_in/located_in指向country/region，须有逐字地理证据；国籍或采购不证明部署。"
        "无法判断或无合适类型/关系则省略。\n"
    )

def validate_entity_semantics(entity: ExtractedEntity) -> None:
    name = normalized_name(entity.surface_text)
    if name in {"cpu load", "battery level", "remaining battery", "remaining power",
                "remaining battery level", "cpu负载", "cpu负載", "剩余电量", "剩餘電量",
                "speed", "acceleration", "deceleration", "emissivity", "速度", "加速度",
                "发射率", "發射率", "home point", "gps availability", "升力", "lift"}:
        raise EntitySemanticError("参数名称应保存为属性或测量背景，不能强标技术实体")
    if entity.entity_type == "software" and re.search(
        r"(?:\bscreen|\bpage|\bmode|\bwarnings|\bareas)$|界面|介面|模式$|页面$|頁面$",
        name,
    ):
        raise EntitySemanticError("界面页面、模式和显示项不是软件程序，不能沿用候选软件类型")
    if entity.entity_type == "software" and name in {"map", "chart", "地图", "地圖"}:
        raise EntitySemanticError("通用地图或图表资源不是软件程序")
    if entity.entity_type == "component" and re.search(r"设计$|設計$|\bdesign$", name):
        raise EntitySemanticError("部件设计概念不是物理硬件部件")
    if entity.entity_type == "technology" and name in {
        "姿态指令", "姿態指令", "方向指令", "速度指令", "位置指令",
        "attitude command", "attitude commands", "position command", "position commands",
    }:
        raise EntitySemanticError("控制输入指令不是技术本身，不能混用命令与控制技术")
    if entity.entity_type == "component" and re.search(r"\bvirtual\b|虚拟|虛擬", name):
        raise EntitySemanticError("虚拟界面控件不是硬件部件")
    if entity.entity_type in {"product", "component"} and re.search(
        r"\b(?:qr code|information|instruction|safety)\s+cards?\b|"
        r"二维码.*卡|二維碼.*卡|信息卡|資訊卡|说明书|說明書", name,
    ):
        raise EntitySemanticError("信息卡和说明资料应使用document类型")
    expected = "country" if name in COUNTRY_NAMES else "region" if name in REGION_NAMES else None
    if expected is not None and entity.entity_type != expected:
        raise EntitySemanticError(f"已知地理实体类型冲突，必须使用 {expected}")


def validate_relation_semantics(
    relation: ExtractedRelation, subject: ExtractedEntity, obj: ExtractedEntity
) -> None:
    subject_types, object_types = RELATION_TYPE_RULES[relation.predicate]
    if subject.entity_type not in subject_types or obj.entity_type not in object_types:
        raise RelationSemanticError(f"关系 {relation.predicate} 的主语或宾语类型不符合约束")
    if (relation.predicate == "contains" and
        ((subject.entity_type == "organization") != (obj.entity_type == "organization"))):
        raise RelationSemanticError("组织包含关系仅表达组织结构，产品归属应使用offered_by")
    if relation.predicate == "contains" and "product_family" in {
        subject.entity_type, obj.entity_type
    }:
        raise RelationSemanticError("产品系列不是机体，系列与型号应使用has_model")
    if (
        relation.predicate == "located_in"
        and subject.entity_type == "country"
        and obj.entity_type == "country"
    ):
        raise RelationSemanticError("located_in 不支持国家到国家的位置归属")


def orient_relation_by_types(relation, subject, obj):
    """Repair a uniquely typed direction, leaving ambiguous or invalid pairs to review."""
    try:
        validate_relation_semantics(relation, subject, obj)
        return relation
    except RelationSemanticError:
        try:
            validate_relation_semantics(relation, obj, subject)
        except RelationSemanticError:
            return relation
        return relation.model_copy(update={"subject": relation.object, "object": relation.subject})
