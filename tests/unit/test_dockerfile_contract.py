from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def test_runtime_image_includes_video_crawler_dependencies() -> None:
    dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")

    assert "ffmpeg" in dockerfile
    assert "nodejs" in dockerfile
    assert "COPY skills ./skills" in dockerfile


def test_docker_context_excludes_video_crawler_runtime_artifacts() -> None:
    dockerignore = (ROOT / ".dockerignore").read_text(encoding="utf-8")

    assert "skills/video-crawler/downloads/" in dockerignore
    assert "skills/video-crawler/cookies*.txt" in dockerignore
