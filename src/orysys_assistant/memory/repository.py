"""Owner-isolated in-memory and PostgreSQL conversation repositories."""

import asyncio
import json
from typing import Any, Protocol, cast
from uuid import UUID

import asyncpg  # type: ignore[import-untyped]

from orysys_assistant.domain.errors import AuthorizationError
from orysys_assistant.memory.models import ConversationRecord, LongTermPreference, StoredMessage


class ConversationRepository(Protocol):
    persistence_name: str

    async def get_or_create(self, conversation_id: UUID, user_id: str) -> ConversationRecord: ...

    async def get(self, conversation_id: UUID, user_id: str) -> ConversationRecord | None: ...

    async def append_turn(
        self,
        conversation_id: UUID,
        user_id: str,
        user_message: str,
        assistant_message: str,
        evidence_ids: list[str],
    ) -> ConversationRecord: ...

    async def list_preferences(self, user_id: str) -> list[LongTermPreference]: ...

    async def upsert_preference(self, user_id: str, key: str, value: str) -> LongTermPreference: ...

    async def delete_preference(self, user_id: str, key: str) -> bool: ...

    async def close(self) -> None: ...


def _conversation_summary(messages: list[StoredMessage], max_characters: int) -> str:
    """Build a bounded transcript that keeps whole recent turns instead of mid-sentence tails."""

    lines = [f"{message.role.title()}: {message.content}" for message in messages]
    if not lines:
        return ""
    transcript = "\n".join(lines)
    if len(transcript) <= max_characters:
        return transcript

    kept: list[str] = []
    for line in reversed(lines):
        candidate = "\n".join([line, *kept]) if kept else line
        if len(candidate) + 64 > max_characters and kept:
            break
        kept.insert(0, line)

    omitted = len(lines) - len(kept)
    body = "\n".join(kept)
    if omitted:
        prefix = f"[{omitted} earlier turn(s) omitted from this summary]\n"
        while len(prefix) + len(body) > max_characters and len(kept) > 1:
            omitted += 1
            kept.pop(0)
            body = "\n".join(kept)
            prefix = f"[{omitted} earlier turn(s) omitted from this summary]\n"
        return f"{prefix}{body}"
    return body


def _updated_record(
    record: ConversationRecord,
    user_message: str,
    assistant_message: str,
    evidence_ids: list[str],
    max_messages: int,
    max_summary_characters: int,
) -> ConversationRecord:
    messages = [
        *record.messages,
        StoredMessage(role="user", content=user_message),
        StoredMessage(role="assistant", content=assistant_message),
    ][-max_messages:]
    summary = _conversation_summary(messages, max_summary_characters)
    return record.model_copy(
        update={
            "messages": messages,
            "summary": summary,
            "evidence_ids": list(dict.fromkeys([*record.evidence_ids, *evidence_ids])),
        }
    )


class InMemoryConversationRepository:
    persistence_name = "in_memory"

    def __init__(self, max_messages: int, max_summary_characters: int) -> None:
        self._records: dict[UUID, ConversationRecord] = {}
        self._preferences: dict[tuple[str, str], LongTermPreference] = {}
        self._lock = asyncio.Lock()
        self._max_messages = max_messages
        self._max_summary_characters = max_summary_characters

    async def get_or_create(self, conversation_id: UUID, user_id: str) -> ConversationRecord:
        async with self._lock:
            record = self._records.get(conversation_id)
            if record is None:
                record = ConversationRecord(conversation_id=conversation_id, user_id=user_id)
                self._records[conversation_id] = record
            self._require_owner(record, user_id)
            return record.model_copy(deep=True)

    async def get(self, conversation_id: UUID, user_id: str) -> ConversationRecord | None:
        async with self._lock:
            record = self._records.get(conversation_id)
            if record is None:
                return None
            self._require_owner(record, user_id)
            return record.model_copy(deep=True)

    async def append_turn(
        self,
        conversation_id: UUID,
        user_id: str,
        user_message: str,
        assistant_message: str,
        evidence_ids: list[str],
    ) -> ConversationRecord:
        async with self._lock:
            record = self._records.get(conversation_id)
            if record is None:
                record = ConversationRecord(conversation_id=conversation_id, user_id=user_id)
            self._require_owner(record, user_id)
            updated = _updated_record(
                record,
                user_message,
                assistant_message,
                evidence_ids,
                self._max_messages,
                self._max_summary_characters,
            )
            self._records[conversation_id] = updated
            return updated.model_copy(deep=True)

    async def close(self) -> None:
        return None

    async def list_preferences(self, user_id: str) -> list[LongTermPreference]:
        async with self._lock:
            return sorted(
                (
                    item.model_copy(deep=True)
                    for (owner, _), item in self._preferences.items()
                    if owner == user_id
                ),
                key=lambda item: item.key,
            )

    async def upsert_preference(self, user_id: str, key: str, value: str) -> LongTermPreference:
        preference = LongTermPreference(user_id=user_id, key=key, value=value)
        async with self._lock:
            self._preferences[(user_id, key)] = preference
        return preference.model_copy(deep=True)

    async def delete_preference(self, user_id: str, key: str) -> bool:
        async with self._lock:
            return self._preferences.pop((user_id, key), None) is not None

    @staticmethod
    def _require_owner(record: ConversationRecord, user_id: str) -> None:
        if record.user_id != user_id:
            raise AuthorizationError("You are not authorized to access this conversation.")


class PostgresConversationRepository:
    persistence_name = "postgres"

    def __init__(self, database_url: str, max_messages: int, max_summary_characters: int) -> None:
        self._database_url = database_url.replace("postgresql+asyncpg://", "postgresql://")
        self._max_messages = max_messages
        self._max_summary_characters = max_summary_characters
        self._pool: asyncpg.Pool | None = None

    async def start(self) -> None:
        if self._pool is not None:
            return
        self._pool = await asyncpg.create_pool(self._database_url, min_size=1, max_size=5)
        async with self._pool.acquire() as connection:
            await connection.execute(
                """
                CREATE TABLE IF NOT EXISTS conversations (
                    conversation_id UUID PRIMARY KEY,
                    user_id TEXT NOT NULL,
                    messages JSONB NOT NULL DEFAULT '[]'::jsonb,
                    summary TEXT NOT NULL DEFAULT '',
                    evidence_ids JSONB NOT NULL DEFAULT '[]'::jsonb,
                    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                )
                """
            )
            await connection.execute(
                """
                CREATE TABLE IF NOT EXISTS long_term_preferences (
                    user_id TEXT NOT NULL,
                    preference_key TEXT NOT NULL,
                    preference_value TEXT NOT NULL,
                    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    PRIMARY KEY (user_id, preference_key)
                )
                """
            )

    async def get_or_create(self, conversation_id: UUID, user_id: str) -> ConversationRecord:
        pool = self._require_pool()
        async with pool.acquire() as connection:
            await connection.execute(
                """
                INSERT INTO conversations (conversation_id, user_id)
                VALUES ($1, $2) ON CONFLICT (conversation_id) DO NOTHING
                """,
                conversation_id,
                user_id,
            )
            row = await connection.fetchrow(
                "SELECT * FROM conversations WHERE conversation_id = $1", conversation_id
            )
        record = self._from_row(row)
        self._require_owner(record, user_id)
        return record

    async def get(self, conversation_id: UUID, user_id: str) -> ConversationRecord | None:
        pool = self._require_pool()
        async with pool.acquire() as connection:
            row = await connection.fetchrow(
                "SELECT * FROM conversations WHERE conversation_id = $1", conversation_id
            )
        if row is None:
            return None
        record = self._from_row(row)
        self._require_owner(record, user_id)
        return record

    async def append_turn(
        self,
        conversation_id: UUID,
        user_id: str,
        user_message: str,
        assistant_message: str,
        evidence_ids: list[str],
    ) -> ConversationRecord:
        pool = self._require_pool()
        async with pool.acquire() as connection, connection.transaction():
            row = await connection.fetchrow(
                "SELECT * FROM conversations WHERE conversation_id = $1 FOR UPDATE",
                conversation_id,
            )
            if row is None:
                await connection.execute(
                    "INSERT INTO conversations (conversation_id, user_id) VALUES ($1, $2)",
                    conversation_id,
                    user_id,
                )
                row = await connection.fetchrow(
                    "SELECT * FROM conversations WHERE conversation_id = $1 FOR UPDATE",
                    conversation_id,
                )
            record = self._from_row(row)
            self._require_owner(record, user_id)
            updated = _updated_record(
                record,
                user_message,
                assistant_message,
                evidence_ids,
                self._max_messages,
                self._max_summary_characters,
            )
            await connection.execute(
                """
                UPDATE conversations
                SET messages = $2::jsonb, summary = $3, evidence_ids = $4::jsonb,
                    updated_at = NOW()
                WHERE conversation_id = $1
                """,
                conversation_id,
                json.dumps([item.model_dump(mode="json") for item in updated.messages]),
                updated.summary,
                json.dumps(updated.evidence_ids),
            )
        return updated

    async def close(self) -> None:
        if self._pool is not None:
            await self._pool.close()
            self._pool = None

    async def list_preferences(self, user_id: str) -> list[LongTermPreference]:
        pool = self._require_pool()
        async with pool.acquire() as connection:
            rows = await connection.fetch(
                """
                SELECT user_id, preference_key, preference_value, updated_at
                FROM long_term_preferences WHERE user_id = $1 ORDER BY preference_key
                """,
                user_id,
            )
        return [
            LongTermPreference(
                user_id=row["user_id"],
                key=row["preference_key"],
                value=row["preference_value"],
                updated_at=row["updated_at"],
            )
            for row in rows
        ]

    async def upsert_preference(self, user_id: str, key: str, value: str) -> LongTermPreference:
        pool = self._require_pool()
        async with pool.acquire() as connection:
            row = await connection.fetchrow(
                """
                INSERT INTO long_term_preferences (user_id, preference_key, preference_value)
                VALUES ($1, $2, $3)
                ON CONFLICT (user_id, preference_key) DO UPDATE
                SET preference_value = EXCLUDED.preference_value, updated_at = NOW()
                RETURNING user_id, preference_key, preference_value, updated_at
                """,
                user_id,
                key,
                value,
            )
        return LongTermPreference(
            user_id=row["user_id"],
            key=row["preference_key"],
            value=row["preference_value"],
            updated_at=row["updated_at"],
        )

    async def delete_preference(self, user_id: str, key: str) -> bool:
        pool = self._require_pool()
        async with pool.acquire() as connection:
            result = await connection.execute(
                "DELETE FROM long_term_preferences WHERE user_id = $1 AND preference_key = $2",
                user_id,
                key,
            )
        return cast(str, result) == "DELETE 1"

    def _require_pool(self) -> asyncpg.Pool:
        if self._pool is None:
            raise RuntimeError("PostgreSQL conversation repository has not been started.")
        return self._pool

    @staticmethod
    def _from_row(row: Any) -> ConversationRecord:
        if row is None:
            raise RuntimeError("Conversation row was not found.")
        messages = row["messages"]
        evidence_ids = row["evidence_ids"]
        if isinstance(messages, str):
            messages = json.loads(messages)
        if isinstance(evidence_ids, str):
            evidence_ids = json.loads(evidence_ids)
        return ConversationRecord(
            conversation_id=row["conversation_id"],
            user_id=row["user_id"],
            messages=[StoredMessage.model_validate(item) for item in messages],
            summary=row["summary"],
            evidence_ids=evidence_ids,
            updated_at=row["updated_at"],
        )

    @staticmethod
    def _require_owner(record: ConversationRecord, user_id: str) -> None:
        if record.user_id != user_id:
            raise AuthorizationError("You are not authorized to access this conversation.")
