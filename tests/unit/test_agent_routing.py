"""Root delegation: which specialist runs, what it may reach, and what the turn reports.

The root is a model-driven loop now, so routing is exercised by scripting the delegation
tools it chooses rather than by stubbing a classifier. One script drives the whole
request: the root's turns and the specialist's turns are replayed in execution order,
while the real gateway, the real budgets, and the real specialists run underneath.
"""

from pathlib import Path
from typing import Any

import pytest
from conftest import scripted_model, text_turn, tool_turn

from orysys_assistant.agent.build_agent import (
    AgentDependencies,
    build_root_orchestrator,
)
from orysys_assistant.agent.models import (
    AgentExecutionResult,
    AgentRoute,
    AgentTransition,
    AnswerToken,
)
from orysys_assistant.agent.orchestrator import (
    ROOT_QUESTION_MAX_CHARACTERS,
    RootOrchestrator,
    _with_conversation_context,
)
from orysys_assistant.agent.toolbox import ScopedToolbox
from orysys_assistant.config import Settings
from orysys_assistant.domain.errors import AuthorizationError, InvalidRequestError
from orysys_assistant.domain.models import ResponseStatus, Role
from orysys_assistant.guardrails.output import OutputValidator
from orysys_assistant.retrieval.models import Evidence
from orysys_assistant.retrieval.runtime import build_retrieval_runtime
from orysys_assistant.security.access_scope import AccessScopeService
from orysys_assistant.security.authorization import AuthorizationPolicy, Capability
from orysys_assistant.security.models import TrustedRequestContext, UserIdentity
from orysys_assistant.tools.enterprise import SearchServicesInput, enterprise_tool_specs
from orysys_assistant.tools.gateway import ToolGateway, ToolSpec
from orysys_assistant.tools.knowledge_search import KnowledgeSearchInput, knowledge_search_spec
from orysys_assistant.tools.mcp_client import InMemoryEnterpriseClient
from orysys_assistant.tools.python_analysis import python_analysis_spec

KNOWLEDGE = "consult_knowledge_specialist"
RESEARCH = "consult_research_specialist"
ANALYSIS = "consult_analysis_specialist"
ENTERPRISE = "consult_enterprise_specialist"


def settings() -> Settings:
    return Settings(openai_api_key=None, _env_file=None)


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


def delegate(tool_name: str, request: str = "objective") -> Any:
    return tool_turn((tool_name, {"request": request}))


async def capture_into(events: list[Any]) -> Any:
    async def capture(transition: Any) -> None:
        events.append(transition)

    return capture


def root_tool_names(orchestrator: RootOrchestrator) -> set[str]:
    node = orchestrator.graph.nodes["tools"]
    runnable = getattr(node, "bound", None) or getattr(node, "runnable", None)
    return set(getattr(runnable, "tools_by_name", {}))


def test_conversation_context_fits_the_root_question_contract() -> None:
    question = "Were there any incidents related to attachments?"
    summary = "older context " * 1_000 + "most recent attachment discussion"

    prompt = _with_conversation_context(question, summary)

    assert len(prompt) == ROOT_QUESTION_MAX_CHARACTERS
    assert prompt.startswith(question)
    assert prompt.endswith("most recent attachment discussion")


def test_long_current_question_is_bounded_without_conversation_context() -> None:
    prompt = _with_conversation_context("q" * 20_000, "")

    assert len(prompt) == ROOT_QUESTION_MAX_CHARACTERS


def test_factory_refuses_to_build_without_a_chat_model() -> None:
    """Every loop is model-driven now, so a credential is not optional.

    Failing at construction is the honest outcome: a silent keyword-matching fallback
    would answer with a different system than the one the deployment is configured for.
    """

    with pytest.raises(InvalidRequestError) as failure:
        build_root_orchestrator(
            AgentDependencies(ToolGateway(AuthorizationPolicy()), settings=settings())
        )

    assert "chat model" in str(failure.value)


def test_root_can_only_reach_the_four_specialists() -> None:
    """The root holds no capability of its own; delegation is its entire tool surface.

    This is the boundary the whole design rests on. The root model chooses freely among
    specialists, but it cannot reach a document, a record, a file, or a shell, so its
    autonomy is bounded by which specialist it consults rather than by instruction.
    """

    orchestrator = build_root_orchestrator(
        AgentDependencies(
            ToolGateway(AuthorizationPolicy()),
            settings=settings(),
            model=scripted_model(text_turn("unused")),
        )
    )

    assert root_tool_names(orchestrator) == {
        KNOWLEDGE,
        RESEARCH,
        ANALYSIS,
        ENTERPRISE,
        "write_todos",
    }


def test_production_orchestrator_is_a_compiled_langgraph() -> None:
    orchestrator = build_root_orchestrator(
        AgentDependencies(
            ToolGateway(AuthorizationPolicy()),
            settings=settings(),
            model=scripted_model(text_turn("unused")),
        )
    )

    assert type(orchestrator.graph).__name__ == "CompiledStateGraph"
    assert {"model", "tools"} <= set(orchestrator.graph.nodes)


@pytest.mark.asyncio
async def test_an_undelegated_turn_returns_the_capabilities_answer() -> None:
    """Answering with no consultation is treated as out of scope, never as knowledge.

    A claim the root invented has no evidence ledger behind it, so there is nothing
    downstream that could catch it. Substituting the capabilities response makes the
    model's own parameters unreachable as an answer source.
    """

    orchestrator = build_root_orchestrator(
        AgentDependencies(
            ToolGateway(AuthorizationPolicy()),
            settings=settings(),
            model=scripted_model(text_turn("Sure! Here is a joke about databases.")),
        )
    )
    events: list[Any] = []

    request_context = context(Role.VIEWER)
    result = await orchestrator.run(
        "Tell me a joke about databases.", request_context, await capture_into(events)
    )
    validation = OutputValidator().validate(result, request_context.access_scope)

    assert result.route is AgentRoute.OUT_OF_SCOPE
    assert result.citations == []
    assert "joke" not in result.answer
    assert "organizational assistant" in result.answer
    assert "approved read-only duties" in result.answer
    assert validation.valid is True
    assert [item.event_type for item in events] == ["agent_started"]


async def corpus_gateway(project_root: Path) -> tuple[ToolGateway, Any]:
    runtime = await build_retrieval_runtime(
        Settings(retrieval_backend="memory", _env_file=None), project_root
    )
    gateway = ToolGateway(AuthorizationPolicy())
    gateway.register(knowledge_search_spec(runtime.service))
    gateway.register(python_analysis_spec(1_000))
    for spec in enterprise_tool_specs(InMemoryEnterpriseClient(), 1, 100_000):
        gateway.register(spec)
    return gateway, runtime


def corpus_orchestrator(gateway: ToolGateway, model: Any) -> RootOrchestrator:
    return build_root_orchestrator(AgentDependencies(gateway, settings=settings(), model=model))


@pytest.mark.asyncio
async def test_each_specialist_delegation_reaches_its_tools_over_the_real_corpus() -> None:
    """Every delegation reaches its specialist and comes back with tool-derived results.

    Each request gets its own scripted model so one delegation's turns cannot be consumed
    by another, and every specialist still runs its real loop against the real gateway.
    """

    gateway, runtime = await corpus_gateway(Path(__file__).parents[2])
    analyst = context(Role.ANALYST)
    events: list[Any] = []

    try:
        direct = await corpus_orchestrator(
            gateway,
            scripted_model(
                delegate(KNOWLEDGE, "Find the remote working policy."),
                tool_turn(("knowledge_search", {"query": "remote working policy"})),
                text_turn("The policy permits remote work for approved roles."),
                text_turn("Remote work is permitted for approved roles [1]."),
            ),
        ).run("What is the remote working policy?", analyst, await capture_into(events))

        research = await corpus_orchestrator(
            gateway,
            scripted_model(
                delegate(RESEARCH, "Investigate payment outages across the last year."),
                tool_turn(
                    (
                        "write_todos",
                        {"todos": [{"content": "Collect payment outages", "status": "pending"}]},
                    )
                ),
                tool_turn(
                    ("knowledge_search", {"query": "payment outage", "document_type": "incident"}),
                    ("knowledge_search", {"query": "payment incident runbook"}),
                ),
                text_turn("SUMMARY: Payment outages recurred.\nFINDING: Load caused them || ev\n"),
                text_turn("Payment outages recurred across services [1]."),
            ),
        ).run("Investigate payment outages across the last year.", analyst)

        analysis = await corpus_orchestrator(
            gateway,
            scripted_model(
                delegate(ANALYSIS, "Count payment incidents by root cause."),
                tool_turn(
                    ("knowledge_search", {"query": "payment incident", "document_type": "incident"})
                ),
                tool_turn(
                    (
                        "structured_analysis",
                        {
                            "operation": "count_by",
                            "field": "root_cause",
                            "records": [
                                {"root_cause": "connection pool exhaustion"},
                                {"root_cause": "connection pool exhaustion"},
                                {"root_cause": "configuration change"},
                            ],
                        },
                    )
                ),
                text_turn("Connection pool exhaustion leads at two of three."),
                text_turn("Connection pool exhaustion is the most common root cause [1]."),
            ),
        ).run("Count payment incidents by root cause.", analyst)

        enterprise = await corpus_orchestrator(
            gateway,
            scripted_model(
                delegate(ENTERPRISE, "Who owns the payment service?"),
                tool_turn(("search_services", {"query": "payment"})),
                text_turn("The payment service is owned by Payments Reliability."),
                text_turn("The payment service is owned by Payments Reliability."),
            ),
        ).run("Who owns the payment service?", analyst)
    finally:
        await runtime.close()

    assert direct.route is AgentRoute.DIRECT_KNOWLEDGE
    assert direct.citations and direct.evidence_ids
    assert research.route is AgentRoute.RESEARCH
    assert research.evidence_ids
    assert analysis.route is AgentRoute.ANALYSIS
    assert analysis.evidence_ids
    assert analysis.status is ResponseStatus.COMPLETE
    assert enterprise.route is AgentRoute.ENTERPRISE
    assert enterprise.status is ResponseStatus.COMPLETE
    assert "Payments Reliability" in enterprise.answer
    assert [item.event_type for item in events] == [
        "agent_started",
        "routing_completed",
        "subagent_started",
        "tool_started",
        "tool_completed",
        "subagent_completed",
    ]


@pytest.mark.asyncio
async def test_production_graph_streams_native_activity_and_one_result() -> None:
    gateway, runtime = await corpus_gateway(Path(__file__).parents[2])
    orchestrator = corpus_orchestrator(
        gateway,
        scripted_model(
            delegate(RESEARCH, "Investigate recurring payment incidents."),
            tool_turn(
                (
                    "write_todos",
                    {"todos": [{"content": "Find recurring incidents", "status": "pending"}]},
                )
            ),
            tool_turn(("knowledge_search", {"query": "recurring payment incidents"})),
            text_turn("SUMMARY: Payment incidents recur under load.\nUNRESOLVED: none"),
            text_turn("Payment incidents recur under load [1]."),
        ),
    )
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
    # The plan the UI shows is the agent's own todo list, and retrieval narrates itself.
    assert {item.node for item in transitions} >= {
        "intent_routing",
        "delegation",
        "planner",
        "knowledge_search",
    }


@pytest.mark.asyncio
async def test_native_stream_relays_specialist_tool_activity() -> None:
    gateway, runtime = await corpus_gateway(Path(__file__).parents[2])
    orchestrator = corpus_orchestrator(
        gateway,
        scripted_model(
            delegate(ENTERPRISE, "Who owns the payment service?"),
            tool_turn(("search_services", {"query": "payment"})),
            text_turn("The payment service is owned by Payments Reliability."),
            text_turn("The payment service is owned by Payments Reliability."),
        ),
    )
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
async def test_specialist_prose_never_reaches_the_answer_stream() -> None:
    """Only the root's own tokens stream; a specialist's working notes stay internal.

    A specialist loop runs inside a delegation tool, so its intermediate prose is on the
    same message stream as the answer. Streaming it would show the user reasoning that
    the validated response never contains.
    """

    gateway, runtime = await corpus_gateway(Path(__file__).parents[2])
    orchestrator = corpus_orchestrator(
        gateway,
        scripted_model(
            delegate(KNOWLEDGE, "Find the remote working policy."),
            tool_turn(("knowledge_search", {"query": "remote working policy"})),
            text_turn("SPECIALIST WORKING NOTES that must not be streamed."),
            text_turn("Remote work is permitted for approved roles [1]."),
        ),
    )
    try:
        updates = [
            update
            async for update in orchestrator.stream(
                "What is the remote working policy?", context(Role.ANALYST)
            )
        ]
    finally:
        await runtime.close()

    tokens = [item for item in updates if isinstance(item, AnswerToken)]
    results = [item for item in updates if isinstance(item, AgentExecutionResult)]
    streamed = "".join(item.text for item in tokens)
    assert "WORKING NOTES" not in streamed
    assert streamed == "Remote work is permitted for approved roles [1]."
    # Tokens must reach the caller before the terminal result, not after it.
    assert updates.index(tokens[-1]) < updates.index(results[0])
    assert results[0].answer == streamed


@pytest.mark.asyncio
async def test_enterprise_delegation_enforces_rbac_before_handler() -> None:
    gateway, runtime = await corpus_gateway(Path(__file__).parents[2])
    orchestrator = corpus_orchestrator(
        gateway,
        scripted_model(
            delegate(ENTERPRISE, "Who owns the payment service?"),
            tool_turn(("search_services", {"query": "payment"})),
            text_turn("never reached"),
        ),
    )
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


def orchestrator_with_stubs(
    knowledge_handler: Any,
    model: Any,
    enterprise_handler: Any = None,
) -> RootOrchestrator:
    gateway = ToolGateway(AuthorizationPolicy())
    gateway.register(
        ToolSpec(
            name="knowledge_search",
            capability=Capability.KNOWLEDGE_SEARCH,
            input_model=KnowledgeSearchInput,
            handler=knowledge_handler,
        )
    )
    if enterprise_handler is not None:
        gateway.register(
            ToolSpec(
                name="search_services",
                capability=Capability.MCP_READ,
                input_model=SearchServicesInput,
                handler=enterprise_handler,
            )
        )
    return build_root_orchestrator(AgentDependencies(gateway, settings=settings(), model=model))


def evidence_payload() -> dict[str, Any]:
    return Evidence(
        evidence_id="ev_handoff_fixture",
        document_id="policy-handoff-001",
        chunk_id="policy-handoff-001:purpose:0000",
        title="Remote Work Policy",
        content="Remote work is permitted for approved roles.",
        metadata={
            "access_level": "internal",
            "source_path": "policies/policy-handoff-001.md",
        },
        final_score=1.0,
    ).model_dump(mode="json")


@pytest.mark.asyncio
async def test_empty_enterprise_lookup_is_followed_by_a_knowledge_consultation() -> None:
    """The hand-off is now the root's decision, and the turn stays honest about it.

    The system of record had no row, so the root consulted the corpus instead. Recovery
    is still not a clean success: a specialist was spent and found nothing, which the
    reported status and warnings have to show.
    """

    async def empty_enterprise(parameters: Any, request_context: Any) -> dict[str, Any]:
        return {"services": []}

    async def knowledge(parameters: Any, request_context: Any) -> dict[str, Any]:
        return {"evidence": [evidence_payload()]}

    orchestrator = orchestrator_with_stubs(
        knowledge,
        scripted_model(
            delegate(ENTERPRISE, "Find the owner of the payment service."),
            tool_turn(("search_services", {"query": "payment service owner"})),
            text_turn("The service catalog has no matching record."),
            delegate(KNOWLEDGE, "Which document describes payment service ownership?"),
            tool_turn(("knowledge_search", {"query": "payment service ownership"})),
            text_turn("The remote work policy is the only match."),
            text_turn("Remote work is permitted for approved roles [1]."),
        ),
        enterprise_handler=empty_enterprise,
    )
    events: list[Any] = []

    request_context = context(Role.ANALYST)
    result = await orchestrator.run(
        "Who owns the payment service?", request_context, await capture_into(events)
    )

    assert result.route is AgentRoute.DIRECT_KNOWLEDGE
    assert result.evidence_ids == ["ev_handoff_fixture"]
    handoffs = [item for item in events if item.event_type == "handoff_completed"]
    assert len(handoffs) == 1
    assert handoffs[0].metadata["from_route"] == "enterprise"
    assert handoffs[0].metadata["route"] == "direct_knowledge"
    assert handoffs[0].metadata["handoff_hop"] == 1
    assert result.status is ResponseStatus.PARTIAL
    assert "[1]" in result.answer
    assert OutputValidator().validate(result, request_context.access_scope).valid is True


@pytest.mark.asyncio
async def test_empty_authorized_lookup_reports_insufficient_evidence() -> None:
    async def empty_knowledge(parameters: Any, request_context: Any) -> dict[str, Any]:
        return {"evidence": []}

    orchestrator = orchestrator_with_stubs(
        empty_knowledge,
        scripted_model(
            delegate(KNOWLEDGE, "Find the restricted fraud playbook."),
            tool_turn(("knowledge_search", {"query": "restricted fraud playbook"})),
            text_turn("I could not find authorized evidence that answers this question."),
            text_turn("I could not find authorized evidence that answers this question."),
        ),
    )
    events: list[Any] = []

    request_context = context(Role.VIEWER)
    result = await orchestrator.run(
        "What is the restricted fraud playbook?", request_context, await capture_into(events)
    )
    validation = OutputValidator().validate(result, request_context.access_scope)

    assert result.route is AgentRoute.DIRECT_KNOWLEDGE
    assert result.evidence_ids == []
    assert result.status is ResponseStatus.INSUFFICIENT_EVIDENCE
    assert validation.result.status is ResponseStatus.INSUFFICIENT_EVIDENCE


@pytest.mark.asyncio
async def test_a_specialist_that_delivered_leaves_no_handoff_trace() -> None:
    async def knowledge(parameters: Any, request_context: Any) -> dict[str, Any]:
        return {"evidence": [evidence_payload()]}

    orchestrator = orchestrator_with_stubs(
        knowledge,
        scripted_model(
            delegate(KNOWLEDGE, "What does the policy allow?"),
            tool_turn(("knowledge_search", {"query": "policy allowances"})),
            text_turn("Remote work is permitted for approved roles."),
            text_turn("Remote work is permitted for approved roles [1]."),
        ),
    )
    events: list[Any] = []

    result = await orchestrator.run(
        "What does the policy allow?", context(Role.VIEWER), await capture_into(events)
    )

    assert result.route is AgentRoute.DIRECT_KNOWLEDGE
    assert result.status is ResponseStatus.COMPLETE
    assert not any(item.event_type == "handoff_completed" for item in events)


@pytest.mark.asyncio
async def test_a_specialist_cannot_be_consulted_twice_in_one_turn() -> None:
    """Re-asking a specialist that already answered is a budget fact, not a request.

    The prompt tells the root not to loop on one specialist; the middleware makes the
    second attempt impossible, so a confused loop costs one blocked call rather than the
    whole budget.
    """

    calls = 0

    async def knowledge(parameters: Any, request_context: Any) -> dict[str, Any]:
        nonlocal calls
        calls += 1
        return {"evidence": []}

    orchestrator = orchestrator_with_stubs(
        knowledge,
        scripted_model(
            delegate(KNOWLEDGE, "First attempt."),
            tool_turn(("knowledge_search", {"query": "first attempt"})),
            text_turn("Nothing found."),
            delegate(KNOWLEDGE, "Try that again."),
            text_turn("I could not find authorized evidence that answers this question."),
        ),
    )

    result = await orchestrator.run("What is the fraud playbook?", context(Role.VIEWER))

    assert calls == 1
    assert result.status is ResponseStatus.INSUFFICIENT_EVIDENCE


@pytest.mark.asyncio
async def test_citation_markers_are_assigned_by_the_ledger_not_the_specialist() -> None:
    """Markers count positions in the evidence ledger, across every consultation.

    The root writes the markers, so they have to mean the same thing as the citations the
    API returns. Numbering from the ledger is what keeps a second consultation's evidence
    from renumbering the first one's.
    """

    async def knowledge(parameters: Any, request_context: Any) -> dict[str, Any]:
        return {"evidence": [evidence_payload()]}

    orchestrator = orchestrator_with_stubs(
        knowledge,
        scripted_model(
            delegate(KNOWLEDGE, "What does the policy allow?"),
            tool_turn(("knowledge_search", {"query": "policy allowances"})),
            text_turn("Remote work is permitted."),
            text_turn("Remote work is permitted for approved roles [1]."),
        ),
    )

    request_context = context(Role.VIEWER)
    result = await orchestrator.run("What does the policy allow?", request_context)

    assert [item.citation_id for item in result.citations] == ["1"]
    assert result.citations[0].evidence_id == "ev_handoff_fixture"
    assert OutputValidator().validate(result, request_context.access_scope).valid is True
