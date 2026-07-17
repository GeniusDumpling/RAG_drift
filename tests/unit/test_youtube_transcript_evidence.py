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

    result = module.collect_public_evidence(
        "https://youtu.be/abc123", allow_whisper_fallback=False
    )
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


@pytest.mark.parametrize(
    ("duration", "expected"),
    [(300, 6), (301, 10), (900, 10), (901, 16), (1801, 24), (3601, 32)],
)
def test_keyframe_cap_matches_design_duration_bands(duration: int, expected: int) -> None:
    assert load_module().caption_frame_limit(duration) == expected
