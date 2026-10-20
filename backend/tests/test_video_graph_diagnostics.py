import json
import uuid

import httpx
import pytest
from app.schemas.video_graph import ChunkExtraction
from app.services.video_graph import VideoChunk, locate_quote, validate_extraction
from app.services.video_graph_diagnostics import describe_graph_error, graph_error_summary
from pydantic import ValidationError

pytestmark = pytest.mark.no_db


@pytest.mark.parametrize("code", [
    "duplicate_entity_key", "entity_surface_not_found", "relation_entity_not_found",
    "relation_quote_not_found", "relation_qualifier_too_long",
])
def test_distinguishes_source_validation_failures(code):
    chunk = VideoChunk(uuid.uuid4(), "PX4 uses GPS.", "transcript_segment", 0, 20)
    extraction = ChunkExtraction.model_validate({
        "entities": [
            {"key": "p", "surface_text": "PX4", "entity_type": "software"},
            {"key": "g", "surface_text": "GPS", "entity_type": "technology"},
        ],
        "relations": [{
            "subject": "p", "object": "g", "predicate": "uses", "quote": chunk.text,
            "assertion": "asserted", "confidence": 0.8, "qualifiers": {},
        }],
    })
    if code == "duplicate_entity_key":
        extraction.entities[1].key = "p"
    elif code == "entity_surface_not_found":
        extraction.entities[0].surface_text = "DJI PX4"
    elif code == "relation_entity_not_found":
        extraction.relations[0].object = "missing"
    elif code == "relation_quote_not_found":
        extraction.relations[0].quote = "PX4 supports GPS."
    else:
        extraction.relations[0].qualifiers = {"x" * 81: "value"}
    with pytest.raises(ValueError) as caught:
        validate_extraction(chunk, extraction)
    detail = describe_graph_error(
        caught.value, stage="validation", chunk=chunk, chunk_index=2,
        extraction=extraction, locate=locate_quote,
    )
    assert detail["code"] == code
    assert detail["chunk_id"] == str(chunk.id)
    assert detail["chunk_index"] == 2
    assert graph_error_summary(detail).startswith("ValueError:")
    if code == "entity_surface_not_found":
        assert detail["surface_text"] == "DJI PX4"
    if code == "relation_quote_not_found":
        assert detail["quote"] == "PX4 supports GPS."


def test_provider_errors_preserve_cause_without_sensitive_message():
    secret = "test-private-provider-key"
    try:
        try:
            raise httpx.ConnectError(f"https://provider.invalid/?key={secret}")
        except httpx.ConnectError as cause:
            raise RuntimeError("wrapped request error") from cause
    except RuntimeError as exc:
        detail = describe_graph_error(exc, stage="extraction", locate=locate_quote)
    assert detail["code"] == "model_connection_error"
    assert detail["cause_types"] == ["RuntimeError", "ConnectError"]
    assert secret not in json.dumps(detail)
    assert "provider.invalid" not in json.dumps(detail)


def test_schema_error_details_omit_rejected_input():
    secret = "private-model-output-not-to-log"
    try:
        ChunkExtraction.model_validate({"entities": [], "relations": [], "type": secret})
    except ValidationError as exc:
        detail = describe_graph_error(exc, stage="extraction", locate=locate_quote)
    assert detail["code"] == "schema_validation_failed"
    assert detail["fields"] == [{"path": ["type"], "type": "extra_forbidden"}]
    assert secret not in json.dumps(detail)
