"""Owner-scoped storage for answer-quality feedback."""

import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol
from uuid import UUID, uuid4

from orysys_assistant.domain.errors import AuthorizationError
from orysys_assistant.domain.models import FeedbackRequest


@dataclass(frozen=True, slots=True)
class StoredFeedback:
    feedback_id: UUID
    user_id: str
    conversation_id: UUID
    response_id: UUID
    rating: int
    comment: str | None
    created_at: datetime


class FeedbackRepository(Protocol):
    persistence_name: str

    async def record(self, user_id: str, payload: FeedbackRequest) -> StoredFeedback: ...

    async def list_for_user(self, user_id: str, *, limit: int = 50) -> list[StoredFeedback]: ...


class InMemoryFeedbackRepository:
    persistence_name = "in_memory"

    def __init__(self) -> None:
        self._records: list[StoredFeedback] = []
        self._lock = asyncio.Lock()

    async def record(self, user_id: str, payload: FeedbackRequest) -> StoredFeedback:
        item = StoredFeedback(
            feedback_id=uuid4(),
            user_id=user_id,
            conversation_id=payload.conversation_id,
            response_id=payload.response_id,
            rating=payload.rating,
            comment=payload.comment,
            created_at=datetime.now(UTC),
        )
        async with self._lock:
            self._records.append(item)
        return item

    async def list_for_user(self, user_id: str, *, limit: int = 50) -> list[StoredFeedback]:
        async with self._lock:
            return [item for item in reversed(self._records) if item.user_id == user_id][:limit]


def require_owner(record: StoredFeedback, user_id: str) -> None:
    if record.user_id != user_id:
        raise AuthorizationError("You do not have access to this feedback record.")
