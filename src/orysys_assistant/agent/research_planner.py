"""Harness todo-backed planning for bounded document research."""

from collections.abc import Sequence
from typing import Any, Protocol

from langchain.agents import create_agent
from langchain.agents.middleware import TodoListMiddleware
from langsmith import traceable

PLANNER_SYSTEM_PROMPT = """You are a research-planning agent, not an answering agent.
For every request, call write_todos exactly once and create the requested number of research tasks.
Decompose the objective by claims, causal questions, time periods, dependencies, contradictions,
or requirements that must be verified. Do not create one generic task per document folder or type.
The authorized corpus contains only these document types: incident, meeting_note, runbook,
architecture, policy, and product_specification. Metadata supports department, document type,
created-after date, and created-before date. Do not request Jira, PagerDuty, Slack, email,
Confluence, screenshots, raw logs, or external telemetry unless the research objective explicitly
says those records are in the corpus.

Every todo must use exactly this compact format:
SEARCH: <concise retrieval query, at most 180 characters> | SOURCE: <one corpus document type>
| VERIFY: <the claim, contradiction, timeline, or requirement this evidence must establish>

Use literal incident/action identifiers and dates when the objective provides them. Keep SEARCH
focused on corpus terminology; do not put deliverable instructions into it. Split a claim across
source types only when each source establishes a distinct part of the claim. Tasks may run in
parallel when independent. Do not answer the research question and do not call any tool other than
write_todos."""


class ResearchPlanner(Protocol):
    async def plan(
        self,
        question: str,
        *,
        max_tasks: int,
        completed_tasks: Sequence[str] = (),
        evidence_titles: Sequence[str] = (),
    ) -> list[str]: ...


class TodoResearchPlanner:
    """Turn harness todo state into bounded research-worker questions."""

    def __init__(self, agent: Any) -> None:
        self._agent = agent

    @traceable(
        name="research-todo-planner",
        run_type="chain",
        metadata={"agent": "research_planner", "operation": "write_todos"},
    )
    async def plan(
        self,
        question: str,
        *,
        max_tasks: int,
        completed_tasks: Sequence[str] = (),
        evidence_titles: Sequence[str] = (),
    ) -> list[str]:
        prior = "\n".join(f"- {item[:300]}" for item in completed_tasks[:8]) or "- None"
        evidence = "\n".join(f"- {item[:200]}" for item in evidence_titles[:12]) or "- None"
        minimum_tasks = 1 if completed_tasks else min(2, max_tasks)
        prompt = (
            f"Research objective:\n{question}\n\n"
            f"Create between {minimum_tasks} and {max_tasks} bounded research todos.\n\n"
            f"Already completed tasks:\n{prior}\n\n"
            f"Evidence titles already collected:\n{evidence}\n\n"
            "If prior work is listed, create only targeted gap-closing follow-up todos. "
            "Call write_todos once, then state that the plan is ready."
        )
        result = await self._agent.ainvoke({"messages": [{"role": "user", "content": prompt}]})
        raw_todos = result.get("todos") if isinstance(result, dict) else None
        if not isinstance(raw_todos, list):
            raise ValueError("Research planner did not create harness todos.")

        tasks: list[str] = []
        seen: set[str] = set()
        for item in raw_todos:
            if not isinstance(item, dict):
                continue
            content = " ".join(str(item.get("content", "")).split())
            normalized = content.casefold()
            if len(content) < 10 or normalized in seen:
                continue
            tasks.append(content[:1_000])
            seen.add(normalized)
            if len(tasks) == max_tasks:
                break
        if not tasks:
            raise ValueError("Research planner created no valid harness todos.")
        return tasks


def build_todo_research_planner(model: Any) -> TodoResearchPlanner:
    """Create a planner whose only tool is the harness-managed write_todos tool."""
    middleware = TodoListMiddleware(system_prompt="")
    agent = create_agent(
        model=model,
        tools=[],
        middleware=[middleware],
        system_prompt=PLANNER_SYSTEM_PROMPT,
        name="research-todo-planner",
    )
    return TodoResearchPlanner(agent)
