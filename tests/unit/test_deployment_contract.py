from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_jobs_image_installs_all_scheduled_pipeline_dependencies() -> None:
    dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")

    assert "COPY tools ./tools" in dockerfile
    assert ".[local-embeddings,video-keyframes,video-asr,supplier]" in dockerfile


def test_deploy_compose_defines_an_internal_jobs_runner_with_persistent_artifacts() -> None:
    compose = (ROOT / "docker-compose.deploy.yml").read_text(encoding="utf-8")

    assert "  jobs:" in compose
    assert "      - jobs" in compose
    assert "    env_file:\n      - .env" in compose
    assert "HTTP_PROXY: ${HTTP_PROXY:-}" in compose
    assert "HTTPS_PROXY: ${HTTPS_PROXY:-}" in compose
    assert "NO_PROXY: localhost,127.0.0.1,postgres,qdrant,app,jobs" in compose
    assert (
        "${COLLECTOR_DATA_DIR:-./data/collector}/video-downloads:/app/tools/video-crawler/scripts/downloads"
        in compose
    )
    assert (
        "${COLLECTOR_DATA_DIR:-./data/collector}/supplier:/app/tools/supplier-information/search_results"
        in compose
    )


def test_deployment_assets_document_host_timer_triggering_container_jobs() -> None:
    guide = (ROOT / "docs/deployment-guide.md").read_text(encoding="utf-8")

    assert "systemd" in guide
    assert "docker compose -f docker-compose.deploy.yml run --rm jobs" in guide
    assert "HTTP_PROXY" in guide
    assert "COLLECTOR_DATA_DIR" in guide
    assert (ROOT / "deploy/systemd/intelligence-rag-video-collect.service").is_file()
    assert (ROOT / "deploy/systemd/intelligence-rag-video-collect.timer").is_file()


def test_deploy_configuration_is_portable_and_keeps_state_outside_container() -> None:
    compose = (ROOT / "docker-compose.deploy.yml").read_text(encoding="utf-8")
    assert "${POSTGRES_PASSWORD:?" in compose
    assert "${APP_BIND_ADDRESS:-127.0.0.1}" in compose
    assert "VIDEO_SEARCH_STATE_PATH: /data/video/search-state.json" in compose
    assert "${COLLECTOR_DATA_DIR:-./data/collector}/video-state:/data/video" in compose
    assert "https_proxy: ${HTTPS_PROXY:-}" in compose
    assert "host.docker.internal:host-gateway" in compose
    assert '      - "127.0.0.1:54329:5432"' not in compose
    assert '      - "127.0.0.1:6333:6333"' not in compose


def test_build_context_excludes_collected_evidence() -> None:
    ignore = (ROOT / ".dockerignore").read_text(encoding="utf-8")
    for path in (
        "data/",
        "tools/video-crawler/scripts/downloads/",
        "tools/video-crawler/scripts/search-state.json",
        "tools/supplier-information/search_results/",
        "tools/supplier-information/logs/",
    ):
        assert path in ignore
