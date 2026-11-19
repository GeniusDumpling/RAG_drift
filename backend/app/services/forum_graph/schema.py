"""Strict model contracts. Assessment is a separate mandatory gate, never extraction."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.schemas.video_graph import Assertion, EntityType


class Record(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


def _coerce_string_map(value):
    if not isinstance(value, dict):
        return value
    return {
        k: (
            ("true" if v else "false")
            if isinstance(v, bool)
            else str(v)
            if isinstance(v, int | float)
            else v
        )
        for k, v in value.items()
    }


class Span(Record):
    post_id: int = Field(gt=0)
    start: int = Field(default=0, ge=0)
    end: int | None = Field(default=None, gt=0)
    quote: str = Field(min_length=1, max_length=12000)

    @model_validator(mode="after")
    def ordered(self):
        if self.end is not None and self.end <= self.start:
            raise ValueError("原文区间无效")
        return self


class Assessment(Record):
    uav_technical_related: bool | None
    has_graph_value: bool | None
    decision: Literal["extract", "skip", "defer"]
    reason_code: Literal[
        "technical_information",
        "unrelated",
        "no_usable_information",
        "duplicate",
        "context_only",
        "unparsed_attachment",
        "insufficient_context",
        "insufficient_coverage",
    ]
    reason: str = Field(min_length=1, max_length=1500)
    value_types: list[
        Literal[
            "symptom",
            "configuration",
            "procedure",
            "explanation",
            "technical_relation",
            "feedback",
        ]
    ] = Field(default_factory=list, max_length=6)
    supporting_spans: list[Span] = Field(default_factory=list, max_length=12)

    @model_validator(mode="after")
    def gate(self):
        if self.decision == "extract" and not (
            self.uav_technical_related is True
            and self.has_graph_value is True
            and self.supporting_spans
            and self.value_types
        ):
            raise ValueError("抽取须同时通过相关性与价值判断，且有原文依据")
        if self.decision == "skip" and not (
            self.uav_technical_related is False or self.has_graph_value is False
        ):
            raise ValueError("不能跳过尚未确定无关或无价值的材料")
        return self


class Case(Record):
    anchor_post_id: int = Field(gt=0)
    description: str = Field(min_length=1, max_length=1000)
    conditions: dict[str, str] = Field(default_factory=dict, max_length=12)
    evidence: list[Span] = Field(min_length=1, max_length=12)
    condition_rejections: dict[str, str] = Field(default_factory=dict, max_length=12)

    @field_validator("conditions", mode="before")
    @classmethod
    def coerce_conditions(cls, value):
        return _coerce_string_map(value)


class EntityCandidate(Record):
    key: str = Field(min_length=1, max_length=80)
    surface_text: str = Field(min_length=1, max_length=255)
    canonical_name: str = Field(min_length=1, max_length=255)
    entity_type: EntityType
    scope: Literal["global", "topic", "case"]
    case_anchor_post_id: int | None = Field(default=None, gt=0)
    evidence: list[Span] = Field(min_length=1, max_length=8)
    identity_evidence: list[Span] = Field(min_length=1, max_length=8)

    @model_validator(mode="after")
    def scoped(self):
        if (self.scope == "case") != (self.case_anchor_post_id is not None):
            raise ValueError("案例实体须且仅须指定案例锚点")
        return self


class Entities(Record):
    cases: list[Case] = Field(default_factory=list, max_length=12)
    entities: list[EntityCandidate] = Field(default_factory=list, max_length=48)


class Decision(Record):
    key: str
    accept: bool
    reason: str = Field(min_length=1, max_length=1500)


class Review(Record):
    decisions: list[Decision] = Field(max_length=160)


Act = Literal[
    "question",
    "observation",
    "technical_statement",
    "hypothesis",
    "recommendation",
    "test_report",
    "correction",
]
Outcome = Literal["unknown", "reported_success", "reported_failure", "mixed"]


class Claim(Record):
    key: str = Field(min_length=1, max_length=80)
    subject: str
    predicate: Literal[
        "uses",
        "contains",
        "compatible_with",
        "applied_to",
        "reports_fault",
        "mitigates",
        "installed_on",
        "supports",
        "connected_to",
        "controls",
        "has_parameter",
        "instruction",
        "indicates",
        "offered_by",
        "developed_by",
        "manufactured_by",
    ]
    object: str | None = Field(
        default=None, description="仅relation填已审核实体key；state/attribute/instruction必须为null"
    )
    value: str | None = Field(
        default=None,
        max_length=3000,
        description="state/attribute/instruction填文本值；relation必须为null；不能与object同时填写",
    )
    attribute: str | None = Field(default=None, max_length=180)
    kind: Literal["relation", "attribute", "state", "instruction"] = Field(
        description="必填，禁止省略。relation用实体客体；其余类型用文本值"
    )
    assertion: Assertion
    act: Act
    case_anchor_post_id: int | None = Field(default=None, gt=0)
    qualifiers: dict[str, str] = Field(default_factory=dict, max_length=16)
    details: dict[str, str | list[str]] = Field(default_factory=dict, max_length=12)
    evidence: list[Span] = Field(min_length=1, max_length=12)

    @field_validator("qualifiers", mode="before")
    @classmethod
    def coerce_qualifiers(cls, value):
        return _coerce_string_map(value)

    @model_validator(mode="after")
    def shape(self):
        if (self.object is None) == (self.value is None):
            raise ValueError("主张须且只能有实体客体或值")
        if (self.kind == "relation") != (self.object is not None):
            raise ValueError(
                "kind=relation须object非空且value为null；其余kind须object=null且value非空"
            )
        if self.kind == "attribute" and not self.attribute:
            raise ValueError("属性主张缺少属性名")
        if self.act == "hypothesis" and self.assertion != "uncertain":
            raise ValueError("假设不能作为确定事实")
        if self.act == "recommendation" and self.kind != "instruction":
            raise ValueError("建议须保留为操作建议，不是实际配置或已生效关系")
        return self


class Evaluation(Record):
    key: str = Field(min_length=1, max_length=80)
    targets: list[str] = Field(default_factory=list, max_length=8)
    case_anchor_post_id: int = Field(gt=0)
    outcome: Outcome
    combined_operations: bool = False
    evidence: list[Span] = Field(min_length=1, max_length=12)

    @model_validator(mode="after")
    def combined(self):
        if len(self.targets) > 1 and not self.combined_operations:
            raise ValueError("多项操作的联合结果不能拆成独立有效性")
        return self


class Claims(Record):
    claims: list[Claim] = Field(default_factory=list, max_length=64)
    evaluations: list[Evaluation] = Field(default_factory=list, max_length=24)
