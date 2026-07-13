from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def test_runtime_image_includes_video_crawler_dependencies() -> None:
    dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")

    assert "ffmpeg" in dockerfile
    assert "nodejs" in dockerfile
    assert "COPY skills ./skills" in dockerfile
    assert "https://download.pytorch.org/whl/cpu" in dockerfile
    assert "python -m pip install torch" in dockerfile
    assert "python -m pip install '.[local-embeddings]'" in dockerfile
    assert "EMBEDDING_PROVIDER=sentence-transformers" in dockerfile
    assert "EMBEDDING_MODEL=BAAI/bge-small-zh-v1.5" in dockerfile
    assert "EMBEDDING_DIMENSION=512" in dockerfile
    assert "HF_HOME=/models/huggingface" in dockerfile
    assert "SENTENCE_TRANSFORMERS_HOME=/models/huggingface" in dockerfile
    assert "HF_HUB_OFFLINE=1" in dockerfile
    assert "TRANSFORMERS_OFFLINE=1" in dockerfile
    assert "COPY model-cache/huggingface /models/huggingface" in dockerfile
    assert "FRONTEND_DIST_DIR=/app/frontend/dist" in dockerfile
    assert "sed -i 's/\\r$//'" in dockerfile
    assert "EMBEDDING_PROVIDER=deterministic" not in dockerfile
    assert "EMBEDDING_MODEL=deterministic-hash-v1" not in dockerfile


def test_docker_context_excludes_video_crawler_runtime_artifacts() -> None:
    dockerignore = (ROOT / ".dockerignore").read_text(encoding="utf-8")

    assert "skills/video-crawler/downloads/" in dockerignore
    assert "skills/video-crawler/cookies*.txt" in dockerignore
    assert "model-cache/*" in dockerignore
    assert "!model-cache/huggingface/**" in dockerignore


def test_deploy_compose_uses_vendored_local_embedding_cache() -> None:
    compose = (ROOT / "docker-compose.deploy.yml").read_text(encoding="utf-8")

    assert "QDRANT_COLLECTION: content_chunks_v2" in compose
    assert "EMBEDDING_PROVIDER: sentence-transformers" in compose
    assert "EMBEDDING_MODEL: BAAI/bge-small-zh-v1.5" in compose
    assert "EMBEDDING_DIMENSION: 512" in compose
    assert "HF_HUB_OFFLINE: 1" in compose
    assert "TRANSFORMERS_OFFLINE: 1" in compose
    assert "EMBEDDING_PROVIDER: deterministic" not in compose
    assert "EMBEDDING_PROVIDER: siliconflow" not in compose
    assert "HF_HOME: /models/huggingface" in compose
    assert "SENTENCE_TRANSFORMERS_HOME: /models/huggingface" in compose
    assert "MODEL_CACHE_DIR" not in compose
    assert "./model-cache/huggingface" not in compose


def test_docker_build_requires_local_embedding_cache() -> None:
    build_script = (ROOT / "scripts/docker_build.sh").read_text(encoding="utf-8")

    assert "models--BAAI--bge-small-zh-v1.5" in build_script
    assert "snapshot_download" in build_script
    assert "offline target machines" in build_script
