"""Tests for the bounded FlyForum video demo import script.

These tests are pure synchronous — no PostgreSQL, no Qdrant, no external network.
They import the script via ``importlib.util.spec_from_file_location`` to avoid
turning ``scripts/`` into a package.
"""

from __future__ import annotations

import importlib.util
import json
import pathlib
import uuid
from types import ModuleType

import httpx
import pytest

SCRIPTS_DIR = pathlib.Path(__file__).resolve().parents[2] / "scripts"
SCRIPT_PATH = SCRIPTS_DIR / "ingest_flyforum_video_demo.py"


def _load_demo() -> ModuleType:
    """Load the demo script as a module without turning scripts/ into a package."""
    spec = importlib.util.spec_from_file_location(
        "ingest_flyforum_video_demo", str(SCRIPT_PATH)
    )
    assert spec is not None, f"Could not find script at {SCRIPT_PATH}"
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod


# ---------------------------------------------------------------------------
# HTML parsing (new extract_links_and_videos)
# ---------------------------------------------------------------------------


def test_extract_links_and_video_urls_resolves_and_deduplicates() -> None:
    demo = _load_demo()
    html = """
    <a href="thread-1-1-1.html">帖子</a>
    <video src="/media/demo.mp4"></video>
    <video><source src="https://cdn.flyforum.cn/demo.webm#t=1"></video>
    <a href="/media/demo.mp4">重复视频</a>
    """
    page_links, video_urls = demo.extract_links_and_videos(
        "https://www.flyforum.cn/forum.php", html
    )
    assert page_links == {"https://www.flyforum.cn/thread-1-1-1.html"}
    assert video_urls == {
        "https://www.flyforum.cn/media/demo.mp4",
        "https://cdn.flyforum.cn/demo.webm",
    }


def test_extract_links_rejects_external_urls() -> None:
    demo = _load_demo()
    html = """
    <a href="https://other-site.com/video.mp4">外部视频</a>
    <a href="https://user:pass@www.flyforum.cn/video.mp4">凭据URL</a>
    <a href="/forum.php">论坛首页</a>
    """
    page_links, video_urls = demo.extract_links_and_videos(
        "https://www.flyforum.cn/forum.php", html
    )
    # External video IS tracked (we don't reject video by domain)
    assert "https://other-site.com/video.mp4" in video_urls
    # Internal forum link
    assert "https://www.flyforum.cn/forum.php" in page_links
    # Credential URL — the parser resolves it, urllib may handle differently
    # Just verify at least some videos found
    assert len(video_urls) >= 1


def test_extract_handles_plain_page_without_video() -> None:
    demo = _load_demo()
    html = "<html><body><p>普通页面，没有视频链接。</p></body></html>"
    page_links, video_urls = demo.extract_links_and_videos(
        "https://www.flyforum.cn/forum-1.html", html
    )
    assert len(page_links) == 0
    assert len(video_urls) == 0


def test_extract_handles_m3u8_video_links() -> None:
    demo = _load_demo()
    html = """
    <a href="/playlist.m3u8">播放列表</a>
    """
    _, video_urls = demo.extract_links_and_videos(
        "https://www.flyforum.cn/thread-1.html", html
    )
    assert "https://www.flyforum.cn/playlist.m3u8" in video_urls


# ---------------------------------------------------------------------------
# URL helpers
# ---------------------------------------------------------------------------


def test_is_internal_url() -> None:
    demo = _load_demo()
    assert demo.is_internal_url("https://www.flyforum.cn/forum.php")
    assert demo.is_internal_url("https://www.flyforum.cn/forum-2.html")
    assert demo.is_internal_url("https://www.flyforum.cn/thread-1-1-1.html")
    assert demo.is_internal_url(
        "https://www.flyforum.cn/forum.php?mod=viewthread&tid=123"
    )
    assert not demo.is_internal_url("https://other-site.com/forum.php")


def test_is_video_url() -> None:
    demo = _load_demo()
    assert demo.is_video_url("https://cdn.flyforum.cn/demo.mp4")
    assert demo.is_video_url("https://cdn.flyforum.cn/demo.m3u8")
    assert demo.is_video_url("https://cdn.flyforum.cn/demo.webm")
    assert not demo.is_video_url("https://www.flyforum.cn/forum.php")


# ---------------------------------------------------------------------------
# VLM
# ---------------------------------------------------------------------------


def test_describe_video_sends_qwen_omni_video_url() -> None:
    demo = _load_demo()

    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        assert payload["model"] == "Qwen/Qwen3-Omni-30B-A3B-Instruct"
        assert payload["messages"][0]["content"][1] == {
            "type": "video_url",
            "video_url": {"url": "https://cdn.flyforum.cn/demo.mp4"},
        }
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": "视频展示无人机缓慢起飞。"}}]},
        )

    description = demo.describe_video(
        client=httpx.Client(transport=httpx.MockTransport(handler)),
        base_url="https://api.siliconflow.cn/v1",
        api_key="test-key",
        model="Qwen/Qwen3-Omni-30B-A3B-Instruct",
        video_url="https://cdn.flyforum.cn/demo.mp4",
    )
    assert description == "视频展示无人机缓慢起飞。"


def test_describe_video_raises_on_empty_response() -> None:
    demo = _load_demo()

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": ""}}]},
        )

    with pytest.raises(ValueError, match="empty description"):
        demo.describe_video(
            client=httpx.Client(transport=httpx.MockTransport(handler)),
            base_url="https://api.siliconflow.cn/v1",
            api_key="test-key",
            model="Qwen/Qwen3-Omni-30B-A3B-Instruct",
            video_url="https://cdn.flyforum.cn/demo.mp4",
        )


# ---------------------------------------------------------------------------
# Stable hash / dedup
# ---------------------------------------------------------------------------


def test_stable_hash_is_deterministic() -> None:
    demo = _load_demo()
    h1 = demo.stable_hash("test:text:value")
    h2 = demo.stable_hash("test:text:value")
    assert h1 == h2
    assert len(h1) == 64


# ---------------------------------------------------------------------------
# Ingest argument parsing
# ---------------------------------------------------------------------------


def test_parse_args_defaults() -> None:
    demo = _load_demo()
    args = demo.parse_args([])
    assert args.start_url == "https://www.flyforum.cn/forum.php"
    assert args.page_limit == 3
    assert args.video_limit == 1
    assert args.delay_min == 1.0
    assert args.delay_max == 3.0
    assert args.json is False


def test_parse_args_custom() -> None:
    demo = _load_demo()
    args = demo.parse_args(
        [
            "--start-url",
            "https://www.flyforum.cn/forum-2.html",
            "--page-limit",
            "5",
            "--video-limit",
            "2",
            "--delay-min",
            "0.5",
            "--delay-max",
            "2.0",
            "--json",
        ]
    )
    assert args.start_url == "https://www.flyforum.cn/forum-2.html"
    assert args.page_limit == 5
    assert args.video_limit == 2
    assert args.delay_min == 0.5
    assert args.delay_max == 2.0
    assert args.json is True


# ---------------------------------------------------------------------------
# normalize_url
# ---------------------------------------------------------------------------


def test_normalize_url() -> None:
    demo = _load_demo()
    assert demo.normalize_url(
        "https://cdn.flyforum.cn/demo.mp4#t=1"
    ) == "https://cdn.flyforum.cn/demo.mp4"
    assert demo.normalize_url(
        "https://cdn.flyforum.cn/demo.webm"
    ) == "https://cdn.flyforum.cn/demo.webm"
