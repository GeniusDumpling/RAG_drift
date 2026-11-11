"""Extraction proposals and explicit native-text / visual-source references."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

Kind = Literal["product", "component", "software", "protocol", "technology", "method"]
Predicate = Literal[
    "contains",
    "installed_on",
    "connected_to",
    "compatible_with",
    "supports",
    "applied_to",
    "uses",
    "controls",
    "has_parameter",
    "indicates",
    "instruction",
    "located_on",
]


class Evidence(BaseModel):
    model_config = ConfigDict(extra="forbid")
    page: int = Field(ge=1)
    text_ids: list[str] = Field(default_factory=list, max_length=16)
    region_ids: list[str] = Field(default_factory=list, max_length=4)
    visual_label: str | None = Field(default=None, max_length=120)

    @model_validator(mode="after")
    def has_source(self):
        if not self.text_ids and not self.region_ids:
            raise ValueError("证据需要原生文本行或图片区域")
        if self.visual_label and not self.region_ids:
            raise ValueError("视觉标注需要图片区域")
        return self


class Entity(BaseModel):
    model_config = ConfigDict(extra="forbid")
    key: str = Field(min_length=1, max_length=40)
    surface_text: str = Field(min_length=1, max_length=180)
    entity_type: Kind
    evidence: list[Evidence] = Field(min_length=1, max_length=4)


class Fact(BaseModel):
    model_config = ConfigDict(extra="forbid")
    subject: str
    predicate: Predicate
    object: str | None = None
    attribute: str | None = Field(default=None, max_length=180)
    value: str | None = Field(default=None, max_length=500)
    qualifiers: dict[str, str] = Field(default_factory=dict, max_length=8)
    assertion: Literal["asserted", "uncertain", "negated"] = "asserted"
    led_pattern: list[Literal["steady", "blinking", "off"]] | None = Field(
        default=None, min_length=4, max_length=4
    )
    operating_state: Literal["charging", "level_check", "powered_on"] | None = None
    evidence: list[Evidence] = Field(min_length=1, max_length=4)

    @model_validator(mode="after")
    def fact_shape(self):
        if (self.object is None) == (self.value is None):
            raise ValueError("关系对象与属性/状态/指令值必须且只能给一个")
        if self.predicate == "has_parameter" and (not self.attribute or self.value is None):
            raise ValueError("参数事实需要参数名与原文值")
        if self.predicate == "indicates" and (not self.led_pattern or not self.operating_state):
            raise ValueError("灯态事实必须区分操作状态与四灯模式")
        if self.led_pattern and not any(e.region_ids for e in self.evidence):
            raise ValueError("灯态模式必须有图片证据")
        if self.predicate in {"has_parameter", "indicates", "instruction"} and self.object:
            raise ValueError("参数/灯态/操作记录不把数值或动作建立为实体")
        return self


class Proposal(BaseModel):
    model_config = ConfigDict(extra="forbid")
    entities: list[Entity] = Field(default_factory=list, max_length=40)
    facts: list[Fact] = Field(default_factory=list, max_length=60)
