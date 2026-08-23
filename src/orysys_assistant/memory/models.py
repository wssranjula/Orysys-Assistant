"""Persisted conversation records containing only approved memory fields."""

from datetime import UTC, datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class MemoryModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class StoredMessage(MemoryModel):
    role: Literal["user", "assistant"]
    content: str
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class ConversationRecord(MemoryModel):
    conversation_id: UUID
    user_id: str
    messages: list[StoredMessage] = Field(default_factory=list)
    summary: str = ""
    evidence_ids: list[str] = Field(default_factory=list)
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class LongTermPreference(MemoryModel):
    """An explicit user preference, isolated from conversation checkpoints."""

    user_id: str
    key: str = Field(pattern=r"^[a-z][a-z0-9_]{1,49}$")
    value: str = Field(min_length=1, max_length=500)
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
