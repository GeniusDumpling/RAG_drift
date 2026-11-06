"""Reject gold corruption without making model or database requests."""

import copy
import json
from pathlib import Path

import pytest

from scripts.relation_gold_dataset import DEFAULT_DATASET, DEFAULT_ENTITIES, main, render, validate

pytestmark = pytest.mark.no_db


@pytest.fixture(params=["v1", "v3"])
def datasets(request):
    root = Path("scripts/evaluation_data")
    version = request.param
    return (json.loads((root / f"relation_gold_{version}.json").read_text()),
            json.loads((root / f"entity_gold_{version}.json").read_text()))


def test_each_relation_version_keeps_its_paired_entities_and_all_twenty_sources(datasets):
    relations, entities = datasets
    result = validate(relations, entities)
    assert entities["status"] == "human_reviewed"
    assert result["status"] == relations["status"]
    assert result["cases"] == 20
    assert result["languages"] == {"zh": 10, "en": 10}
    assert result["relations"] > 0
    assert result["empty_cases"] == ["EG009"]


@pytest.mark.parametrize("field,value", [
    ("text", ["rewritten source"]), ("models", ["DJI Made Up Model"]),
])
def test_source_and_scope_cannot_drift(datasets, field, value):
    relations, entities = datasets
    relations["cases"][0][field] = value
    with pytest.raises(ValueError):
        validate(relations, entities)


def test_valid_but_changed_entity_name_is_rejected(datasets):
    relations, entities = datasets
    relations["cases"][0]["entities"][0]["name"] = "changed canonical name"
    with pytest.raises(ValueError, match="entities必须沿用"):
        validate(relations, entities)


def test_endpoint_must_exist_in_this_case(datasets):
    relations, entities = datasets
    relations["cases"][0]["relations"][0]["object"] = "DJI Fly"
    with pytest.raises(ValueError, match="本片段已有实体"):
        validate(relations, entities)


@pytest.mark.parametrize("predicate,match", [
    ("compared_to", "关系类型"), ("equipped_with", "关系类型"),
    ("manufactured_by", "类型不符合约束"),
])
def test_predicate_and_endpoint_types_are_checked(datasets, predicate, match):
    relations, entities = datasets
    relations["cases"][0]["relations"][0]["predicate"] = predicate
    with pytest.raises(ValueError, match=match):
        validate(relations, entities)


def test_separated_source_lines_cannot_be_stitched_as_one_quote(datasets):
    relations, entities = datasets
    case = relations["cases"][0]
    case["relations"][0]["quote"] = [case["text"][2], case["text"][16]]
    with pytest.raises(ValueError, match="连续原文"):
        validate(relations, entities)


def test_reverse_symmetric_edge_is_still_a_duplicate(datasets):
    relations, entities = datasets
    case = relations["cases"][9]
    duplicate = copy.deepcopy(case["relations"][0])
    duplicate["subject"], duplicate["object"] = duplicate["object"], duplicate["subject"]
    case["relations"].append(duplicate)
    with pytest.raises(ValueError, match="重复关系"):
        validate(relations, entities)


@pytest.mark.parametrize("qualifiers", [
    {"modality": "conditional"},
    {"modality": "conditional", "scope": "made up condition"},
    {"mode": "made up mode"}, {"modality": "guess"},
])
def test_scopes_and_modes_must_be_literal_evidence(datasets, qualifiers):
    relations, entities = datasets
    relations["cases"][0]["relations"][0]["qualifiers"] = qualifiers
    with pytest.raises(ValueError):
        validate(relations, entities)


def test_fault_condition_is_preserved_and_not_a_current_universal_fact(datasets):
    relations, entities = datasets
    fault = next(r for r in relations["cases"][16]["relations"] if r["predicate"] == "reports_fault")
    assert fault["assertion"] == "asserted"
    assert fault["qualifiers"]["modality"] == "conditional"
    assert fault["qualifiers"]["scope"].startswith("If your DJI Air 3")
    assert validate(relations, entities)["assertions"]["uncertain"] > 0


def test_render_is_current_and_reports_actual_review_status(datasets):
    relations, _ = datasets
    result = render(relations)
    status = "已人工复核" if relations["status"] == "human_reviewed" else "助手初标，待人工复核"
    assert f"状态：{status}" in result
    assert "EG001" in result and "EG020" in result
    assert "本段未标注关系" in result
    assert "If your DJI Air 3" in result
    version = relations["version"]
    assert result + "\n" == DEFAULT_DATASET.parents[2].joinpath(f"docs/{version}-review.md").read_text()


def test_render_does_not_mark_a_draft_reviewed(datasets):
    relations, _ = datasets
    relations["status"] = "assistant_draft"
    assert "状态：助手初标，待人工复核" in render(relations)


@pytest.mark.parametrize("target", [DEFAULT_DATASET, DEFAULT_ENTITIES])
def test_cli_cannot_overwrite_either_annotation_file(target):
    original = target.read_bytes()
    with pytest.raises(SystemExit) as exc:
        main(["render", "--output", str(target)])
    assert exc.value.code == 2
    assert target.read_bytes() == original


def test_relation_v3_requires_its_paired_entity_version():
    data = json.loads(DEFAULT_DATASET.read_text())
    entities = json.loads(Path("scripts/evaluation_data/entity_gold_v1.json").read_text())
    with pytest.raises(ValueError, match="版本不配套"):
        validate(data, entities)


def test_unknown_relation_versions_are_not_silently_accepted(datasets):
    data, entities = datasets
    data["version"] = "relation-gold-unknown"
    with pytest.raises(ValueError, match="已支持的version"):
        validate(data, entities)
