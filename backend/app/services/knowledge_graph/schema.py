"""Source-independent publication contract. Source locators remain typed extension data."""

import hashlib
import json
import unicodedata
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

SCHEMA_VERSION = 1
ADAPTER_VERSION = "video-document-v1"


def encode(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def digest(value):
    return hashlib.sha256(encode(value).encode()).hexdigest()


def normalized(value):
    return " ".join(unicodedata.normalize("NFKC", value).casefold().split())


def source_key(source_type, external_id):
    return f"{source_type}:{external_id}"


class Record(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class Evidence(Record):
    # A reviewed image can be the only evidence; do not invent a text quote.
    quote: str
    locator: dict = Field(min_length=1)
    review_id: str = Field(min_length=1)
    confidence: float | None = Field(default=None, ge=0, le=1, allow_inf_nan=False)


class Entity(Record):
    id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    entity_type: str = Field(min_length=1)
    scope: str = Field(min_length=1)
    mentions: list[Evidence] = Field(default_factory=list)


class Fact(Record):
    subject: str
    predicate: str = Field(min_length=1)
    object: str | None = None
    value: str | None = None
    attribute: str | None = None
    kind: Literal["relation", "attribute", "state", "instruction"] = "relation"
    assertion: Literal["asserted", "uncertain", "negated"] = "asserted"
    qualifiers: dict = Field(default_factory=dict)
    details: dict = Field(default_factory=dict)
    evidence: list[Evidence] = Field(min_length=1)

    @model_validator(mode="after")
    def valid_shape(self):
        if (self.object is None) == (self.value is None):
            raise ValueError("事实需要且只能有实体客体或属性值")
        if (self.kind == "relation") != (self.object is not None):
            raise ValueError("关系事实必须有实体客体；属性、状态、操作必须有值")
        if self.kind == "attribute" and not self.attribute:
            raise ValueError("属性事实缺少属性名")
        return self

    @property
    def id(self):
        return digest(self.model_dump(exclude={"evidence"}))


class Publication(Record):
    schema_version: Literal[1] = SCHEMA_VERSION
    adapter_version: str = ADAPTER_VERSION
    source_type: str = Field(min_length=1, pattern=r"^[a-z][a-z0-9_]*$")
    external_id: str = Field(min_length=1)
    native_build_id: str = Field(min_length=1)
    source_fingerprint: str = Field(min_length=1)
    extractor_version: str = Field(min_length=1)
    title: str
    url: str
    metadata: dict = Field(default_factory=dict)
    entities: list[Entity]
    facts: list[Fact]

    @model_validator(mode="after")
    def valid_graph(self):
        ids = [e.id for e in self.entities]
        if len(set(ids)) != len(ids):
            raise ValueError("统一实体 ID 重复")
        for fact in self.facts:
            if fact.subject not in ids or (fact.object is not None and fact.object not in ids):
                raise ValueError("事实端点不属于当前发布")
        return self

    @property
    def source_id(self):
        return source_key(self.source_type, self.external_id)

    @property
    def payload_hash(self):
        return digest(self.model_dump())

    @property
    def build_id(self):
        # A partial native build may be reactivated with more evidence. Each
        # shared projection remains immutable and has its own revision ID.
        return digest([self.source_id, self.native_build_id, self.payload_hash])
