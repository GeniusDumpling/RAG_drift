"""One-to-one scoring must expose errors without semantic retries or loose substring matches."""

import pytest
from app.schemas.video_graph import ChunkExtraction

from scripts.evaluate_entity_gold import (
    matching,
    measures,
    normalize,
    prediction_entities,
    score_all,
)

pytestmark = pytest.mark.no_db


def entity(name, kind="product", mentions=None):
    return {"name": name, "type": kind, "mentions": mentions or [name]}


def test_one_prediction_cannot_satisfy_two_gold_identities():
    gold = [entity("left camera", "component", ["camera"]),
            entity("right camera", "component", ["camera"])]
    result = matching(gold, [entity("camera", "component")], [], typed=True)
    assert (result["tp"], result["fp"], result["fn"]) == (1, 0, 1)
    assert result["ambiguous_predictions"] == [0]


def test_augmenting_paths_find_maximum_matches_instead_of_greedy_first_hits():
    gold = [entity("A", mentions=["shared", "only A"]), entity("B", mentions=["shared"])]
    predictions = [entity("shared"), entity("only A")]
    assert matching(gold, predictions, [], typed=True)["tp"] == 2


def test_type_error_is_visible_separately_from_entity_recognition():
    gold = [entity("firmware", "software")]
    predicted = [entity("firmware", "method")]
    assert matching(gold, predicted, [], typed=False)["tp"] == 1
    result = matching(gold, predicted, [], typed=True)
    assert (result["tp"], result["fp"], result["fn"]) == (0, 1, 1)


def test_named_generations_and_whole_noun_phrases_never_use_substring_matching():
    assert not matching([entity("Mavic 4")], [entity("Mavic 4 Pro")], [], typed=False)["tp"]
    assert not matching([entity("battery", "component")],
                        [entity("battery cover", "component")], [], typed=False)["tp"]
    assert normalize("C++") != normalize("C#")


def test_raw_english_mention_can_match_a_chinese_canonical_name_without_translation():
    gold = [entity("手机", mentions=["phone"])]
    assert matching(gold, [entity("phone")], [], typed=True)["tp"] == 1
    assert normalize("RC\nMotion 3") == normalize("rc motion 3")


def test_explicit_conflicting_device_owners_block_an_otherwise_identical_surface():
    models = ["DJI Air 3", "DJI Air 3S"]
    gold = [entity("Air 3 camera", "component", ["camera"])]
    predicted = [entity("DJI Air 3S camera", "component", ["camera"])]
    assert not matching(gold, predicted, models, typed=True)["tp"]


def test_multiple_mentions_of_one_published_identity_do_not_inflate_false_positives():
    output = ChunkExtraction.model_validate({"entities": [
        dict(key="a", surface_text="Avata 2", canonical_name="DJI Avata 2",
             entity_type="product", identity_scope="global"),
        dict(key="b", surface_text="the drone", canonical_name="DJI Avata 2",
             entity_type="product", identity_scope="global"),
    ]})
    result = prediction_entities(output)
    assert len(result) == 1 and result[0]["mentions"] == ["Avata 2", "the drone"]


def test_failed_cases_remain_in_recall_denominator_and_have_no_fabricated_predictions():
    cases = [{"id": "c", "language": "zh", "models": [], "entities": [entity("A")]}]
    result = score_all(cases, {"c": {"status": "error"}})
    assert result["errors"] == ["c"]
    assert result["overall"]["typed"] == measures({"tp": 0, "fp": 0, "fn": 1})
    assert result["overall"]["typed"]["recall"] == 0
    assert result["overall"]["typed"]["precision"] is None
