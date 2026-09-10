from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parents[2] / "tools" / "video-crawler" / "scripts"


def load_module(name: str):
    path = SCRIPT_DIR / f"{name}.py"
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def load_pipeline():
    load_module("config_loader")
    load_module("youtube_search")
    load_module("youtube_transcript_evidence")
    return load_module("pipeline")


def test_select_rotating_entry_persists_progress_between_runs(tmp_path: Path) -> None:
    pipeline = load_pipeline()
    conf = {
        "search": {
            "queries": [
                {"query": "drone GPS spoofing", "language": "en"},
                {"query": "MAVLink telemetry security", "language": "en"},
            ],
            "state_path": str(tmp_path / "search-state.json"),
        }
    }

    first = pipeline.select_next_query_entry(conf)
    second = pipeline.select_next_query_entry(conf)
    third = pipeline.select_next_query_entry(conf)

    assert [first["query"], second["query"], third["query"]] == [
        "drone GPS spoofing",
        "MAVLink telemetry security",
        "drone GPS spoofing",
    ]


def test_default_search_configuration_rotates_diverse_queries_and_targets_one_success() -> None:
    pipeline = load_pipeline()

    conf = pipeline.default_conf()

    assert conf["search"]["success_target"] == 1
    assert conf["search"]["max_pages"] > 1
    assert len(conf["search"]["queries"]) >= 6
    assert {entry["language"] for entry in conf["search"]["queries"]} == {"en", "zh"}


def test_search_ingests_new_candidates_across_pages_until_success_target(monkeypatch) -> None:
    pipeline = load_pipeline()
    conf = {
        "search": {
            "max_results": 2,
            "max_pages": 2,
            "success_target": 2,
            "language": "en",
            "caption_only": True,
            "order": "relevance",
            "whisper_fallback": False,
        },
        "retry": {"attempts": 1},
    }
    pages = iter(
        [
            {
                "items": [
                    {"video_id": "duplicate01", "canonical_url": "https://www.youtube.com/watch?v=duplicate01"},
                    {"video_id": "failure001", "canonical_url": "https://www.youtube.com/watch?v=failure001"},
                ],
                "next_page_token": "page-2",
            },
            {
                "items": [
                    {"video_id": "success001", "canonical_url": "https://www.youtube.com/watch?v=success001"},
                    {"video_id": "success002", "canonical_url": "https://www.youtube.com/watch?v=success002"},
                ],
                "next_page_token": None,
            },
        ]
    )
    seen_tokens = []

    def fake_search(*_args, page_token=None, **_kwargs):
        seen_tokens.append(page_token)
        return next(pages)

    monkeypatch.setattr(pipeline, "load_api_key", lambda: "test-key")
    monkeypatch.setattr(pipeline, "search_youtube_videos", fake_search)
    monkeypatch.setattr(
        pipeline,
        "load_existing_dedup_keys",
        lambda: {"video-evidence:https://www.youtube.com/watch?v=duplicate01"},
    )
    monkeypatch.setattr(pipeline, "apply_proxy", lambda _conf: None)
    monkeypatch.setattr(pipeline, "load_global_env", lambda: None)
    monkeypatch.setattr(pipeline, "resolve_keyframes_dir", lambda _conf: Path("/tmp/frames"))
    monkeypatch.setattr(pipeline, "resolve_whisper_model_cache", lambda _conf: Path("/tmp/whisper"))

    def fake_ingest(url, **_kwargs):
        if url.endswith("failure001"):
            return {"status": "no_public_captions"}
        return {"status": "success", "ingestion": {"status": "success", "content_id": url[-3:]}}

    monkeypatch.setattr(pipeline, "ingest_candidate", fake_ingest)

    result = pipeline.run_search_and_ingest(conf, query="drone security")

    assert seen_tokens == [None, "page-2"]
    assert result["success_count"] == 2
    assert result["outcome_counts"] == {
        "duplicate": 1,
        "no_captions": 1,
        "caption_failure": 0,
        "temporary_stream_failure": 0,
        "success": 2,
        "failed": 0,
    }
    assert result["pages_searched"] == 2
    assert result["candidates_seen"] == 4
