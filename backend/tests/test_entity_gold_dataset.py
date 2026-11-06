"""Check the user-defined scope and the small, editable annotation format."""

import json

import pytest

from scripts.entity_gold_dataset import DEFAULT_DATASET, validate

pytestmark = pytest.mark.no_db


def data():
    return json.loads(DEFAULT_DATASET.read_text())


def test_actual_set_has_twenty_sources_ten_per_language_and_explicit_models():
    dataset = data()
    result = validate(dataset)
    assert result["cases"] == 20 and result["languages"] == {"zh": 10, "en": 10}
    assert result["status"] == dataset["status"]
    assert all(case["models"] for case in dataset["cases"])


def test_unbalanced_languages_are_rejected():
    dataset = data()
    dataset["cases"][0]["language"] = "en"
    with pytest.raises(ValueError, match="各10段"):
        validate(dataset)


def test_a_model_in_the_title_only_is_not_a_valid_body_annotation():
    dataset = data()
    dataset["cases"][0]["entities"][0]["mentions"] = ["DJI Mini 99 Imaginary"]
    with pytest.raises(ValueError, match="正文"):
        validate(dataset)


def test_models_describes_the_source_without_overruling_reviewed_entities():
    dataset = data()
    dataset["cases"][0]["models"] = []
    with pytest.raises(ValueError, match="具体无人机型号"):
        validate(dataset)
    dataset = data()
    dataset["cases"][0]["models"].append("a passing model not retained as a gold entity")
    assert validate(dataset)["cases"] == 20


def test_repeated_video_sources_are_rejected():
    dataset = data()
    dataset["cases"][1]["source"]["url"] = dataset["cases"][0]["source"]["url"]
    with pytest.raises(ValueError, match="重复的来源"):
        validate(dataset)


def test_old_metadata_fields_and_unknown_entity_types_are_not_silently_accepted():
    dataset = data()
    dataset["cases"][0]["source_sha256"] = "obsolete field"
    with pytest.raises(ValueError, match="简化格式"):
        validate(dataset)
    dataset = data()
    dataset["cases"][0]["entities"][0]["type"] = "anything"
    with pytest.raises(ValueError, match="类型错误"):
        validate(dataset)
