from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field
from sqlalchemy.engine import make_url

from app.core.llm_config import LLMSettings


class Settings(LLMSettings):
    app_env: str = Field(default="dev", alias="APP_ENV")
    database_browser_enabled: bool = Field(default=False, alias="DATABASE_BROWSER_ENABLED")
    document_database_url: str | None = Field(default=None, alias="DOCUMENT_DATABASE_URL")
    document_qdrant_url: str | None = Field(default=None, alias="DOCUMENT_QDRANT_URL")
    document_qdrant_collection: str = Field(
        default="drone_document_chunks_bge_m3_v1", alias="DOCUMENT_QDRANT_COLLECTION"
    )
    document_artifacts_dir: Path = Field(
        default=Path("data/processed/drone-documents"), alias="DOCUMENT_ARTIFACTS_DIR"
    )
    document_artifacts_source_root: Path | None = Field(
        default=None, alias="DOCUMENT_ARTIFACTS_SOURCE_ROOT"
    )
    api_host: str = Field(default="0.0.0.0", alias="API_HOST")
    api_port: int = Field(default=8000, alias="API_PORT")
    drone_documents_dir: Path = Field(
        default=Path("data/DJI无人机文档"), alias="DRONE_DOCUMENTS_DIR"
    )
    parrot_documents_dir: Path = Field(
        default=Path("data/Parrot无人机文档"), alias="PARROT_DOCUMENTS_DIR"
    )
    database_url: str = Field(
        default="postgresql+psycopg://intelligence:intelligence@localhost:54329/intelligence_rag",
        alias="DATABASE_URL",
    )
    sync_database_url: str = Field(
        default="postgresql+psycopg://intelligence:intelligence@localhost:54329/intelligence_rag",
        alias="SYNC_DATABASE_URL",
    )
    qdrant_url: str = Field(default="http://localhost:6333", alias="QDRANT_URL")
    qdrant_collection: str = Field(default="content_chunks_bge_m3_v1", alias="QDRANT_COLLECTION")
    embedding_provider: str = Field(default="siliconflow", alias="EMBEDDING_PROVIDER")
    embedding_model: str = Field(default="BAAI/bge-m3", alias="EMBEDDING_MODEL")
    embedding_dimension: int = Field(default=1024, alias="EMBEDDING_DIMENSION")
    embedding_base_url: str | None = Field(default=None, alias="EMBEDDING_BASE_URL")
    embedding_api_key: str | None = Field(default=None, alias="EMBEDDING_API_KEY")
    reranker_provider: Literal["none", "siliconflow"] = Field(
        default="none", alias="RERANKER_PROVIDER"
    )
    reranker_model: str = Field(default="BAAI/bge-reranker-v2-m3", alias="RERANKER_MODEL")
    reranker_base_url: str = Field(
        default="https://api.siliconflow.cn/v1", alias="RERANKER_BASE_URL"
    )
    reranker_api_key: str | None = Field(default=None, alias="RERANKER_API_KEY")
    reranker_timeout_seconds: float = Field(default=5, gt=0, alias="RERANKER_TIMEOUT_SECONDS")
    reranker_candidate_limit: int = Field(
        default=20, ge=1, le=150, alias="RERANKER_CANDIDATE_LIMIT"
    )
    reranker_document_max_chars: int = Field(
        default=8000, ge=100, alias="RERANKER_DOCUMENT_MAX_CHARS"
    )
    answer_max_evidence: int = Field(default=8, ge=1, le=50, alias="ANSWER_MAX_EVIDENCE")
    llm_provider: str = Field(default="fake", alias="LLM_PROVIDER")
    agent_timeout_seconds: int = Field(default=20, alias="AGENT_TIMEOUT_SECONDS")
    worker_poll_interval_seconds: int = Field(default=2, alias="WORKER_POLL_INTERVAL_SECONDS")

    video_graph_backend: Literal["postgres", "neo4j"] = Field(
        default="postgres", alias="VIDEO_GRAPH_BACKEND"
    )
    neo4j_uri: str = Field(default="bolt://localhost:7687", alias="NEO4J_URI")
    neo4j_user: str = Field(default="neo4j", alias="NEO4J_USER")
    neo4j_password: str | None = Field(default=None, alias="NEO4J_PASSWORD")
    neo4j_database: str = Field(default="neo4j", alias="NEO4J_DATABASE")

    # VLM settings
    vlm_base_url: str = Field(default="https://api.siliconflow.cn/v1", alias="VLM_BASE_URL")
    vlm_api_key: str | None = Field(default=None, alias="VLM_API_KEY")
    vlm_model: str = Field(default="Qwen/Qwen3-Omni-30B-A3B-Instruct", alias="VLM_MODEL")

    # Literature research settings
    literature_database_url: str | None = Field(default=None, alias="LITERATURE_DATABASE_URL")
    literature_sync_database_url: str | None = Field(
        default=None, alias="LITERATURE_SYNC_DATABASE_URL"
    )
    literature_llm_provider: str = Field(
        default="openai-compatible", alias="LITERATURE_LLM_PROVIDER"
    )
    literature_browser_profile_dir: Path = Field(
        default=Path("~/.intelligence-rag/ieee-profile").expanduser(),
        alias="LITERATURE_BROWSER_PROFILE_DIR",
    )
    literature_headless: bool = Field(default=False, alias="LITERATURE_HEADLESS")
    literature_login_timeout_seconds: int = Field(
        default=300, alias="LITERATURE_LOGIN_TIMEOUT_SECONDS"
    )
    literature_pdf_max_bytes: int = Field(
        default=50 * 1024 * 1024, alias="LITERATURE_PDF_MAX_BYTES"
    )
    literature_fulltext_max_chars: int = Field(
        default=200_000, alias="LITERATURE_FULLTEXT_MAX_CHARS"
    )

    @property
    def resolved_literature_database_url(self) -> str:
        return self.literature_database_url or make_url(self.database_url).set(
            database="literature_research"
        ).render_as_string(hide_password=False)

    @property
    def resolved_literature_sync_database_url(self) -> str:
        return self.literature_sync_database_url or self.literature_database_url or make_url(
            self.sync_database_url
        ).set(database="literature_research").render_as_string(hide_password=False)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
