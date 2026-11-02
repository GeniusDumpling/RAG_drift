import pytest
from app.services.citation_check import validate_citations

pytestmark = pytest.mark.no_db


def test_valid_single_citations() -> None:
    result = validate_citations("结论一 [1]。结论二 [2]。再次引用 [1]。", 3)

    assert result.status == "ok"
    assert result.cited_indices == [1, 2]
    assert result.out_of_range_indices == []
    assert result.uncited_evidence_indices == [3]
    assert result.marker_count == 3


def test_comma_separated_citation_group() -> None:
    result = validate_citations("综合结论 [1, 2] 成立。", 2)

    assert result.status == "ok"
    assert result.cited_indices == [1, 2]
    assert result.marker_count == 2
    assert result.uncited_evidence_indices == []


def test_out_of_range_citation_is_flagged() -> None:
    result = validate_citations("幻觉引用 [5] 和有效引用 [1]。", 2)

    assert result.status == "invalid_citations"
    assert result.out_of_range_indices == [5]
    assert result.cited_indices == [1]
    assert result.marker_count == 2


def test_no_citations_when_evidence_exists() -> None:
    result = validate_citations("这里没有任何引用标记。", 2)

    assert result.status == "no_citations"
    assert result.cited_indices == []
    assert result.uncited_evidence_indices == [1, 2]


def test_markdown_links_are_not_citations() -> None:
    result = validate_citations("参考 [官方文档](https://example.com) 与 [1]。", 1)

    assert result.status == "ok"
    assert result.cited_indices == [1]
    assert result.marker_count == 1


def test_duplicate_and_repeated_out_of_range_markers() -> None:
    result = validate_citations("[9] [9] [1, 9]", 1)

    assert result.status == "invalid_citations"
    assert result.out_of_range_indices == [9]
    assert result.cited_indices == [1]
    assert result.marker_count == 4


def test_zero_index_is_out_of_range() -> None:
    result = validate_citations("错误的零号引用 [0]。", 2)

    assert result.status == "invalid_citations"
    assert result.out_of_range_indices == [0]


def test_to_trace_shape() -> None:
    trace = validate_citations("引用 [1]。", 2).to_trace()

    assert trace == {
        "status": "ok",
        "marker_count": 1,
        "cited_indices": [1],
        "out_of_range_indices": [],
        "uncited_evidence_indices": [2],
    }


def test_negative_evidence_count_rejected() -> None:
    with pytest.raises(ValueError):
        validate_citations("文本 [1]。", -1)
