import uuid
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

EntityType = Literal[
    "organization",
    "product",
    "component",
    "software",
    "protocol",
    "technology",
    "method",
    "fault",
    "country",
    "region",
]
Predicate = Literal[
    "uses",
    "contains",
    "compatible_with",
    "applied_to",
    "reports_fault",
    "mitigates",
    "used_in",
    "located_in",
]
Assertion = Literal["asserted", "uncertain", "negated"]
ShortText = Annotated[str, Field(min_length=1, max_length=255)]


class ExtractedEntity(BaseModel):
    model_config = ConfigDict(extra="forbid")
    key: ShortText
    surface_text: ShortText
    entity_type: EntityType
    confidence: float = Field(default=0.7, ge=0, le=1, allow_inf_nan=False)


class ExtractedRelation(BaseModel):
    model_config = ConfigDict(extra="forbid")
    subject: ShortText
    object: ShortText
    predicate: Predicate
    quote: str = Field(min_length=1, max_length=6000)
    assertion: Assertion
    confidence: float = Field(ge=0, le=1, allow_inf_nan=False)
    qualifiers: dict[str, str] = Field(default_factory=dict, max_length=12)


class ChunkExtraction(BaseModel):
    model_config = ConfigDict(extra="forbid")
    entities: list[ExtractedEntity] = Field(default_factory=list, max_length=60)
    relations: list[ExtractedRelation] = Field(default_factory=list, max_length=80)


class VideoAssessment(BaseModel):
    model_config = ConfigDict(extra="forbid")
    relevant: bool
    reason: str = Field(max_length=1000)


class GraphNode(BaseModel):
    id: str
    label: str
    kind: Literal["entity", "video"]
    entity_type: str | None = None
    content_item_id: uuid.UUID | None = None


class GraphEdge(BaseModel):
    id: str
    source: str
    target: str
    predicate: str
    kind: Literal["relation", "mention"]
    evidence_count: int = 0
    assertions: dict[str, int] = Field(default_factory=dict)
    qualifiers: dict[str, str] = Field(default_factory=dict)


class GraphData(BaseModel):
    nodes: list[GraphNode]
    edges: list[GraphEdge]
    truncated: bool = False


class GraphEvidenceRead(BaseModel):
    id: uuid.UUID
    relation_id: uuid.UUID
    content_item_id: uuid.UUID
    chunk_id: uuid.UUID | None
    title: str | None
    video_url: str
    quote: str
    start_seconds: float | None
    end_seconds: float | None
    assertion: Assertion
    confidence: float
    caption_source: str | None
    extractor_version: str


class GraphRetrieveRequest(BaseModel):
    query: str = Field(min_length=1, max_length=1000)
    top_k: int = Field(default=10, ge=1, le=30)
    max_chars: int = Field(default=12000, ge=500, le=30000)
    content_item_id: uuid.UUID | None = None


class GraphRetrieveResponse(BaseModel):
    graph: GraphData
    evidence: list[GraphEvidenceRead]
    context: str
