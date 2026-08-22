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
    retrieval_min_sparse_score: float = Field(default=0.1, ge=0, le=10)
    retrieval_retry_attempts: int = Field(default=2, ge=0, le=3)
    research_max_initial_tasks: int = Field(default=4, gt=0, le=8)
    research_max_followup_tasks: int = Field(default=2, ge=0, le=4)
    research_max_recursion_depth: int = Field(default=2, ge=0, le=4)
    research_max_parallel_workers: int = Field(default=3, gt=0, le=8)
    research_max_total_tool_calls: int = Field(default=20, gt=0, le=50)
    research_max_chunks_per_worker: int = Field(default=6, gt=0, le=12)
    research_worker_timeout_seconds: float = Field(default=25, gt=0, le=60)
    research_overall_timeout_seconds: float = Field(default=90, gt=0, le=180)
    embedding_model: str = "text-embedding-3-small"
    embedding_dimension: int = Field(default=1536, gt=0)
    memory_backend: str = "memory"
    database_url: str = "postgresql+asyncpg://postgres:postgres@localhost:5432/orysys_assistant"
    memory_max_recent_messages: int = Field(default=20, gt=0, le=100)
    memory_max_summary_characters: int = Field(default=8_000, gt=0, le=20_000)
    analysis_max_records: int = Field(default=1_000, gt=0, le=5_000)
    mcp_backend: str = "memory"
    mcp_server_url: str = "http://localhost:8100/mcp"
    mcp_timeout_seconds: float = Field(default=10, gt=0, le=60)
    mcp_max_result_bytes: int = Field(default=100_000, gt=0, le=1_000_000)
    mcp_retry_attempts: int = Field(default=1, ge=0, le=2)
    llm_retry_attempts: int = Field(default=1, ge=0, le=2)
    request_timeout_seconds: float = Field(default=120, gt=0, le=300)
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
    langsmith_endpoint: str = "https://api.smith.langchain.com"
    langsmith_project: str = "commercial-bank-assistant"
    openai_api_key: str | None = None
    agent_model: str = "gpt-5-mini"
    agent_synthesis_enabled: bool = True
    embedding_provider: str = "openai"

    mock_token_delay_seconds: float = Field(default=0.025, ge=0, le=2)

    @property
    def langsmith_enabled(self) -> bool:
        return self.langsmith_tracing and bool(self.langsmith_api_key)


@lru_cache
def get_settings() -> Settings:
    return Settings()
