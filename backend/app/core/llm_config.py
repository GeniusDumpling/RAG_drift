"""Shared configuration for OpenAI-compatible text model services."""

from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ENV_FILE = Path(__file__).resolve().parents[3] / ".env"
if not PROJECT_ENV_FILE.is_file():
    # Installed packages (including the app image) use the workspace environment.
    PROJECT_ENV_FILE = Path.cwd() / ".env"


class LLMSettings(BaseSettings):
    # Resolve from the project root so tools behave the same from any working directory.
    model_config = SettingsConfigDict(
        env_file=PROJECT_ENV_FILE, env_file_encoding="utf-8", extra="ignore"
    )

    base_url: str = Field(default="https://api.deepseek.com/v1", alias="BASE_URL")
    llm_api_key: str | None = Field(default=None, alias="LLM_API_KEY", repr=False)
    llm_model: str = Field(default="deepseek-chat", alias="LLM_MODEL")
