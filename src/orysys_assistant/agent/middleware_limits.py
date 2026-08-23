"""Agent middleware with quieter LangSmith traces and fewer graph nodes."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Sequence
from typing import Annotated, Any, NotRequired, cast

from langchain.agents.middleware import (
    ModelCallLimitMiddleware,
    TodoListMiddleware,
    ToolCallLimitMiddleware,
)
from langchain.agents.middleware.types import AgentMiddleware, AgentState, PrivateStateAttr
from langchain_core.messages import AIMessage, RemoveMessage, ToolCall, ToolMessage
from langchain_core.runnables import RunnableConfig
from langgraph.graph.message import REMOVE_ALL_MESSAGES
from langgraph.prebuilt.tool_node import ToolCallRequest
from langgraph.runtime import Runtime
from langgraph.typing import ContextT
from typing_extensions import override

from orysys_assistant.observability.agent_tracing import (
    QUIET_MIDDLEWARE_TRACE_POLICY,
    app_span_tags,
)


class NamedToolTraceMiddleware(AgentMiddleware):
    """Label LangSmith tool spans with the invoked tool name instead of generic defaults."""

    trace_policy = QUIET_MIDDLEWARE_TRACE_POLICY

    @property
    def name(self) -> str:
        return "NamedToolTrace"

    @staticmethod
    def _named_config(request: ToolCallRequest) -> None:
        tool_name = str(request.tool_call.get("name") or "unknown_tool")
        runtime = request.runtime
        config = cast(RunnableConfig, dict(runtime.config or {}))
        config["run_name"] = tool_name
        tags = [str(tag) for tag in (config.get("tags") or [])]
        for tag in app_span_tags("tool", tool_name):
            if tag not in tags:
                tags.append(tag)
        config["tags"] = tags
        runtime.config = config

    @override
    def wrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], Any],
    ) -> Any:
        self._named_config(request)
        return handler(request)

    @override
    async def awrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], Awaitable[Any]],
    ) -> Any:
        self._named_config(request)
        return await handler(request)


class QuietTodoListMiddleware(TodoListMiddleware):
    """Todo middleware that omits bulky state payloads from LangSmith spans."""

    trace_policy = QUIET_MIDDLEWARE_TRACE_POLICY


class QuietToolCallLimitMiddleware(ToolCallLimitMiddleware):
    """Tool budget middleware with quiet LangSmith spans."""

    trace_policy = QUIET_MIDDLEWARE_TRACE_POLICY


class QuietModelCallLimitMiddleware(ModelCallLimitMiddleware):
    """Model budget middleware with quiet LangSmith spans."""

    trace_policy = QUIET_MIDDLEWARE_TRACE_POLICY


class DelegationOnceState(AgentState):
    """Per-run delegation counts shared by the root orchestrator."""

    run_tool_call_count: NotRequired[Annotated[dict[str, int], PrivateStateAttr]]


class DelegationOnceMiddleware(AgentMiddleware[DelegationOnceState, ContextT]):
    """Allow at most one consultation per delegation tool in a single user turn."""

    trace_policy = QUIET_MIDDLEWARE_TRACE_POLICY

    def __init__(self, tool_names: Sequence[str]) -> None:
        self._tool_names = frozenset(tool_names)

    @property
    def name(self) -> str:
        return "DelegationOnceLimit"

    @staticmethod
    def _last_ai_message(messages: Sequence[Any]) -> AIMessage | None:
        for message in reversed(messages):
            if isinstance(message, AIMessage):
                return message
        return None

    @override
    def after_model(
        self,
        state: DelegationOnceState,
        runtime: Runtime[ContextT],
    ) -> dict[str, Any] | None:
        messages = state.get("messages", [])
        last_ai_message = self._last_ai_message(messages)
        if last_ai_message is None or not last_ai_message.tool_calls:
            return None

        run_counts = dict(state.get("run_tool_call_count", {}))
        blocked_calls: list[ToolCall] = []
        tracked = False

        for tool_call in last_ai_message.tool_calls:
            tool_name = tool_call.get("name")
            if tool_name not in self._tool_names:
                continue
            tracked = True
            if run_counts.get(tool_name, 0) >= 1:
                blocked_calls.append(tool_call)
                continue
            run_counts[tool_name] = run_counts.get(tool_name, 0) + 1

        if not tracked:
            return None

        if not blocked_calls:
            return {"run_tool_call_count": run_counts}

        artificial_messages = [
            ToolMessage(
                content=f"Tool call limit exceeded. Do not call '{tool_call['name']}' again.",
                tool_call_id=tool_call["id"],
                name=tool_call.get("name"),
                status="error",
            )
            for tool_call in blocked_calls
        ]
        return {
            "run_tool_call_count": run_counts,
            "messages": artificial_messages,
        }

    @override
    async def aafter_model(
        self,
        state: DelegationOnceState,
        runtime: Runtime[ContextT],
    ) -> dict[str, Any] | None:
        return self.after_model(state, runtime)


ORPHANED_TOOL_MESSAGE = (
    "Tool execution did not complete before the turn ended; continuing the conversation."
)


def repair_orphaned_tool_calls(messages: Sequence[Any]) -> list[Any] | None:
    """Insert synthetic tool responses for unanswered assistant tool calls."""

    if not messages:
        return None

    repaired: list[Any] = []
    inserted = False
    index = 0

    while index < len(messages):
        message = messages[index]
        if isinstance(message, AIMessage) and message.tool_calls:
            repaired.append(message)
            expected_ids = {tool_call["id"] for tool_call in message.tool_calls}
            answered_ids: set[str] = set()
            index += 1

            while index < len(messages) and isinstance(messages[index], ToolMessage):
                tool_message = messages[index]
                repaired.append(tool_message)
                if tool_message.tool_call_id in expected_ids:
                    answered_ids.add(tool_message.tool_call_id)
                index += 1

            for tool_call in message.tool_calls:
                if tool_call["id"] in answered_ids:
                    continue
                repaired.append(
                    ToolMessage(
                        content=ORPHANED_TOOL_MESSAGE,
                        tool_call_id=tool_call["id"],
                        name=tool_call.get("name"),
                        status="error",
                    )
                )
                inserted = True
            continue

        repaired.append(message)
        index += 1

    if not inserted:
        return None
    return repaired


class RepairToolMessageHistoryMiddleware(AgentMiddleware):
    """Ensure checkpoint history never sends unanswered tool calls to the model."""

    trace_policy = QUIET_MIDDLEWARE_TRACE_POLICY

    @property
    def name(self) -> str:
        return "RepairToolMessageHistory"

    @override
    def before_model(
        self,
        state: AgentState,
        runtime: Runtime[ContextT],
    ) -> dict[str, Any] | None:
        repaired = repair_orphaned_tool_calls(state.get("messages", []))
        if repaired is None:
            return None
        return {
            "messages": [
                RemoveMessage(id=REMOVE_ALL_MESSAGES),
                *repaired,
            ]
        }

    @override
    async def abefore_model(
        self,
        state: AgentState,
        runtime: Runtime[ContextT],
    ) -> dict[str, Any] | None:
        return self.before_model(state, runtime)


def budget_middleware(*, max_tool_calls: int, max_model_calls: int) -> list[Any]:
    """Execution budgets with quieter LangSmith middleware spans."""

    return [
        RepairToolMessageHistoryMiddleware(),
        NamedToolTraceMiddleware(),
        QuietToolCallLimitMiddleware(run_limit=max_tool_calls, exit_behavior="continue"),
        QuietModelCallLimitMiddleware(run_limit=max_model_calls, exit_behavior="end"),
    ]
