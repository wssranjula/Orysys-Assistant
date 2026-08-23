"""Behaviour of the autonomous research specialist and its enforced boundaries."""

import asyncio
import hashlib
from typing import Any, cast

import pytest
from conftest import request_context, scripted_model, text_turn, tool_turn
from pydantic import BaseModel

from orysys_assistant.agent.research_agent import ResearchLimits, ResearchSubagent
from orysys_assistant.agent.toolbox import ScopedToolbox
from orysys_assistant.retrieval.models import Evidence
from orysys_assistant.security.authorization import AuthorizationPolicy, Capability
from orysys_assistant.security.models import TrustedRequestContext
from orysys_assistant.tools.gateway import ToolGateway, ToolSpec
from orysys_assistant.tools.knowledge_search import KnowledgeSearchInput


def evidence_for(query: str, document_type: str = "incident") -> Evidence:
    identifier = hashlib.sha256(query.encode()).hexdigest()[:12]
    return Evidence(
        evidence_id=f"ev_{identifier}",
        document_id=f"doc_{identifier}",
        chunk_id=f"chunk_{identifier}",
        title=f"Incident {identifier}",
        content=(
            f"{query} The root cause was an exhausted connection pool. Service recovered safely."
        ),
        metadata={"source_path": f"incidents/{identifier}.md", "document_type": document_type},
        dense_score=0.8,
        sparse_score=0.7,
        final_score=0.76,
    )


def limits(**overrides: Any) -> ResearchLimits:
    values: dict[str, Any] = {
        "max_tool_calls": 20,
        "max_model_calls": 8,
        "max_chunks_per_search": 6,
        "overall_timeout_seconds": 5,
        **overrides,
    }
    return ResearchLimits(**values)


def research_agent(
    handler: Any, model: Any, configured_limits: ResearchLimits | None = None
) -> ResearchSubagent:
    gateway = ToolGateway(AuthorizationPolicy())
    gateway.register(
        ToolSpec(
            name="knowledge_search",
            capability=Capability.KNOWLEDGE_SEARCH,
            input_model=KnowledgeSearchInput,
            handler=handler,
        )
    )
    return ResearchSubagent(
        ScopedToolbox(gateway, frozenset({"knowledge_search"})),
        configured_limits or limits(),
        model,
    )


async def evidence_handler(parameters: BaseModel, context: TrustedRequestContext) -> dict[str, Any]:
    request = cast(KnowledgeSearchInput, parameters)
    return {
        "evidence": [
            evidence_for(request.query, request.document_type or "incident").model_dump(mode="json")
        ]
    }


@pytest.mark.asyncio
async def test_plan_and_parallel_searches_reach_the_activity_stream() -> None:
    observed: list[tuple[str, str | None]] = []

    async def handler(parameters: BaseModel, context: TrustedRequestContext) -> dict[str, Any]:
        request = cast(KnowledgeSearchInput, parameters)
        observed.append((request.query, request.document_type))
        return await evidence_handler(parameters, context)

    model = scripted_model(
        tool_turn(
            (
                "write_todos",
                {
                    "todos": [
                        {"content": "Trace Orion completion claims", "status": "in_progress"},
                        {"content": "Reconcile PAY-1224 root cause", "status": "pending"},
                    ]
                },
            )
        ),
        tool_turn(
            (
                "knowledge_search",
                {"query": "Orion controls complete", "document_type": "meeting_note"},
            ),
            ("knowledge_search", {"query": "PAY-1224 root cause", "document_type": "incident"}),
        ),
        text_turn(
            "SUMMARY: Orion controls were reported complete but later regressed.\n"
            "FINDING: Connection pool exhaustion recurred. || "
            f"{evidence_for('PAY-1224 root cause').evidence_id}\n"
            "UNRESOLVED: none"
        ),
    )
    transitions: list[Any] = []

    async def capture(transition: Any) -> None:
        transitions.append(transition)

    execution = await research_agent(handler, model).run(
        "Investigate Project Orion across incidents and meeting notes.",
        request_context(),
        capture,
    )

    assert {item[1] for item in observed} == {"meeting_note", "incident"}
    plan_event = next(item for item in transitions if item.node == "planner")
    assert [todo["content"] for todo in plan_event.metadata["todos"]] == [
        "Trace Orion completion claims",
        "Reconcile PAY-1224 root cause",
    ]
    assert len(execution.evidence) == 2
    assert execution.grounded is True
    assert "Connection pool exhaustion recurred." in execution.report
    assert not any("unresolved" in warning.lower() for warning in execution.warnings)


@pytest.mark.asyncio
async def test_findings_citing_unretrieved_evidence_are_dropped() -> None:
    model = scripted_model(
        tool_turn(("knowledge_search", {"query": "payment outage"})),
        text_turn(
            "SUMMARY: Payment outages recurred.\n"
            "FINDING: Grounded claim. || "
            f"{evidence_for('payment outage').evidence_id}\n"
            "FINDING: Invented claim. || ev_never_retrieved\n"
            "UNRESOLVED: none"
        ),
    )

    execution = await research_agent(evidence_handler, model).run(
        "Investigate payment outages.", request_context()
    )

    # The invented reference cannot resolve to a citation later, so the claim it carries
    # never reaches the root agent that writes the answer.
    assert "Grounded claim." in execution.report
    assert "Invented claim." not in execution.report
    assert "ev_never_retrieved" not in execution.report


@pytest.mark.asyncio
async def test_retrieval_failure_is_isolated_and_reported_as_a_warning() -> None:
    async def failing_handler(
        parameters: BaseModel, context: TrustedRequestContext
    ) -> dict[str, Any]:
        request = cast(KnowledgeSearchInput, parameters)
        if "meeting" in request.query:
            raise RuntimeError("simulated retrieval failure")
        return await evidence_handler(parameters, context)

    model = scripted_model(
        tool_turn(
            ("knowledge_search", {"query": "meeting notes for Orion"}),
            ("knowledge_search", {"query": "incident reports for Orion"}),
        ),
        text_turn(
            "SUMMARY: Only incident evidence was available.\n"
            "FINDING: Incident evidence exists. || "
            f"{evidence_for('incident reports for Orion').evidence_id}\n"
            "UNRESOLVED: Meeting note coverage is missing."
        ),
    )

    execution = await research_agent(failing_handler, model).run(
        "Investigate Project Orion.", request_context()
    )

    # One failing retrieval must not lose the sibling result or end the turn.
    assert len(execution.evidence) == 1
    assert "Unresolved: Meeting note coverage is missing." in execution.report
    assert any("unresolved" in warning.lower() for warning in execution.warnings)
    assert any("unavailable" in warning for warning in execution.warnings)


@pytest.mark.asyncio
async def test_tool_call_budget_is_enforced_by_middleware() -> None:
    calls = 0

    async def counting_handler(
        parameters: BaseModel, context: TrustedRequestContext
    ) -> dict[str, Any]:
        nonlocal calls
        calls += 1
        return await evidence_handler(parameters, context)

    # The script keeps asking for more retrieval than the budget allows.
    model = scripted_model(
        tool_turn(
            ("knowledge_search", {"query": "first"}),
            ("knowledge_search", {"query": "second"}),
            ("knowledge_search", {"query": "third"}),
            ("knowledge_search", {"query": "fourth"}),
        ),
        text_turn("SUMMARY: Budget reached.\nUNRESOLVED: Remaining sources were not searched."),
    )

    execution = await research_agent(
        counting_handler, model, limits(max_tool_calls=2, max_model_calls=3)
    ).run("Investigate everything.", request_context())

    assert calls <= 2
    # No finding survived the budget cut, so the consultation reports itself as ungrounded
    # rather than presenting a truncated search as a settled answer.
    assert execution.grounded is False
    assert any("unresolved" in warning.lower() for warning in execution.warnings)


@pytest.mark.asyncio
async def test_overall_timeout_returns_evidence_collected_before_the_deadline() -> None:
    async def slow_handler(parameters: BaseModel, context: TrustedRequestContext) -> dict[str, Any]:
        request = cast(KnowledgeSearchInput, parameters)
        if request.query == "slow":
            await asyncio.sleep(10)
        return await evidence_handler(parameters, context)

    model = scripted_model(
        tool_turn(("knowledge_search", {"query": "fast"})),
        tool_turn(("knowledge_search", {"query": "slow"})),
        text_turn("SUMMARY: never reached"),
    )

    execution = await research_agent(slow_handler, model, limits(overall_timeout_seconds=0.5)).run(
        "Investigate payment outages.", request_context()
    )

    assert execution.grounded is False
    assert any("timeout" in warning for warning in execution.warnings)
    # Work completed before the deadline is still authorized evidence.
    assert len(execution.evidence) == 1


@pytest.mark.asyncio
async def test_cancellation_propagates_through_the_research_agent() -> None:
    started = asyncio.Event()

    async def blocking_handler(
        parameters: BaseModel, context: TrustedRequestContext
    ) -> dict[str, Any]:
        started.set()
        await asyncio.sleep(10)
        return {"evidence": []}

    model = scripted_model(
        tool_turn(("knowledge_search", {"query": "anything"})),
        text_turn("SUMMARY: never reached"),
    )
    agent = research_agent(blocking_handler, model, limits(overall_timeout_seconds=30))
    task = asyncio.create_task(agent.run("Investigate incidents.", request_context()))
    await asyncio.wait_for(started.wait(), timeout=2)
    task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await task
