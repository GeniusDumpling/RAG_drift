from unittest.mock import AsyncMock

import pytest
from app.core.config import Settings
from app.literature.llm import LiteratureLLM, verify_analysis
from app.literature.ranking import pick_top
from app.main import create_app
from app.schemas.literature import LiteratureRunCreate

pytestmark = pytest.mark.no_db


@pytest.mark.asyncio
async def test_fake_llm_generates_requested_dynamic_directions() -> None:
    llm = LiteratureLLM(Settings(LITERATURE_LLM_PROVIDER="fake"))

    directions = await llm.generate_directions("UAV lidar obstacle avoidance", direction_count=3)

    assert [row["id"] for row in directions] == ["D1", "D2", "D3"]
    assert all("UAV" in row["query"] for row in directions)
    assert directions[0]["matched_terms"]


def test_evidence_is_verified_against_pdf_page_and_unverified_text_is_downgraded() -> None:
    analysis = {
        "hits": [
            {
                "matched_term": "obstacle avoidance",
                "level": "exact",
                "evidence": "The proposed method avoids obstacles at 12 m/s.",
                "evidence_source": "abstract",
            },
            {
                "matched_term": "fabricated",
                "level": "exact",
                "evidence": "This sentence does not exist.",
                "evidence_source": "body",
            },
        ]
    }

    normalized, rows = verify_analysis(
        analysis,
        title="UAV navigation",
        abstract="A navigation method.",
        pages=[{"page": 6, "text": "The proposed method avoids obstacles at 12 m/s."}],
    )

    assert rows[0]["verified"] is True
    assert rows[0]["evidence_source"] == "body"
    assert rows[0]["page_number"] == 6
    assert rows[1]["verified"] is False
    assert rows[1]["match_level"] == "keyword"
    assert rows[1]["evidence_text"] == ""
    assert normalized["_verification"] == {"total": 2, "verified": 1, "downgraded": 1}


def test_ranking_prefers_venue_then_year_and_keeps_score_breakdown() -> None:
    records = [
        {
            "articleTitle": "UAV obstacle avoidance",
            "publicationTitle": "International Workshop",
            "publicationYear": 2026,
            "citationCount": 100,
        },
        {
            "articleTitle": "UAV obstacle avoidance with lidar",
            "publicationTitle": "IEEE Transactions on Robotics",
            "publicationYear": 2021,
            "citationCount": 5,
        },
    ]

    selected = pick_top(records, keywords=["lidar"], year_from=2018, top_n=1)

    assert selected[0]["record"]["publicationTitle"] == "IEEE Transactions on Robotics"
    assert selected[0]["score_detail"]["venue_tier"] == 100
    assert selected[0]["selected_rank"] == 1


def test_literature_request_rejects_invalid_selection_size() -> None:
    with pytest.raises(ValueError, match="top_n_per_direction"):
        LiteratureRunCreate(
            query="UAV lidar",
            candidates_per_direction=2,
            top_n_per_direction=3,
        )


def test_literature_routes_are_in_openapi_contract() -> None:
    paths = create_app().openapi()["paths"]

    assert "/literature-runs" in paths
    assert "/literature-runs/{run_id}/events" in paths
    assert "/literature-runs/{run_id}/results" in paths
    assert "/literature-artifacts/{artifact_id}/download" in paths


@pytest.mark.asyncio
async def test_llm_repairs_one_malformed_json_response(monkeypatch: pytest.MonkeyPatch) -> None:
    llm = LiteratureLLM(Settings(DEEPSEEK_API_KEY="test-key"))
    request_json = AsyncMock(
        side_effect=[
            '{"directions":[{"id":"D1" "query":"UAV lidar"}]}',
            '{"directions":[{"id":"D1","query":"UAV lidar"}]}',
        ]
    )
    monkeypatch.setattr(llm, "_request_json_content", request_json)

    response = await llm._chat_json(system="Return JSON.", user="UAV lidar", temperature=0.2)

    assert response == {"directions": [{"id": "D1", "query": "UAV lidar"}]}
    assert request_json.await_count == 2
    assert request_json.await_args_list[1].kwargs["temperature"] == 0
    assert "JSON 修复器" in request_json.await_args_list[1].kwargs["messages"][0]["content"]
