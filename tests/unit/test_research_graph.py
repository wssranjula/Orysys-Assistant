import asyncio
import hashlib
from typing import Any, cast

import pytest
from pydantic import BaseModel

from orysys_assistant.agent.research_graph import ResearchLimits, ResearchWorkflow
from orysys_assistant.agent.toolbox import ScopedToolbox
from orysys_assistant.config import Settings
from orysys_assistant.domain.models import Role
from orysys_assistant.retrieval.models import Evidence
from orysys_assistant.security.access_scope import AccessScopeService
from orysys_assistant.security.authorization import AuthorizationPolicy, Capability
from orysys_assistant.security.models import TrustedRequestContext, UserIdentity
from orysys_assistant.tools.gateway import ToolGateway, ToolSpec
from orysys_assistant.tools.knowledge_search import KnowledgeSearchInput


def request_context() -> TrustedRequestContext:
    identity = UserIdentity(
        user_id="phase5-analyst",
        username="analyst@commercialbank.test",
        display_name="Phase 5 Analyst",
        role=Role.ANALYST,
        department="payments",
    )
    return TrustedRequestContext(
        identity=identity,
        access_scope=AccessScopeService(Settings(_env_file=None)).build(identity),
        rate_limit_remaining=10,
    )


def evidence_for(query: str) -> Evidence:
    identifier = hashlib.sha256(query.encode()).hexdigest()[:12]
    return Evidence(
        evidence_id=f"ev_{identifier}",
        document_id=f"doc_{identifier}",
        chunk_id=f"chunk_{identifier}",
        title=f"Incident {identifier}",
        content="The root cause was an exhausted connection pool. Service recovered safely.",
        metadata={
            "source_path": f"incidents/{identifier}.md",
            "document_type": "incident",
        },
        dense_score=0.8,
        sparse_score=0.7,
        final_score=0.76,
    )


def limits(**overrides: Any) -> ResearchLimits:
    values = {
        "max_initial_tasks": 4,
        "max_followup_tasks": 2,
        "max_recursion_depth": 2,
        "max_parallel_workers": 2,
        "max_total_tool_calls": 20,
        "max_chunks_per_worker": 6,
        "worker_timeout_seconds": 1,
        "overall_timeout_seconds": 5,
        **overrides,
    }
    return ResearchLimits(**values)


def workflow_with_handler(
    handler: Any, configured_limits: ResearchLimits, planner: Any = None
) -> ResearchWorkflow:
    gateway = ToolGateway(AuthorizationPolicy())
    gateway.register(
        ToolSpec(
            name="knowledge_search",
            capability=Capability.KNOWLEDGE_SEARCH,
            input_model=KnowledgeSearchInput,
            handler=handler,
        )
    )
    return ResearchWorkflow(
        ScopedToolbox(gateway, frozenset({"knowledge_search"})),
        configured_limits,
        planner=planner,
    )


@pytest.mark.asyncio
async def test_todo_planner_creates_claim_driven_tasks_instead_of_folder_fanout() -> None:
    planned = [
        "Trace which Project Orion controls were represented as complete before each failure.",
        "Reconcile the initial and final root-cause evidence for PAY-1224.",
        "Audit runtime evidence that reopened the connection-budget control after PAY-1288.",
        "Assess whether later actions closed the recovery and consumer-canary gaps.",
    ]

    class FakeTodoPlanner:
        def __init__(self) -> None:
            self.calls: list[dict[str, Any]] = []

        async def plan(self, question: str, **kwargs: Any) -> list[str]:
            self.calls.append({"question": question, **kwargs})
            return planned

    planner = FakeTodoPlanner()
    observed_queries: list[str] = []
    observed_document_types: list[str | None] = []

    async def handler(parameters: BaseModel, context: TrustedRequestContext) -> dict[str, Any]:
        request = cast(KnowledgeSearchInput, parameters)
        observed_queries.append(request.query)
        observed_document_types.append(request.document_type)
        return {"evidence": [evidence_for(request.query).model_dump(mode="json")]}

    workflow = workflow_with_handler(handler, limits(), planner)
    transitions = []

    async def capture(transition: Any) -> None:
        transitions.append(transition)

    execution = await workflow.run(
        "Investigate Project Orion across incidents, meeting notes, runbooks, and architecture.",
        request_context(),
        capture,
    )

    assert len(planner.calls) == 1
    assert set(observed_queries) == set(planned)
    assert observed_document_types == [None] * 4
    assert execution.result.partial is False
    planner_event = next(
        item for item in transitions if item.node == "planner" and item.status == "completed"
    )
    assert [todo["content"] for todo in planner_event.metadata["todos"]] == planned


@pytest.mark.asyncio
async def test_compiled_graph_bounds_concurrency_and_isolates_worker_failure() -> None:
    active = 0
    maximum_active = 0

    async def handler(parameters: BaseModel, context: TrustedRequestContext) -> dict[str, Any]:
        nonlocal active, maximum_active
        query = cast(KnowledgeSearchInput, parameters).query
        active += 1
        maximum_active = max(maximum_active, active)
        try:
            await asyncio.sleep(0.02)
            if "meeting note" in query:
                raise RuntimeError("simulated retrieval failure")
            return {"evidence": [evidence_for(query).model_dump(mode="json")]}
        finally:
            active -= 1

    workflow = workflow_with_handler(handler, limits(max_parallel_workers=2))
    transitions = []

    async def capture(transition: Any) -> None:
        transitions.append(transition)

    execution = await workflow.run(
        "Investigate recurring payment failures across multiple sources.",
        request_context(),
        capture,
    )

    assert type(workflow.graph).__name__ == "CompiledStateGraph"
    assert {
        "normalize_scope",
        "planner",
        "workers",
        "reducer",
        "coverage_check",
        "followup_planner",
        "finalize",
    } <= set(workflow.graph.nodes)
    assert maximum_active == 2
    assert execution.result.partial is False
    assert len(execution.evidence) >= 3
    assert len(set(execution.result.evidence_ids)) >= 3
    assert any("RuntimeError" in warning for warning in execution.result.warnings)
    assert any(item.node.startswith("worker:") for item in transitions)


@pytest.mark.asyncio
async def test_empty_coverage_recurses_once_then_returns_partial_result() -> None:
    calls = 0

    async def empty_handler(
        parameters: BaseModel, context: TrustedRequestContext
    ) -> dict[str, Any]:
        nonlocal calls
        calls += 1
        return {"evidence": []}

    workflow = workflow_with_handler(
        empty_handler,
        limits(
            max_initial_tasks=2,
            max_followup_tasks=1,
            max_recursion_depth=1,
            max_parallel_workers=1,
            max_total_tool_calls=3,
        ),
    )
    transitions = []

    async def capture(transition: Any) -> None:
        transitions.append(transition)

    execution = await workflow.run(
        "Investigate multiple payment failures.", request_context(), capture
    )

    followups = [
        item for item in transitions if item.node == "followup_planner" and item.status == "started"
    ]
    assert calls == 3
    assert len(followups) == 1
    assert execution.result.partial is True
    assert execution.result.unresolved_questions
    assert "3 bounded retrieval tasks" in execution.result.summary
    assert any("limits were reached" in warning for warning in execution.result.warnings)


@pytest.mark.asyncio
async def test_tool_call_budget_skips_excess_initial_tasks() -> None:
    calls = 0

    async def handler(parameters: BaseModel, context: TrustedRequestContext) -> dict[str, Any]:
        nonlocal calls
        calls += 1
        return {"evidence": [evidence_for("duplicate evidence").model_dump(mode="json")]}

    workflow = workflow_with_handler(
        handler,
        limits(max_initial_tasks=4, max_total_tool_calls=2, max_followup_tasks=2),
    )
    execution = await workflow.run(
        "Investigate recurring payment issues across multiple sources.", request_context()
    )

    assert calls == 2
    assert len(execution.evidence) == 1
    assert execution.result.partial is True
    assert any("tool-call budget" in warning for warning in execution.result.warnings)


@pytest.mark.asyncio
async def test_cancellation_propagates_through_worker_fanout() -> None:
    started = asyncio.Event()

    async def slow_handler(parameters: BaseModel, context: TrustedRequestContext) -> dict[str, Any]:
        started.set()
        await asyncio.sleep(10)
        return {"evidence": []}

    workflow = workflow_with_handler(slow_handler, limits(overall_timeout_seconds=30))
    task = asyncio.create_task(workflow.run("Investigate multiple incidents.", request_context()))
    await asyncio.wait_for(started.wait(), timeout=1)
    task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await task
