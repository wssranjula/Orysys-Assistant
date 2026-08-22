"""Lifecycle owner for conversation storage and LangGraph checkpoints."""

from contextlib import AbstractAsyncContextManager
from typing import Any

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer

from orysys_assistant.config import Settings
from orysys_assistant.memory.repository import (
    ConversationRepository,
    InMemoryConversationRepository,
    PostgresConversationRepository,
)


def _checkpoint_serializer() -> JsonPlusSerializer:
    return JsonPlusSerializer(
        pickle_fallback=False,
        allowed_msgpack_modules=[
            ("orysys_assistant.security.models", "AccessScope"),
            ("orysys_assistant.retrieval.models", "SearchFilters"),
            ("orysys_assistant.retrieval.models", "Evidence"),
            ("orysys_assistant.agent.models", "Finding"),
            ("orysys_assistant.agent.models", "ResearchPlan"),
            ("orysys_assistant.agent.models", "ResearchTask"),
            ("orysys_assistant.agent.models", "ResearchTaskResult"),
        ],
    )


class MemoryRuntime:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._started = False
        self._checkpoint_context: AbstractAsyncContextManager[Any] | None = None
        if settings.memory_backend == "memory":
            self.repository: ConversationRepository = InMemoryConversationRepository(
                settings.memory_max_recent_messages,
                settings.memory_max_summary_characters,
            )
            self.checkpointer: Any = InMemorySaver(serde=_checkpoint_serializer())
        elif settings.memory_backend == "postgres":
            self.repository = PostgresConversationRepository(
                settings.database_url,
                settings.memory_max_recent_messages,
                settings.memory_max_summary_characters,
            )
            self.checkpointer = None
        else:
            raise ValueError("MEMORY_BACKEND must be 'memory' or 'postgres'.")

    async def start(self) -> None:
        if self._started:
            return
        if isinstance(self.repository, PostgresConversationRepository):
            await self.repository.start()
            serializer = _checkpoint_serializer()
            uri = self._settings.database_url.replace("postgresql+asyncpg://", "postgresql://")
            self._checkpoint_context = AsyncPostgresSaver.from_conn_string(uri, serde=serializer)
            self.checkpointer = await self._checkpoint_context.__aenter__()
            await self.checkpointer.setup()
        self._started = True

    async def close(self) -> None:
        await self.repository.close()
        if self._checkpoint_context is not None:
            await self._checkpoint_context.__aexit__(None, None, None)
            self._checkpoint_context = None
        self._started = False
