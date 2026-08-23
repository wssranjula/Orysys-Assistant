"""Shared test doubles for the autonomous specialist loops.

Specialists are now model-driven, so their behaviour is exercised with a scripted chat
model rather than with a credential. Scripting the exact tool calls keeps assertions
deterministic while still running the real agent loop, real middleware, and the real
gateway underneath.
"""

from typing import Any

import pytest
from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage

from orysys_assistant.config import Settings
from orysys_assistant.domain.models import Role
from orysys_assistant.security.access_scope import AccessScopeService
from orysys_assistant.security.models import TrustedRequestContext, UserIdentity


class ScriptedChatModel(FakeMessagesListChatModel):
    """Replay a fixed sequence of assistant turns, including tool calls.

    The stock fake model refuses ``bind_tools``, which every agent loop performs. Tool
    binding is accepted and ignored here because the script, not the model, decides
    which tools get called.
    """

    def bind_tools(self, tools: Any, **kwargs: Any) -> Any:
        return self


def tool_turn(*calls: tuple[str, dict[str, Any]]) -> AIMessage:
    """One assistant turn requesting the given tools, in parallel when several."""
    return AIMessage(
        content="",
        tool_calls=[
            {"name": name, "args": args, "id": f"call_{index}"}
            for index, (name, args) in enumerate(calls, start=1)
        ],
    )


def text_turn(content: str) -> AIMessage:
    return AIMessage(content=content)


def scripted_model(*turns: AIMessage) -> ScriptedChatModel:
    return ScriptedChatModel(responses=list(turns))


def request_context(role: Role = Role.ANALYST, user_id: str = "test-user") -> TrustedRequestContext:
    identity = UserIdentity(
        user_id=f"{user_id}-{role.value}",
        username=f"{role.value}@commercialbank.test",
        display_name="Test User",
        role=role,
        department="payments",
    )
    return TrustedRequestContext(
        identity=identity,
        access_scope=AccessScopeService(Settings(_env_file=None)).build(identity),
        rate_limit_remaining=10,
    )


@pytest.fixture
def scripted_model_factory() -> Any:
    return scripted_model
