from langchain_core.messages import AIMessage, HumanMessage, RemoveMessage, ToolMessage
from langgraph.graph.message import REMOVE_ALL_MESSAGES

from orysys_assistant.agent.middleware_limits import (
    DelegationOnceMiddleware,
    NamedToolTraceMiddleware,
    repair_orphaned_tool_calls,
)


def test_delegation_once_middleware_blocks_repeat_consultations() -> None:
    middleware = DelegationOnceMiddleware(["consult_knowledge_specialist"])
    state = {
        "messages": [
            HumanMessage(content="Question"),
            AIMessage(
                content="",
                tool_calls=[
                    {"id": "1", "name": "consult_knowledge_specialist", "args": {"question": "a"}},
                    {"id": "2", "name": "consult_knowledge_specialist", "args": {"question": "b"}},
                ],
            ),
        ],
        "run_tool_call_count": {},
    }

    update = middleware.after_model(state, runtime=None)

    assert update is not None
    assert update["run_tool_call_count"]["consult_knowledge_specialist"] == 1
    assert len(update["messages"]) == 1
    assert update["messages"][0].status == "error"


def test_named_tool_trace_middleware_sets_run_name() -> None:
    middleware = NamedToolTraceMiddleware()
    captured: dict[str, str] = {}

    class Runtime:
        config: dict[str, object] = {}

    class Request:
        tool_call = {"name": "knowledge_search", "args": {"query": "policy"}, "id": "1"}
        runtime = Runtime()

    async def handler(request: Request) -> str:
        captured["run_name"] = str(request.runtime.config.get("run_name"))
        captured["tags"] = list(request.runtime.config.get("tags") or [])
        return "ok"

    import asyncio

    asyncio.run(middleware.awrap_tool_call(Request(), handler))

    assert captured["run_name"] == "knowledge_search"
    assert "app-span" in captured["tags"]
    assert "tool" in captured["tags"]
    assert "knowledge_search" in captured["tags"]


def test_repair_orphaned_tool_calls_adds_missing_tool_messages() -> None:
    messages = [
        HumanMessage(content="Question"),
        AIMessage(
            content="",
            tool_calls=[
                {"id": "call-1", "name": "consult_knowledge_specialist", "args": {"question": "a"}},
                {"id": "call-2", "name": "consult_research_specialist", "args": {"question": "b"}},
            ],
        ),
    ]

    repaired = repair_orphaned_tool_calls(messages)

    assert repaired is not None
    assert len(repaired) == 4
    assert isinstance(repaired[2], ToolMessage)
    assert repaired[2].tool_call_id == "call-1"
    assert isinstance(repaired[3], ToolMessage)
    assert repaired[3].tool_call_id == "call-2"
    assert repaired[2].status == "error"
    assert repaired[3].status == "error"


def test_repair_orphaned_tool_calls_leaves_complete_history_unchanged() -> None:
    messages = [
        HumanMessage(content="Question"),
        AIMessage(
            content="",
            tool_calls=[
                {"id": "call-1", "name": "consult_knowledge_specialist", "args": {"question": "a"}},
            ],
        ),
        ToolMessage(content="done", tool_call_id="call-1", name="consult_knowledge_specialist"),
        AIMessage(content="Final answer"),
    ]

    assert repair_orphaned_tool_calls(messages) is None


def test_repair_tool_message_history_middleware_rewrites_state() -> None:
    from orysys_assistant.agent.middleware_limits import RepairToolMessageHistoryMiddleware

    middleware = RepairToolMessageHistoryMiddleware()
    state = {
        "messages": [
            HumanMessage(content="Question"),
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "id": "call-1",
                        "name": "consult_knowledge_specialist",
                        "args": {"question": "a"},
                    }
                ],
            ),
        ]
    }

    update = middleware.before_model(state, runtime=None)

    assert update is not None
    assert isinstance(update["messages"][0], RemoveMessage)
    assert update["messages"][0].id == REMOVE_ALL_MESSAGES
    assert isinstance(update["messages"][-1], ToolMessage)
    assert update["messages"][-1].tool_call_id == "call-1"
