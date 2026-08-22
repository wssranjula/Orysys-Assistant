"""Environment-backed application settings."""

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    app_env: str = "development"
    app_name: str = "Commercial Bank AI Assistant"
    log_level: str = "INFO"
    api_host: str = "0.0.0.0"
    api_port: int = 8000
    ui_api_base_url: str = "http://localhost:8000"

    langsmith_tracing: bool = False
    langsmith_api_key: str | None = None
    langsmith_project: str = "commercial-bank-assistant"

    mock_token_delay_seconds: float = Field(default=0.025, ge=0, le=2)

    @property
    def langsmith_enabled(self) -> bool:
        return self.langsmith_tracing and bool(self.langsmith_api_key)


@lru_cache
def get_settings() -> Settings:
    return Settings()
