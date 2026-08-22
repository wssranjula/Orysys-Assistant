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

    auth_viewer_token: str = "phase2-viewer-demo-token"
    auth_analyst_token: str = "phase2-analyst-demo-token"
    auth_admin_token: str = "phase2-administrator-demo-token"
    organization_id: str = "commercial-bank"
    pinecone_namespace: str = "commercial-bank"
    pinecone_api_key: str | None = None
    pinecone_index: str = "commercial-bank-assistant"
    pinecone_host: str | None = None
    retrieval_backend: str = "memory"
    retrieval_dense_weight: float = Field(default=0.65, ge=0, le=1)
    retrieval_sparse_weight: float = Field(default=0.35, ge=0, le=1)
    retrieval_candidate_count: int = Field(default=20, gt=0, le=100)
    retrieval_final_count: int = Field(default=6, gt=0, le=20)
    embedding_model: str = "text-embedding-3-small"
    embedding_dimension: int = Field(default=1536, gt=0)
    redis_url: str = "redis://localhost:6379/0"
    rate_limit_backend: str = "redis"
    rate_limit_viewer_capacity: int = Field(default=10, gt=0)
    rate_limit_viewer_refill_per_minute: float = Field(default=5, gt=0)
    rate_limit_analyst_capacity: int = Field(default=30, gt=0)
    rate_limit_analyst_refill_per_minute: float = Field(default=15, gt=0)
    rate_limit_administrator_capacity: int = Field(default=60, gt=0)
    rate_limit_administrator_refill_per_minute: float = Field(default=30, gt=0)

    langsmith_tracing: bool = False
    langsmith_api_key: str | None = None
    langsmith_project: str = "commercial-bank-assistant"
    openai_api_key: str | None = None
    embedding_provider: str = "openai"

    mock_token_delay_seconds: float = Field(default=0.025, ge=0, le=2)

    @property
    def langsmith_enabled(self) -> bool:
        return self.langsmith_tracing and bool(self.langsmith_api_key)


@lru_cache
def get_settings() -> Settings:
    return Settings()
