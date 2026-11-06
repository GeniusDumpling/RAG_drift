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
    "person",
    "event",
    "data",
    "document",
    "product_family",
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
    "manufactured_by",
    "developed_by",
    "installed_on",
    "distributed_via",
    "organizes",
    "affiliated_with",
    "supports",
    "connected_to",
    "controls",
    "packaged_with",
    "offered_by",
    "has_model",
    "equipped_with",
]
Assertion = Literal["asserted", "uncertain", "negated"]
ShortText = Annotated[str, Field(min_length=1, max_length=255)]


class ExtractedEntity(BaseModel):
    model_config = ConfigDict(extra="forbid")
    key: ShortText
    surface_text: ShortText
    entity_type: EntityType
    confidence: float = Field(default=0.7, ge=0, le=1, allow_inf_nan=False)
    canonical_name: ShortText | None = None
    identity_scope: Literal["global", "video"] | None = None
    identity_quote: str | None = Field(default=None, max_length=6000)
    identity_source: Literal["current_chunk", "neighbor_context", "title"] | None = None
    attributes: dict[str, str] = Field(default_factory=dict, max_length=12)


class ExtractedRelation(BaseModel):
    model_config = ConfigDict(extra="forbid")
    subject: ShortText
    object: ShortText
    predicate: Predicate
    quote: str = Field(min_length=1, max_length=6000)
    assertion: Assertion
    confidence: float = Field(ge=0, le=1, allow_inf_nan=False)
    qualifiers: dict[str, str] = Field(default_factory=dict, max_length=12)


class QualityReview(BaseModel):
    model_config = ConfigDict(extra="forbid")
    policy: str
    input_hash: str
    result_hash: str
    candidate_entities: int = Field(ge=0)
    candidate_relations: int = Field(ge=0)
    rejected: list[dict[str, str]] = Field(default_factory=list)
    relation_support: list[dict[str, str]] = Field(default_factory=list)


class ChunkExtraction(BaseModel):
    model_config = ConfigDict(extra="forbid")
    entities: list[ExtractedEntity] = Field(default_factory=list, max_length=60)
    relations: list[ExtractedRelation] = Field(default_factory=list, max_length=80)
    schema_version: Literal[1, 2] = 1
    review: QualityReview | None = None


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
    qualifiers: dict[str, str] = Field(default_factory=dict)


class GraphRetrieveRequest(BaseModel):
    query: str = Field(min_length=1, max_length=1000)
    top_k: int = Field(default=10, ge=1, le=30)
    max_chars: int = Field(default=12000, ge=500, le=30000)
    content_item_id: uuid.UUID | None = None


class GraphRetrieveResponse(BaseModel):
    graph: GraphData
    evidence: list[GraphEvidenceRead]
    context: str
