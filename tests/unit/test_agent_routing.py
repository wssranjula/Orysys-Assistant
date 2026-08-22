from pathlib import Path
from typing import Any

import pytest

import orysys_assistant.agent.build_agent as build_agent_module
from orysys_assistant.agent.build_agent import (
    AgentDependencies,
    build_deep_agent_graph,
    build_root_orchestrator,
)
from orysys_assistant.agent.models import AgentRoute
from orysys_assistant.agent.orchestrator import RootOrchestrator
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


def test_secure_deep_agent_harness_compiles_with_static_specialists(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    graph = build_deep_agent_graph(
        model="openai:gpt-5-mini",
        gateway=ToolGateway(AuthorizationPolicy()),
        context=context(Role.ADMINISTRATOR),
        project_root=Path(__file__).parents[2],
    )

    assert type(graph).__name__ == "CompiledStateGraph"
    assert {"model", "tools", "SkillsMiddleware.before_agent"} <= set(graph.nodes)


def test_deep_agent_specialists_receive_only_their_approved_tools(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        build_agent_module,
        "create_deep_agent",
        lambda **arguments: arguments,
    )
    built = build_deep_agent_graph(
        model="openai:gpt-5-mini",
        gateway=ToolGateway(AuthorizationPolicy()),
        context=context(Role.ADMINISTRATOR),
        project_root=Path(__file__).parents[2],
    )
    surfaces = {
        agent["name"]: {tool.name for tool in agent["tools"]}
        for agent in built["subagents"]
    }

    assert surfaces["research"] == {"knowledge_search"}
    assert surfaces["analysis"] == {"knowledge_search", "structured_analysis"}
    assert surfaces["enterprise-tools"] == {
        "get_employee",
        "search_employees",
        "get_service",
        "search_services",
        "get_incident",
        "search_incidents",
    }
