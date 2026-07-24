import importlib.util
import sys
from pathlib import Path

import pytest

SCRIPT_PATH = (
    Path(__file__).resolve().parents[2]
    / "skills"
    / "video-crawler"
    / "scripts"
    / "youtube_transcript_evidence.py"
)


def load_module():
    spec = importlib.util.spec_from_file_location("youtube_transcript_evidence", SCRIPT_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_transcript_evidence_script_exists() -> None:
    assert SCRIPT_PATH.is_file()


def test_module_exposes_local_whisper_fallback() -> None:
    assert hasattr(load_module(), "transcribe_with_local_whisper")


def test_no_captions_uses_local_whisper_without_persisting_media(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = load_module()

    class FakeYoutubeDL:
        def __init__(self, _options):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def extract_info(self, _url, download=False):
            assert not download
            return {
                "id": "abc123",
                "title": "short video",
                "description": "metadata only",
                "channel": "channel",
                "duration": 120,
            }

    monkeypatch.setitem(sys.modules, "yt_dlp", type("FakeYtDlp", (), {"YoutubeDL": FakeYoutubeDL}))
    calls = []

    def fake_transcribe(video_url, **kwargs):
        calls.append((video_url, kwargs))
        return [module.CaptionSegment(0.0, 1.0, "真实 ASR 文本")]

    monkeypatch.setattr(module, "transcribe_with_local_whisper", fake_transcribe)
    result = module.collect_public_evidence("https://youtu.be/abc123")

    assert result["status"] == "success"
    assert result["caption_source"] == "local_whisper"
    assert result["segments"] == [
        {"start_seconds": 0.0, "end_seconds": 1.0, "text": "真实 ASR 文本"}
    ]
    assert result["media_downloaded"] is False
    assert calls[0][0] == "https://www.youtube.com/watch?v=abc123"


def test_no_whisper_fallback_never_uses_metadata_as_transcript(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = load_module()

    class FakeYoutubeDL:
        def __init__(self, _options):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def extract_info(self, _url, download=False):
            return {"id": "abc123", "title": "title", "description": "description"}

    monkeypatch.setitem(sys.modules, "yt_dlp", type("FakeYtDlp", (), {"YoutubeDL": FakeYoutubeDL}))
    monkeypatch.setattr(
        module,
        "transcribe_with_local_whisper",
        lambda *_args, **_kwargs: pytest.fail("Whisper 应被禁用"),
    )

    result = module.collect_public_evidence("https://youtu.be/abc123", allow_whisper_fallback=False)
    assert result["status"] == "no_public_captions"
    assert result["segments"] == []


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        ("https://www.youtube.com/watch?v=abc123", "https://www.youtube.com/watch?v=abc123"),
        ("https://youtu.be/abc123?t=3", "https://www.youtube.com/watch?v=abc123"),
        ("https://www.youtube.com/shorts/abc123", "https://www.youtube.com/watch?v=abc123"),
    ],
)
def test_normalize_youtube_url_returns_stable_watch_url(source: str, expected: str) -> None:
    module = load_module()
    assert module.normalize_youtube_url(source) == (expected, "abc123")


def test_normalize_youtube_url_rejects_non_youtube_url() -> None:
    module = load_module()
    with pytest.raises(module.EvidenceCollectionError, match="YouTube"):
        module.normalize_youtube_url("https://example.com/video?id=abc123")


def test_choose_caption_track_prefers_manual_track() -> None:
    module = load_module()
    info = {
        "subtitles": {"zh-Hans": [{"url": "https://caption/manual", "ext": "vtt"}]},
        "automatic_captions": {"zh-Hans": [{"url": "https://caption/auto", "ext": "vtt"}]},
    }
    assert module.choose_caption_track(info, "zh-Hans") == (
        "manual_caption",
        "zh-hans",
        {"url": "https://caption/manual", "ext": "vtt"},
    )


def test_choose_caption_track_prefers_webvtt_format() -> None:
    module = load_module()
    info = {
        "subtitles": {
            "en": [
                {"url": "https://caption/json3", "ext": "json3"},
                {"url": "https://caption/vtt", "ext": "vtt"},
            ]
        }
    }
    assert module.choose_caption_track(info, "en") == (
        "manual_caption",
        "en",
        {"url": "https://caption/vtt", "ext": "vtt"},
    )


def test_choose_caption_track_falls_back_to_automatic_caption() -> None:
    module = load_module()
    info = {"automatic_captions": {"zh": [{"url": "https://caption/auto", "ext": "vtt"}]}}
    assert module.choose_caption_track(info, "zh-CN") == (
        "automatic_caption",
        "zh",
        {"url": "https://caption/auto", "ext": "vtt"},
    )


def test_choose_caption_track_accepts_chinese_script_variant_for_generic_zh() -> None:
    module = load_module()
    info = {"automatic_captions": {"zh-Hans": [{"url": "https://caption/auto", "ext": "vtt"}]}}

    assert module.choose_caption_track(info, "zh") == (
        "automatic_caption",
        "zh-hans",
        {"url": "https://caption/auto", "ext": "vtt"},
    )


def test_parse_webvtt_returns_timed_clean_segments() -> None:
    module = load_module()
    vtt = (
        "WEBVTT\n\n"
        "00:00:01.000 --> 00:00:03.500\n"
        '<font color="white">无人机 起飞</font>\n\n'
        "00:00:04.000 --> 00:00:05.000\n第二句\n"
    )
    assert module.parse_webvtt(vtt) == [
        module.CaptionSegment(1.0, 3.5, "无人机 起飞"),
        module.CaptionSegment(4.0, 5.0, "第二句"),
    ]


def test_read_caption_text_uses_yt_dlp_transport(monkeypatch: pytest.MonkeyPatch) -> None:
    module = load_module()
    calls = []

    class FakeResponse:
        def read(self):
            return b"WEBVTT\\n\\n00:00:00.000 --> 00:00:01.000\\n" + "字幕".encode()

    class FakeYoutubeDL:
        def __init__(self, options):
            calls.append(("options", options))

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def urlopen(self, url):
            calls.append(("urlopen", url))
            return FakeResponse()

    monkeypatch.setitem(sys.modules, "yt_dlp", type("FakeYtDlp", (), {"YoutubeDL": FakeYoutubeDL}))

    assert module._read_caption_text("https://caption.example/track.vtt").startswith("WEBVTT")
    assert calls[-1] == ("urlopen", "https://caption.example/track.vtt")


def test_read_caption_text_reuses_existing_yt_dlp_session() -> None:
    module = load_module()
    calls = []

    class FakeResponse:
        def read(self):
            return b"WEBVTT"

    class FakeDownloader:
        def urlopen(self, url):
            calls.append(url)
            return FakeResponse()

    assert module._read_caption_text(
        "https://caption.example/track.vtt", downloader=FakeDownloader()
    ) == "WEBVTT"
    assert calls == ["https://caption.example/track.vtt"]


def test_scene_aware_keyframes_use_every_detected_shot_midpoint() -> None:
    module = load_module()
    assert module.plan_scene_aware_keyframe_timestamps(120, [0.0, 20.0, 50.0, 90.0, 120.0]) == [
        10.0,
        35.0,
        70.0,
        105.0,
    ]


def test_scene_aware_keyframes_fall_back_to_one_center_frame_without_shots() -> None:
    module = load_module()
    assert module.plan_scene_aware_keyframe_timestamps(120, []) == [60.0]


def test_keyframes_are_extracted_as_in_memory_jpegs(monkeypatch: pytest.MonkeyPatch) -> None:
    module = load_module()
    assert hasattr(module, "extract_keyframes_as_jpegs")

    commands = []
    monkeypatch.setattr(
        module, "_resolve_video_stream_url", lambda _url: "https://media.example/video"
    )

    def fake_run(command, **kwargs):
        commands.append((command, kwargs))
        return type("Result", (), {"stdout": b"\xff\xd8frame\xff\xd9"})()

    monkeypatch.setattr(module.subprocess, "run", fake_run)
    frames = module.extract_keyframes_as_jpegs(
        "https://www.youtube.com/watch?v=abc123", duration_seconds=120
    )

    assert [frame.timestamp_seconds for frame in frames] == [60.0]
    assert [frame.jpeg_bytes for frame in frames] == [b"\xff\xd8frame\xff\xd9"]
    assert all("pipe:1" in command for command, _kwargs in commands)


def test_keyframe_extraction_skips_one_failed_scene_and_keeps_other_frames(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = load_module()
    monkeypatch.setattr(module, "_resolve_video_stream_url", lambda _url: "https://media.example/video")
    monkeypatch.setattr(module, "detect_scene_boundaries", lambda *_args: [0.0, 10.0, 120.0])
    calls = 0

    def fake_run(command, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise module.subprocess.CalledProcessError(1, command, stderr=b"decode failed")
        return type("Result", (), {"stdout": b"\xff\xd8frame\xff\xd9"})()

    monkeypatch.setattr(module.subprocess, "run", fake_run)
    frames = module.extract_keyframes_as_jpegs(
        "https://www.youtube.com/watch?v=abc123", duration_seconds=120
    )

    assert [frame.timestamp_seconds for frame in frames] == [65.0]


def test_phash_deduplication_discards_near_duplicate_frames() -> None:
    module = load_module()
    frames = [
        module.Keyframe(10.0, b"a"),
        module.Keyframe(20.0, b"b"),
        module.Keyframe(30.0, b"c"),
    ]
    hashes = iter([0b0000, 0b0001, 0b1111])
    result = module.deduplicate_keyframes(
        frames, hash_frame=lambda _frame: next(hashes), max_distance=1
    )
    assert result == [
        frames[0],
        frames[2],
    ]


def test_cli_extracts_keyframes_by_default() -> None:
    module = load_module()
    args = module.parse_args(["--video-url", "https://youtu.be/abc123"])
    assert args.extract_keyframes is True
    args = module.parse_args(["--video-url", "https://youtu.be/abc123", "--extract-keyframes"])
    assert args.extract_keyframes is True


def test_keyframe_summary_only_exposes_timestamps_and_byte_sizes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = load_module()
    assert hasattr(module, "add_keyframe_summary")
    monkeypatch.setattr(
        module,
        "extract_keyframes_as_jpegs",
        lambda *_args, **_kwargs: [module.Keyframe(20.0, b"image-bytes")],
    )
    evidence = {"duration_seconds": 120}
    result = module.add_keyframe_summary(evidence, "https://youtu.be/abc123")
    assert result["keyframes"] == [{"timestamp_seconds": 20.0, "byte_size": 11}]
    assert "jpeg_bytes" not in result["keyframes"][0]


def test_persist_keyframes_saves_jpegs_in_a_per_video_directory(tmp_path: Path) -> None:
    module = load_module()
    saved = module.persist_keyframes(
        [
            module.Keyframe(20.0, b"\xff\xd8first\xff\xd9"),
            module.Keyframe(40.5, b"\xff\xd8second\xff\xd9"),
        ],
        "abc123",
        downloads_dir=tmp_path,
    )

    assert saved == [
        {
            "timestamp_seconds": 20.0,
            "byte_size": 9,
            "local_path": str(tmp_path / "abc123" / "keyframes" / "keyframe_001_20.000s.jpg"),
        },
        {
            "timestamp_seconds": 40.5,
            "byte_size": 10,
            "local_path": str(tmp_path / "abc123" / "keyframes" / "keyframe_002_40.500s.jpg"),
        },
    ]
    assert (tmp_path / "abc123" / "keyframes" / "keyframe_001_20.000s.jpg").read_bytes() == (
        b"\xff\xd8first\xff\xd9"
    )
    assert (tmp_path / "abc123" / "keyframes" / "keyframe_002_40.500s.jpg").read_bytes() == (
        b"\xff\xd8second\xff\xd9"
    )


def test_cli_extracts_and_saves_keyframes_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    module = load_module()
    monkeypatch.setattr(
        sys,
        "argv",
        ["script", "--video-url", "https://youtu.be/abc123"],
    )
    monkeypatch.setattr(
        module,
        "collect_public_evidence",
        lambda *_args, **_kwargs: {
            "video_id": "abc123",
            "duration_seconds": 120,
            "segments": [{"text": "真实转写"}],
        },
    )
    frames = [module.Keyframe(20.0, b"frame")]
    monkeypatch.setattr(module, "extract_keyframes_as_jpegs", lambda *_args, **_kwargs: frames)
    monkeypatch.setattr(
        module,
        "persist_keyframes",
        lambda got_frames, video_id, **_kwargs: [
            {
                "timestamp_seconds": got_frames[0].timestamp_seconds,
                "byte_size": len(got_frames[0].jpeg_bytes),
                "local_path": f"downloads/{video_id}/keyframes/keyframe_001_20.000s.jpg",
            }
        ],
    )

    monkeypatch.setattr(
        module,
        "load_vlm_config",
        lambda _path: module.VlmConfig("https://vlm.example/v1", "test-key", "vision-model"),
    )
    monkeypatch.setattr(
        module, "summarize_video_evidence_with_vlm", lambda *_args, **_kwargs: "摘要"
    )
    monkeypatch.setattr(module, "ingest_video_evidence", lambda _evidence: {"status": "success"})

    assert module.main() == 0
    assert frames[0].jpeg_bytes == b"frame"


def test_vlm_payload_contains_real_transcript_and_inline_keyframes() -> None:
    module = load_module()
    assert hasattr(module, "build_video_summary_payload")
    evidence = {
        "title": "测试视频",
        "duration_seconds": 12,
        "segments": [{"start_seconds": 0, "end_seconds": 2, "text": "真实字幕文本"}],
    }
    payload = module.build_video_summary_payload(
        evidence,
        [module.Keyframe(1.0, b"jpeg-bytes")],
        model="vision-model",
    )

    assert payload["model"] == "vision-model"
    content = payload["messages"][0]["content"]
    assert content[0]["type"] == "text"
    assert "真实字幕文本" in content[0]["text"]
    assert content[1] == {
        "type": "image_url",
        "image_url": {"url": "data:image/jpeg;base64,anBlZy1ieXRlcw=="},
    }


def test_vlm_summary_uses_env_configured_model_and_returns_content() -> None:
    module = load_module()
    assert hasattr(module, "summarize_video_evidence_with_vlm")
    evidence = {"segments": [{"start_seconds": 0, "text": "真实字幕文本"}]}
    received = {}

    class FakeResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {"choices": [{"message": {"content": "真实证据摘要"}}]}

    def fake_post(url, **kwargs):
        received["url"] = url
        received.update(kwargs)
        return FakeResponse()

    summary = module.summarize_video_evidence_with_vlm(
        evidence,
        [],
        base_url="https://vlm.example/v1",
        api_key="test-key",
        model="configured-model",
        post=fake_post,
    )
    assert summary == "真实证据摘要"
    assert received["url"] == "https://vlm.example/v1/chat/completions"
    assert received["headers"]["Authorization"] == "Bearer test-key"
    assert received["json"]["model"] == "configured-model"


def test_persisted_video_text_does_not_combine_summary_and_transcript() -> None:
    module = load_module()
    assert module.persisted_video_text("摘要文本", "真实转写文本") == "摘要文本"


def test_video_evidence_chunks_include_summary_and_timestamped_transcript() -> None:
    module = load_module()
    chunks = module.build_video_evidence_chunks(
        "视频摘要",
        [
            {"start_seconds": 1.5, "end_seconds": 3.0, "text": "第一句字幕"},
            {"start_seconds": 3.0, "end_seconds": 5.0, "text": "第二句字幕"},
        ],
    )

    assert [chunk["evidence_type"] for chunk in chunks] == [
        "video_summary",
        "transcript_segment",
    ]
    assert chunks[1]["text"] == "第一句字幕\n第二句字幕"
    assert chunks[1]["start_seconds"] == 1.5
    assert chunks[1]["end_seconds"] == 5.0


def test_video_evidence_chunks_group_adjacent_short_transcript_segments() -> None:
    module = load_module()
    chunks = module.build_video_evidence_chunks(
        "视频摘要",
        [
            {"start_seconds": 0, "end_seconds": 2, "text": "第一句"},
            {"start_seconds": 2, "end_seconds": 4, "text": "第二句"},
            {"start_seconds": 4, "end_seconds": 6, "text": "第三句"},
        ],
        max_transcript_chars=7,
        max_transcript_seconds=60,
    )

    assert [chunk["text"] for chunk in chunks] == ["视频摘要", "第一句\n第二句", "第三句"]
    assert chunks[1]["start_seconds"] == 0
    assert chunks[1]["end_seconds"] == 4


def test_video_content_does_not_persist_duplicate_summary_field() -> None:
    module = load_module()
    assert module.persisted_video_summary("摘要文本") is None


def test_load_vlm_config_reads_required_values_without_exposing_secret(tmp_path: Path) -> None:
    module = load_module()
    assert hasattr(module, "load_vlm_config")
    env_file = tmp_path / ".env"
    env_file.write_text(
        "VLM_BASE_URL=https://vlm.example/v1\nVLM_API_KEY=secret\nVLM_MODEL=vision-model\n",
        encoding="utf-8",
    )
    config = module.load_vlm_config(env_file)
    assert config.base_url == "https://vlm.example/v1"
    assert config.api_key == "secret"
    assert config.model == "vision-model"


def test_load_vlm_config_uses_project_defaults_for_optional_base_url_and_model(
    tmp_path: Path,
) -> None:
    module = load_module()
    env_file = tmp_path / ".env"
    env_file.write_text("VLM_API_KEY=secret\n", encoding="utf-8")
    config = module.load_vlm_config(env_file)
    assert config.base_url == "https://api.siliconflow.cn/v1"
    assert config.model == "Qwen/Qwen3-Omni-30B-A3B-Instruct"


def test_cli_runs_vlm_summary_and_ingestion_by_default() -> None:
    module = load_module()
    args = module.parse_args(["--video-url", "https://youtu.be/abc123"])
    assert args.summarize_with_vlm is True
    assert args.ingest is True


def test_module_exposes_evidence_ingestion() -> None:
    assert hasattr(load_module(), "ingest_video_evidence")


def test_cli_vlm_summary_uses_extracted_frames(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    module = load_module()
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "script",
            "--video-url",
            "https://youtu.be/abc123",
            "--keyframes-dir",
            str(tmp_path),
        ],
    )
    evidence = {
        "video_id": "abc123",
        "duration_seconds": 120,
        "segments": [{"text": "真实转写"}],
    }
    monkeypatch.setattr(module, "collect_public_evidence", lambda *_args, **_kwargs: evidence)
    monkeypatch.setattr(
        module,
        "extract_keyframes_as_jpegs",
        lambda *_args, **_kwargs: [module.Keyframe(20.0, b"frame")],
    )
    config = module.VlmConfig("https://vlm.example/v1", "test-key", "vision-model")
    monkeypatch.setattr(module, "load_vlm_config", lambda _path: config)

    def fake_vlm_summary(got_evidence, frames, **_kwargs):
        transcript = got_evidence["segments"][0]["text"]
        return f"摘要: {transcript} / {len(frames)} 帧"

    monkeypatch.setattr(module, "summarize_video_evidence_with_vlm", fake_vlm_summary)
    monkeypatch.setattr(module, "ingest_video_evidence", lambda _evidence: {"status": "success"})

    assert module.main() == 0
    assert evidence["keyframes"] == [
        {
            "timestamp_seconds": 20.0,
            "byte_size": 5,
            "local_path": str(tmp_path / "abc123" / "keyframes" / "keyframe_001_20.000s.jpg"),
        }
    ]
    assert evidence["video_summary"] == "摘要: 真实转写 / 1 帧"
    assert evidence["ingestion"] == {"status": "success"}
