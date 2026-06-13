from app.core.config import Settings
from app.main import app
from fastapi.testclient import TestClient


def test_settings_reads_known_defaults() -> None:
    settings = Settings()
    assert settings.app_env == "dev"
    assert settings.qdrant_collection == "content_chunks_v1"
    assert settings.agent_timeout_seconds == 20


def test_health_endpoint_returns_service_status() -> None:
    client = TestClient(app)
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "service": "intelligence-rag-api"}
