"""Public API and event contracts frozen during Phase 0."""

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator


class StrictModel(BaseModel):
    """Base model that rejects accidental contract expansion."""

    model_config = ConfigDict(extra="forbid")


class Role(StrEnum):
    VIEWER = "viewer"
    ANALYST = "analyst"
    ADMINISTRATOR = "administrator"


class ResponseStatus(StrEnum):
    COMPLETE = "complete"
    PARTIAL = "partial"
    INSUFFICIENT_EVIDENCE = "insufficient_evidence"
    FAILED = "failed"


class ActivityStatus(StrEnum):
    STARTED = "started"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    DEGRADED = "degraded"
    DENIED = "denied"
    FAILED = "failed"


class ActivityEventType(StrEnum):
    REQUEST_RECEIVED = "request_received"
    AUTHENTICATION_COMPLETED = "authentication_completed"
    RATE_LIMIT_CHECKED = "rate_limit_checked"
    AGENT_STARTED = "agent_started"
    ROUTING_COMPLETED = "routing_completed"
    SUBAGENT_STARTED = "subagent_started"
    SUBAGENT_COMPLETED = "subagent_completed"
    RESEARCH_NODE_STARTED = "research_node_started"
    RESEARCH_NODE_COMPLETED = "research_node_completed"
    RETRIEVAL_STARTED = "retrieval_started"
    RETRIEVAL_COMPLETED = "retrieval_completed"
    TOOL_STARTED = "tool_started"
    TOOL_COMPLETED = "tool_completed"
    TOOL_DENIED = "tool_denied"
    MEMORY_UPDATED = "memory_updated"
    MEMORY_LOADED = "memory_loaded"
    VALIDATION_STARTED = "validation_started"
    VALIDATION_FAILED = "validation_failed"
    ANSWER_STREAMING = "answer_streaming"
    REQUEST_COMPLETED = "request_completed"
    REQUEST_FAILED = "request_failed"


class ChatRequest(StrictModel):
    message: str = Field(min_length=1, max_length=8_000)
    conversation_id: UUID | None = None

    @field_validator("message")
    @classmethod
    def message_must_contain_text(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("message must contain non-whitespace text")
        return value


class Citation(StrictModel):
    citation_id: str = Field(min_length=1, max_length=20)
    evidence_id: str = Field(min_length=1, max_length=100)
    document_id: str = Field(min_length=1, max_length=200)
    title: str = Field(min_length=1, max_length=500)
    chunk_id: str = Field(min_length=1, max_length=250)
    source_path: str = Field(min_length=1, max_length=1_000)


class ActivityEvent(StrictModel):
    event_type: ActivityEventType
    request_id: UUID
    conversation_id: UUID
    status: ActivityStatus
    message: str = Field(min_length=1, max_length=500)
    agent: str | None = Field(default=None, max_length=100)
    node: str | None = Field(default=None, max_length=100)
    metadata: dict[str, Any] = Field(default_factory=dict)
    timestamp: datetime = Field(default_factory=lambda: datetime.now(UTC))


class AnswerDelta(StrictModel):
    request_id: UUID
    conversation_id: UUID
    sequence: int = Field(ge=0)
    text: str = Field(min_length=1)


class FinalResponse(StrictModel):
    request_id: UUID
    conversation_id: UUID
    status: ResponseStatus
    answer: str = Field(max_length=20_000)
    citations: list[Citation] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    degraded: bool = False


class ApiError(StrictModel):
    code: str = Field(pattern=r"^[a-z][a-z0-9_]*$")
    message: str = Field(min_length=1, max_length=500)
    request_id: UUID
    retryable: bool = False
    details: dict[str, Any] = Field(default_factory=dict)


class ErrorEnvelope(StrictModel):
    error: ApiError


class ConversationSnapshot(StrictModel):
    conversation_id: UUID
    messages: list[dict[str, str]] = Field(default_factory=list)
    summary: str = ""
    evidence_ids: list[str] = Field(default_factory=list)
    persistence: str = "available"


class FeedbackRequest(StrictModel):
    conversation_id: UUID
    response_id: UUID
    rating: int = Field(ge=-1, le=1)
    comment: str | None = Field(default=None, max_length=1_000)


class FeedbackAcknowledgement(StrictModel):
    accepted: bool
    persistence: str = "not_available_in_phase_1"


class HealthResponse(StrictModel):
    status: str
    components: dict[str, str] = Field(default_factory=dict)


class LoginRequest(StrictModel):
    username: str = Field(min_length=3, max_length=254)
    password: SecretStr


class TokenResponse(StrictModel):
    access_token: str
    token_type: str = "bearer"
    user_id: str
    display_name: str
    role: Role
