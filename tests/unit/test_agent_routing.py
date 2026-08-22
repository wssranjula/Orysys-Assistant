from pathlib import Path
from typing import Any

import pytest

from orysys_assistant.agent.build_agent import (
    AgentDependencies,
    build_root_orchestrator,
)
from orysys_assistant.agent.models import AgentExecutionResult, AgentRoute, AgentTransition
from orysys_assistant.agent.orchestrator import RootOrchestrator
from orysys_assistant.agent.router import IntentRouter
from orysys_assistant.agent.toolbox import ScopedToolbox
from orysys_assistant.config import Settings
from orysys_assistant.domain.errors import AuthorizationError
from orysys_assistant.domain.models import Role
from orysys_assistant.retrieval.runtime import build_retrieval_runtime
from orysys_assistant.security.access_scope import AccessScopeService
from orysys_assistant.security.authorization import AuthorizationPolicy
from orysys_assistant.security.models import TrustedRequestContext, UserIdentity
from orysys_assistant.tools.enterprise import enterprise_tool_specs
from orysys_assistant.tools.gateway import ToolGateway
from orysys_assistant.tools.knowledge_search import (
    KNOWLEDGE_QUERY_MAX_LENGTH,
    KnowledgeSearchInput,
    knowledge_search_spec,
)
from orysys_assistant.tools.mcp_client import InMemoryEnterpriseClient
from orysys_assistant.tools.python_analysis import python_analysis_spec


def context(role: Role) -> TrustedRequestContext:
    identity = UserIdentity(
        user_id=f"phase4-{role.value}",
        username=f"{role.value}@commercialbank.test",
        display_name="Phase 4 User",
        role=role,
        department="payments",
    )
    return TrustedRequestContext(
        identity=identity,
        access_scope=AccessScopeService(Settings(_env_file=None)).build(identity),
        rate_limit_remaining=10,
    )


def test_conversation_context_fits_the_knowledge_search_contract() -> None:
    question = "Were there any incidents related to attachments?"
    summary = "older context " * 1_000 + "most recent attachment discussion"

    query = RootOrchestrator._with_conversation_context(question, summary)

    assert len(query) == KNOWLEDGE_QUERY_MAX_LENGTH
    assert query.startswith(question)
    assert query.endswith("most recent attachment discussion")
    assert KnowledgeSearchInput(query=query).query == query


def test_long_current_question_is_bounded_without_conversation_context() -> None:
    query = RootOrchestrator._with_conversation_context("q" * 8_000, "")

    assert len(query) == KNOWLEDGE_QUERY_MAX_LENGTH
    assert KnowledgeSearchInput(query=query).query == query


@pytest.mark.parametrize("question", ["Show EMP-001", "Look up INC-2025-001"])
def test_enterprise_identifiers_route_without_keyword_hints(question: str) -> None:
    assert IntentRouter().route(question) is AgentRoute.ENTERPRISE


async def build_orchestrator(project_root: Path) -> tuple[Any, Any]:
    runtime = await build_retrieval_runtime(
        Settings(retrieval_backend="memory", _env_file=None), project_root
    )
    gateway = ToolGateway(AuthorizationPolicy())
    gateway.register(knowledge_search_spec(runtime.service))
    gateway.register(python_analysis_spec(1_000))
    for spec in enterprise_tool_specs(InMemoryEnterpriseClient(), 1, 100_000):
        gateway.register(spec)
    return build_root_orchestrator(AgentDependencies(gateway)), runtime


@pytest.mark.asyncio
async def test_root_routes_simple_and_complex_requests_with_structured_outputs() -> None:
    project_root = Path(__file__).parents[2]
    orchestrator, runtime = await build_orchestrator(project_root)
    analyst = context(Role.ANALYST)
    transitions = []
    research_transitions = []

    async def capture(transition: Any) -> None:
        transitions.append(transition)

    async def capture_research(transition: Any) -> None:
        research_transitions.append(transition)

    try:
        direct = await orchestrator.run("What is the remote working policy?", analyst, capture)
        research = await orchestrator.run(
            "Investigate payment outages across the last year.", analyst, capture_research
        )
        analysis = await orchestrator.run("Count payment incidents by root cause.", analyst)
        enterprise = await orchestrator.run("Who owns the payment service?", analyst)
    finally:
        await runtime.close()

    assert direct.route is AgentRoute.DIRECT_KNOWLEDGE
    assert direct.citations and direct.evidence_ids
    assert research.route is AgentRoute.RESEARCH
    assert research.evidence_ids
    assert {item.node for item in research_transitions} >= {
        "planner",
        "workers",
        "reducer",
        "coverage_check",
    }
    assert analysis.route is AgentRoute.ANALYSIS
    assert "Processed" in analysis.answer
    assert enterprise.route is AgentRoute.ENTERPRISE
    assert "Payments Reliability" in enterprise.answer
    assert [transition.event_type for transition in transitions] == [
        "agent_started",
        "routing_completed",
        "retrieval_started",
        "retrieval_completed",
    ]


@pytest.mark.asyncio
async def test_production_graph_streams_native_activity_and_one_result() -> None:
    orchestrator, runtime = await build_orchestrator(Path(__file__).parents[2])
    try:
        updates = [
            update
            async for update in orchestrator.stream(
                "Investigate recurring payment incidents across sources.",
                context(Role.ANALYST),
            )
        ]
    finally:
        await runtime.close()

    transitions = [item for item in updates if isinstance(item, AgentTransition)]
    results = [item for item in updates if isinstance(item, AgentExecutionResult)]
    assert len(results) == 1
    assert results[0].route is AgentRoute.RESEARCH
    assert {item.node for item in transitions} >= {
        "intent_routing",
        "planner",
        "workers",
        "reducer",
        "coverage_check",
    }


@pytest.mark.asyncio
async def test_native_stream_relays_specialist_tool_activity() -> None:
    orchestrator, runtime = await build_orchestrator(Path(__file__).parents[2])
    try:
        updates = [
            update
            async for update in orchestrator.stream(
                "Who owns the payment service?", context(Role.ANALYST)
            )
        ]
    finally:
        await runtime.close()

    transitions = [item for item in updates if isinstance(item, AgentTransition)]
    assert any(item.event_type == "tool_completed" for item in transitions)


@pytest.mark.asyncio
async def test_enterprise_route_enforces_rbac_before_handler() -> None:
    orchestrator, runtime = await build_orchestrator(Path(__file__).parents[2])
    try:
        with pytest.raises(AuthorizationError):
            await orchestrator.run("Who owns the payment service?", context(Role.VIEWER))
    finally:
        await runtime.close()


@pytest.mark.asyncio
async def test_agent_tool_surface_denies_unapproved_tool_before_gateway() -> None:
    gateway = ToolGateway(AuthorizationPolicy())
    research_tools = ScopedToolbox(gateway, frozenset({"knowledge_search"}))

    with pytest.raises(AuthorizationError):
        await research_tools.execute(
            "search_employees",
            {"query": "someone"},
            context(Role.ADMINISTRATOR),
        )


def test_production_orchestrator_is_a_compiled_langgraph() -> None:
    gateway = ToolGateway(AuthorizationPolicy())
    orchestrator = build_root_orchestrator(AgentDependencies(gateway))

    assert type(orchestrator.graph).__name__ == "CompiledStateGraph"
    assert {
        "route",
        "direct_knowledge",
        "research",
        "analysis",
        "enterprise",
    } <= set(orchestrator.graph.nodes)
