from langchain_core.messages import AIMessage, HumanMessage

from orysys_assistant.agent.middleware_limits import (
    DelegationOnceMiddleware,
    NamedToolTraceMiddleware,
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
