from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_env: str = Field(default="dev", alias="APP_ENV")
    database_browser_enabled: bool = Field(
        default=False, alias="DATABASE_BROWSER_ENABLED"
    )
    api_host: str = Field(default="0.0.0.0", alias="API_HOST")
    api_port: int = Field(default=8000, alias="API_PORT")
    database_url: str = Field(
        default="postgresql+psycopg://intelligence:intelligence@localhost:54329/intelligence_rag",
        alias="DATABASE_URL",
    )
    sync_database_url: str = Field(
        default="postgresql+psycopg://intelligence:intelligence@localhost:54329/intelligence_rag",
        alias="SYNC_DATABASE_URL",
    )
    qdrant_url: str = Field(default="http://localhost:6333", alias="QDRANT_URL")
    qdrant_collection: str = Field(default="content_chunks_v2", alias="QDRANT_COLLECTION")
    embedding_provider: str = Field(default="sentence-transformers", alias="EMBEDDING_PROVIDER")
    embedding_model: str = Field(default="BAAI/bge-small-zh-v1.5", alias="EMBEDDING_MODEL")
    embedding_dimension: int = Field(default=512, alias="EMBEDDING_DIMENSION")
    llm_provider: str = Field(default="fake", alias="LLM_PROVIDER")
    agent_timeout_seconds: int = Field(default=20, alias="AGENT_TIMEOUT_SECONDS")
    worker_poll_interval_seconds: int = Field(default=2, alias="WORKER_POLL_INTERVAL_SECONDS")

    # VLM settings
    vlm_base_url: str = Field(
        default="https://api.siliconflow.cn/v1", alias="VLM_BASE_URL"
    )
    vlm_api_key: str | None = Field(default=None, alias="VLM_API_KEY")
    vlm_model: str = Field(
        default="Qwen/Qwen3-Omni-30B-A3B-Instruct", alias="VLM_MODEL"
    )

    # Literature research settings
    literature_llm_provider: str = Field(default="deepseek", alias="LITERATURE_LLM_PROVIDER")
    deepseek_api_key: str | None = Field(default=None, alias="DEEPSEEK_API_KEY")
    deepseek_base_url: str = Field(
        default="https://api.deepseek.com/v1", alias="DEEPSEEK_BASE_URL"
    )
    deepseek_model: str = Field(default="deepseek-chat", alias="DEEPSEEK_MODEL")
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


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
