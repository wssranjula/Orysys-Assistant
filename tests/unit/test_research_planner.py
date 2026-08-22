from typing import Any

import pytest
from langchain.agents.middleware import TodoListMiddleware

import orysys_assistant.agent.research_planner as planner_module
from orysys_assistant.agent.research_planner import (
    PLANNER_SYSTEM_PROMPT,
    TodoResearchPlanner,
    build_todo_research_planner,
)


@pytest.mark.asyncio
async def test_todo_research_planner_extracts_deduplicates_and_bounds_harness_todos() -> None:
    class FakeAgent:
        async def ainvoke(self, request: dict[str, Any]) -> dict[str, Any]:
            return {
                "todos": [
                    {
                        "content": "  Trace the PAY-1224 root-cause timeline.  ",
                        "status": "in_progress",
                    },
                    {"content": "Trace the PAY-1224 root-cause timeline.", "status": "pending"},
                    {
                        "content": "Audit the PAY-1288 connection-budget runtime evidence.",
                        "status": "pending",
                    },
                    {"content": "Check OR-44 and OR-51 closure evidence.", "status": "pending"},
                ]
            }

    planner = TodoResearchPlanner(FakeAgent())

    tasks = await planner.plan("Investigate Project Orion.", max_tasks=2)

    assert tasks == [
        "Trace the PAY-1224 root-cause timeline.",
        "Audit the PAY-1288 connection-budget runtime evidence.",
    ]


def test_todo_research_planner_factory_exposes_only_write_todos(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}

    def fake_create_agent(**kwargs: Any) -> object:
        captured.update(kwargs)
        return object()

    monkeypatch.setattr(planner_module, "create_agent", fake_create_agent)

    build_todo_research_planner(object())

    assert captured["tools"] == []
    assert captured["system_prompt"] == PLANNER_SYSTEM_PROMPT
    assert len(captured["middleware"]) == 1
    middleware = captured["middleware"][0]
    assert isinstance(middleware, TodoListMiddleware)
    assert [tool.name for tool in middleware.tools] == ["write_todos"]
