from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_env: str = Field(default="dev", alias="APP_ENV")
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
    qdrant_collection: str = Field(default="content_chunks_v1", alias="QDRANT_COLLECTION")
    embedding_provider: str = Field(default="deterministic", alias="EMBEDDING_PROVIDER")
    embedding_model: str = Field(default="deterministic-hash-v1", alias="EMBEDDING_MODEL")
    llm_provider: str = Field(default="fake", alias="LLM_PROVIDER")
    agent_timeout_seconds: int = Field(default=20, alias="AGENT_TIMEOUT_SECONDS")
    worker_poll_interval_seconds: int = Field(default=2, alias="WORKER_POLL_INTERVAL_SECONDS")

    # SiliconFlow / VLM settings
    siliconflow_api_key: str | None = Field(default=None, alias="SILICONFLOW_API_KEY")
    siliconflow_base_url: str = Field(
        default="https://api.siliconflow.cn/v1", alias="SILICONFLOW_BASE_URL"
    )
    vlm_model: str = Field(
        default="Qwen/Qwen3-Omni-30B-A3B-Instruct", alias="VLM_MODEL"
    )

    # Embedding settings
    embedding_provider: str = Field(default="fake", alias="EMBEDDING_PROVIDER")
    embedding_model: str = Field(default="BAAI/bge-m3", alias="EMBEDDING_MODEL")
    embedding_dimension: int = Field(default=1024, alias="EMBEDDING_DIMENSION")


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
