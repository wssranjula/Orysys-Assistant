import asyncio
import warnings
from typing import Any
from uuid import uuid4

import pytest
from conftest import scripted_model, text_turn, tool_turn
from pydantic import BaseModel

from orysys_assistant.agent.subagents import EnterpriseToolSubagent
from orysys_assistant.agent.toolbox import ScopedToolbox
from orysys_assistant.config import Settings
from orysys_assistant.domain.errors import AuthorizationError, InvalidRequestError
from orysys_assistant.domain.models import Role
from orysys_assistant.memory.repository import InMemoryConversationRepository
from orysys_assistant.memory.runtime import MemoryRuntime
from orysys_assistant.security.access_scope import AccessScopeService
from orysys_assistant.security.authorization import AuthorizationPolicy, Capability
from orysys_assistant.security.models import TrustedRequestContext, UserIdentity
from orysys_assistant.tools.enterprise import SearchServicesInput, enterprise_tool_specs
from orysys_assistant.tools.gateway import ToolGateway, ToolSpec
from orysys_assistant.tools.mcp_client import InMemoryEnterpriseClient
from orysys_assistant.tools.python_analysis import python_analysis_spec


def context(role: Role) -> TrustedRequestContext:
    identity = UserIdentity(
        user_id=f"phase6-{role.value}",
        username=f"{role.value}@commercialbank.test",
        display_name="Phase 6 User",
        role=role,
        department="payments",
    )
    return TrustedRequestContext(
        identity=identity,
        access_scope=AccessScopeService(Settings(_env_file=None)).build(identity),
        rate_limit_remaining=10,
    )


@pytest.mark.asyncio
async def test_memory_is_owner_isolated_and_stores_only_compact_turn_data() -> None:
    repository = InMemoryConversationRepository(max_messages=4, max_summary_characters=120)
    conversation_id = uuid4()
    await repository.get_or_create(conversation_id, "owner-1")
    record = await repository.append_turn(
        conversation_id,
        "owner-1",
        "What happened?",
        "A bounded answer.",
        ["ev_1", "ev_1", "ev_2"],
    )

    assert [message.role for message in record.messages] == ["user", "assistant"]
    assert record.evidence_ids == ["ev_1", "ev_2"]
    assert len(record.summary) <= 120
    assert "token" not in record.model_dump_json().lower()
    with pytest.raises(AuthorizationError):
        await repository.get(conversation_id, "owner-2")


@pytest.mark.asyncio
async def test_memory_runtime_exposes_langgraph_checkpointer() -> None:
    runtime = MemoryRuntime(Settings(memory_backend="memory", _env_file=None))
    await runtime.start()
    try:
        assert runtime.checkpointer is not None
        assert runtime.repository.persistence_name == "in_memory"
    finally:
        await runtime.close()


@pytest.mark.asyncio
async def test_controlled_analysis_operations_and_rbac() -> None:
    gateway = ToolGateway(AuthorizationPolicy())
    gateway.register(python_analysis_spec(10))
    parameters = {
        "operation": "percentage",
        "records": [{"cause": "queue"}, {"cause": "queue"}, {"cause": "database"}],
        "field": "cause",
    }

    result = await gateway.execute("structured_analysis", parameters, context(Role.ANALYST))

    assert result["rows_processed"] == 3
    assert result["results"][0] == {"value": "queue", "count": 2, "percentage": 66.67}
    with pytest.raises(AuthorizationError):
        await gateway.execute("structured_analysis", parameters, context(Role.VIEWER))
    with pytest.raises(InvalidRequestError):
        await gateway.execute(
            "structured_analysis",
            {**parameters, "operation": "execute_python"},
            context(Role.ADMINISTRATOR),
        )


@pytest.mark.asyncio
async def test_six_read_only_mcp_tools_are_typed_and_role_gated() -> None:
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        from mcp_server.server import mcp

    server_tools = await mcp.list_tools()
    assert {tool.name for tool in server_tools} == {
        "get_employee",
        "search_employees",
        "get_service",
        "search_services",
        "get_incident",
        "search_incidents",
    }
    gateway = ToolGateway(AuthorizationPolicy())
    for spec in enterprise_tool_specs(InMemoryEnterpriseClient(), 1, 100_000):
        gateway.register(spec)

    result = await gateway.execute("search_services", {"query": "payment"}, context(Role.ANALYST))

    assert result["services"][0]["owner"] == "Payments Reliability"
    with pytest.raises(AuthorizationError):
        await gateway.execute("search_services", {"query": "payment"}, context(Role.VIEWER))


@pytest.mark.asyncio
async def test_mcp_timeout_returns_degraded_result_and_activity() -> None:
    async def slow_handler(
        parameters: BaseModel, request_context: TrustedRequestContext
    ) -> dict[str, Any]:
        await asyncio.sleep(1)
        return {"services": []}

    gateway = ToolGateway(AuthorizationPolicy())
    gateway.register(
        ToolSpec(
            name="search_services",
            capability=Capability.MCP_READ,
            input_model=SearchServicesInput,
            handler=slow_handler,
            timeout_seconds=0.01,
        )
    )
    subagent = EnterpriseToolSubagent(
        ScopedToolbox(gateway, frozenset({"search_services"})),
        scripted_model(
            tool_turn(("search_services", {"query": "payment"})),
            text_turn("The service catalog could not be reached."),
        ),
    )
    transitions = []

    async def capture(transition: Any) -> None:
        transitions.append(transition)

    execution = await subagent.run("Who owns the payment service?", context(Role.ANALYST), capture)

    # The timeout is reported to the model as an ordinary degraded result, so the loop
    # finishes and says the system was unreachable instead of raising through the API.
    assert execution.grounded is False
    assert any("ToolTimeoutError" in warning for warning in execution.warnings)
    assert [item.status for item in transitions] == ["started", "degraded"]
    assert "could not be reached" in execution.report
