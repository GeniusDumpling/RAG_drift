from __future__ import annotations

import json
import sys
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

SCRIPT_PATH = (
    Path(__file__).resolve().parents[2]
    / "skills"
    / "video-crawler"
    / "scripts"
    / "video_fetch.py"
)


def load_module() -> Any:
    spec = spec_from_file_location("video_fetch_under_test", SCRIPT_PATH)
    assert spec is not None and spec.loader is not None
    module = module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_resolve_video_input_returns_normalized_metadata(monkeypatch: pytest.MonkeyPatch) -> None:
    module = load_module()
    captured_options: dict[str, object] = {}

    class FakeYoutubeDL:
        def __init__(self, options: dict[str, object]) -> None:
            captured_options.update(options)

        def __enter__(self) -> FakeYoutubeDL:
            return self

        def __exit__(self, *args: object) -> None:
            return None

        def extract_info(self, url: str, download: bool) -> dict[str, object]:
            assert url == "https://www.youtube.com/watch?v=fAZZLPwbPyg"
            assert download is False
            return {
                "id": "fAZZLPwbPyg",
                "title": "Drone test",
                "url": "https://rr.example.googlevideo.com/videoplayback?expire=secret",
                "duration": 42,
                "extractor_key": "Youtube",
            }

    monkeypatch.setitem(sys.modules, "yt_dlp", SimpleNamespace(YoutubeDL=FakeYoutubeDL))

    result = module.resolve_video_input(
        "https://www.youtube.com/watch?v=fAZZLPwbPyg"
    )

    assert result.video_id == "fAZZLPwbPyg"
    assert result.page_url == "https://www.youtube.com/watch?v=fAZZLPwbPyg"
    assert result.title == "Drone test"
    assert result.media_url.startswith("https://rr.example.googlevideo.com/")
    assert result.duration_seconds == 42
    assert result.extractor == "Youtube"
    assert captured_options["skip_download"] is True
    assert "height<=480" in str(captured_options["format"])


@pytest.mark.parametrize("url", ["file:///tmp/a.mp4", "ftp://example.com/a.mp4"])
def test_resolve_video_input_rejects_unsupported_scheme(url: str) -> None:
    module = load_module()
    with pytest.raises(module.VideoResolutionError, match="HTTP"):
        module.resolve_video_input(url)


def test_resolve_video_input_rejects_missing_media_url(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = load_module()

    class FakeYoutubeDL:
        def __init__(self, options: dict[str, object]) -> None:
            pass

        def __enter__(self) -> FakeYoutubeDL:
            return self

        def __exit__(self, *args: object) -> None:
            return None

        def extract_info(self, url: str, download: bool) -> dict[str, object]:
            return {"id": "fAZZLPwbPyg", "title": "Drone test"}

    monkeypatch.setitem(sys.modules, "yt_dlp", SimpleNamespace(YoutubeDL=FakeYoutubeDL))

    with pytest.raises(module.VideoResolutionError, match="媒体地址"):
        module.resolve_video_input("https://www.youtube.com/watch?v=fAZZLPwbPyg")


def test_analyze_video_only_calls_vlm_with_resolved_media(monkeypatch: pytest.MonkeyPatch) -> None:
    module = load_module()
    video_input = module.VideoInput(
        page_url="https://www.youtube.com/watch?v=fAZZLPwbPyg",
        video_id="fAZZLPwbPyg",
        title="Drone test",
        media_url="https://rr.example.googlevideo.com/videoplayback?expire=secret",
        duration_seconds=42,
        extractor="Youtube",
    )
    monkeypatch.setattr(module, "resolve_video_input", lambda *args, **kwargs: video_input)
    captured: dict[str, object] = {}

    def fake_describe_video(**kwargs: object) -> str:
        captured.update(kwargs)
        return "视频展示了一架无人机在室外飞行。"

    monkeypatch.setattr(module, "describe_video", fake_describe_video)

    result = module.analyze_video_only(
        video_url=video_input.page_url,
        base_url="https://api.siliconflow.cn/v1",
        api_key="not-printed",
        model="Qwen/Qwen3-Omni-30B-A3B-Instruct",
        client=object(),
    )

    assert captured["video_url"] == video_input.media_url
    assert result["video_page_url"] == video_input.page_url
    assert result["title"] == video_input.title
    assert result["description"] == "视频展示了一架无人机在室外飞行。"
    assert "media_url" not in result


def test_describe_video_sends_siliconflow_video_url() -> None:
    module = load_module()

    class FakeResponse:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict[str, object]:
            return {"choices": [{"message": {"content": "中文视频描述"}}]}

    class FakeClient:
        def __init__(self) -> None:
            self.request: dict[str, object] = {}

        def post(self, url: str, **kwargs: object) -> FakeResponse:
            self.request = {"url": url, **kwargs}
            return FakeResponse()

    client = FakeClient()
    result = module.describe_video(
        client=client,
        base_url="https://api.siliconflow.cn/v1",
        api_key="not-printed",
        model="Qwen/Qwen3-Omni-30B-A3B-Instruct",
        video_url="https://rr.example.googlevideo.com/videoplayback?expire=secret",
    )

    assert result == "中文视频描述"
    assert client.request["url"] == "https://api.siliconflow.cn/v1/chat/completions"
    body = client.request["json"]
    assert isinstance(body, dict)
    content = body["messages"][0]["content"]
    assert content[-1] == {
        "type": "video_url",
        "video_url": {
            "url": "https://rr.example.googlevideo.com/videoplayback?expire=secret"
        },
    }
    headers = client.request["headers"]
    assert isinstance(headers, dict)
    assert headers["Authorization"] == "Bearer not-printed"


def test_parse_args_accepts_analysis_only_json(monkeypatch: pytest.MonkeyPatch) -> None:
    module = load_module()
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "video_fetch.py",
            "--analyze-only",
            "--json",
            "--video-url",
            "https://www.youtube.com/watch?v=fAZZLPwbPyg",
            "--source",
            "youtube",
        ],
    )

    args = module.parse_args()

    assert args.analyze_only is True
    assert args.json is True


def test_analysis_result_is_json_serializable_without_media_url(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = load_module()
    monkeypatch.setattr(
        module,
        "resolve_video_input",
        lambda *args, **kwargs: module.VideoInput(
            page_url="https://www.youtube.com/watch?v=fAZZLPwbPyg",
            video_id="fAZZLPwbPyg",
            title="Drone test",
            media_url="https://rr.example.googlevideo.com/videoplayback?token=secret",
            duration_seconds=42,
            extractor="Youtube",
        ),
    )
    monkeypatch.setattr(module, "describe_video", lambda **kwargs: "中文视频描述")

    result = module.analyze_video_only(
        video_url="https://www.youtube.com/watch?v=fAZZLPwbPyg",
        base_url="https://api.siliconflow.cn/v1",
        api_key="not-printed",
        model="Qwen/Qwen3-Omni-30B-A3B-Instruct",
        client=object(),
    )

    serialized = json.dumps(result, ensure_ascii=False)
    assert "googlevideo" not in serialized
    assert "secret" not in serialized
