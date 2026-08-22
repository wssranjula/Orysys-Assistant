from pathlib import Path
from typing import Any, cast

import pytest
from pydantic import BaseModel, ConfigDict, Field

from orysys_assistant.agent.build_agent import (
    AgentDependencies,
    build_deep_agent_graph,
    build_root_orchestrator,
)
from orysys_assistant.agent.models import AgentRoute
from orysys_assistant.agent.toolbox import ScopedToolbox
from orysys_assistant.config import Settings
from orysys_assistant.domain.errors import AuthorizationError
from orysys_assistant.domain.models import Role
from orysys_assistant.retrieval.runtime import build_retrieval_runtime
from orysys_assistant.security.access_scope import AccessScopeService
from orysys_assistant.security.authorization import AuthorizationPolicy, Capability
from orysys_assistant.security.models import TrustedRequestContext, UserIdentity
from orysys_assistant.tools.gateway import ToolGateway, ToolSpec
from orysys_assistant.tools.knowledge_search import knowledge_search_spec


class QueryInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    query: str = Field(min_length=1)


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


async def build_orchestrator(project_root: Path) -> tuple[Any, Any]:
    runtime = await build_retrieval_runtime(
        Settings(retrieval_backend="memory", _env_file=None), project_root
    )
    gateway = ToolGateway(AuthorizationPolicy())
    gateway.register(knowledge_search_spec(runtime.service))

    async def enterprise_handler(
        parameters: BaseModel, request_context: TrustedRequestContext
    ) -> dict[str, Any]:
        query = cast(QueryInput, parameters).query
        return {"query": query, "owner": "Payments Reliability", "authorized": True}

    for name in (
        "employee_directory.lookup",
        "service_catalog.search",
        "incident_records.search",
    ):
        gateway.register(
            ToolSpec(
                name=name,
                capability=Capability.MCP_READ,
                input_model=QueryInput,
                handler=enterprise_handler,
            )
        )
    return build_root_orchestrator(AgentDependencies(gateway)), runtime


@pytest.mark.asyncio
async def test_root_routes_simple_and_complex_requests_with_structured_outputs() -> None:
    project_root = Path(__file__).parents[2]
    orchestrator, runtime = await build_orchestrator(project_root)
    analyst = context(Role.ANALYST)
    transitions = []

    async def capture(transition: Any) -> None:
        transitions.append(transition)

    try:
        direct = await orchestrator.run(
            "What is the remote working policy?", analyst, capture
        )
        research = await orchestrator.run(
            "Investigate payment outages across the last year.", analyst
        )
        analysis = await orchestrator.run(
            "Count payment incidents by root cause.", analyst
        )
        enterprise = await orchestrator.run(
            "Who owns the payment service?", analyst
        )
    finally:
        await runtime.close()

    assert direct.route is AgentRoute.DIRECT_KNOWLEDGE
    assert direct.citations and direct.evidence_ids
    assert research.route is AgentRoute.RESEARCH
    assert research.evidence_ids
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
            "employee_directory.lookup",
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
