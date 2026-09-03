from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_runtime_image_includes_video_crawler_dependencies() -> None:
    dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")

    assert "ffmpeg" in dockerfile
    assert "nodejs" in dockerfile
    assert "COPY skills ./skills" in dockerfile
    assert "https://download.pytorch.org/whl/cpu" in dockerfile
    assert "python -m pip install torch" in dockerfile
    assert "python -m pip install '.[local-embeddings,video-keyframes]'" in dockerfile
    assert "EMBEDDING_PROVIDER=siliconflow" in dockerfile
    assert "EMBEDDING_MODEL=BAAI/bge-m3" in dockerfile
    assert "EMBEDDING_DIMENSION=1024" in dockerfile

    assert "sed -i 's/\\r$//'" in dockerfile
    assert "EMBEDDING_PROVIDER=deterministic" not in dockerfile
    assert "EMBEDDING_MODEL=deterministic-hash-v1" not in dockerfile


def test_docker_context_excludes_video_crawler_runtime_artifacts() -> None:
    dockerignore = (ROOT / ".dockerignore").read_text(encoding="utf-8")

    assert "skills/video-crawler/downloads/" in dockerignore
    assert "skills/video-crawler/cookies*.txt" in dockerignore


def test_deploy_compose_uses_siliconflow_bge_m3() -> None:
    compose = (ROOT / "docker-compose.deploy.yml").read_text(encoding="utf-8")

    assert "QDRANT_COLLECTION: content_chunks_bge_m3_v1" in compose
    assert "EMBEDDING_PROVIDER: siliconflow" in compose
    assert "EMBEDDING_MODEL: BAAI/bge-m3" in compose
    assert "EMBEDDING_DIMENSION: 1024" in compose
    assert "EMBEDDING_API_KEY: ${EMBEDDING_API_KEY:-${VLM_API_KEY:-}}" in compose
    assert "EMBEDDING_PROVIDER: deterministic" not in compose

    assert "HF_HOME: /models/huggingface" in compose
    assert "SENTENCE_TRANSFORMERS_HOME: /models/huggingface" in compose
    assert "${MODEL_CACHE_DIR:-./model-cache/huggingface}:/models/huggingface" in compose
