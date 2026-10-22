from types import SimpleNamespace

import pytest
from app.services.query_matching import lexical_terms, matches_term, model_phrases
from app.services.retrieval import _hybrid_score, _keyword_score

pytestmark = pytest.mark.no_db


@pytest.mark.parametrize(
    ("query", "expected"),
    [
        ("介绍一下Mavic 3 Pro", ["mavic 3 pro"]),
        ("Tell me about Mavic   3 Pro", ["mavic 3 pro"]),
        ("Air 3S 和 Mini 4 Pro", ["air 3s", "mini 4 pro"]),
        ("PX4 v1.17", ["px4", "v1.17"]),
        ("Tell me about Mavic 3.", ["mavic 3"]),
    ],
)
def test_model_names_survive_query_tokenization(query: str, expected: list[str]) -> None:
    assert model_phrases(query) == expected
    terms = lexical_terms(query.split(), query)
    assert all(phrase in terms for phrase in expected)


@pytest.mark.parametrize(
    ("text", "term", "expected"),
    [
        ("protocol protection", "pro", False),
        ("Reply 13", "3", False),
        ("Mavic 3S Pro", "mavic 3", False),
        ("DJI Mavic   3 PRO相机", "mavic 3 pro", True),
        ("TELEMETRY_ENABLED=false", "telemetry", True),
        ("飞控故障排查", "故障", True),
    ],
)
def test_lexical_boundaries_preserve_models_without_substring_noise(
    text: str, term: str, expected: bool,
) -> None:
    assert matches_term(text, term) is expected


def test_model_coverage_beats_recent_numeric_and_partial_model_matches() -> None:
    terms = lexical_terms(["mavic", "3", "pro"], "Mavic 3 Pro")

    def score(title: str, text: str) -> float:
        item = SimpleNamespace(title=title, summary_text=text, tags=[])
        chunk = SimpleNamespace(display_text=text, embed_text=text)
        return _keyword_score(item, chunk, terms)

    exact = score("Introducing DJI Mavic 3 Pro", "Three lenses and camera specifications")
    other_model = score("Mavic 4 Pro against Air 3S", "Mavic 4 Pro camera details")
    noise = score("Reply 3 · MAVLink handling", "protocol issue 3")
    assert exact > other_model > noise
    assert noise < 0.2
    assert exact > 0.9
    assert _hybrid_score(vector_score=0.67, keyword_score=exact) > other_model


def test_weak_keyword_overlap_does_not_saturate_hybrid_scores() -> None:
    weak = _hybrid_score(vector_score=0.67, keyword_score=0.2)
    strong = _hybrid_score(vector_score=0.67, keyword_score=1.0)
    assert 0.67 <= weak < strong < 1.0
