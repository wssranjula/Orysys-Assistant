from typing import Any

import pytest
from conftest import scripted_model, text_turn, tool_turn

from orysys_assistant.agent.approval_graph import ApprovalService, ApprovalStatus
from orysys_assistant.agent.research_agent import ResearchLimits, ResearchSubagent
from orysys_assistant.agent.toolbox import ScopedToolbox
from orysys_assistant.config import Settings
from orysys_assistant.domain.errors import AuthorizationError, InvalidRequestError
from orysys_assistant.domain.models import Role
from orysys_assistant.memory.repository import InMemoryConversationRepository
from orysys_assistant.retrieval.models import Evidence
from orysys_assistant.retrieval.reranking import HybridLexicalReranker
from orysys_assistant.security.access_scope import AccessScopeService
from orysys_assistant.security.authorization import AuthorizationPolicy, Capability
from orysys_assistant.security.models import TrustedRequestContext, UserIdentity
from orysys_assistant.tools.admin import DummyIncidentWriteStore, modify_incident_spec
from orysys_assistant.tools.gateway import ToolGateway, ToolSpec
from orysys_assistant.tools.knowledge_search import KnowledgeSearchInput


def context(role: Role = Role.ADMINISTRATOR, user_id: str | None = None) -> TrustedRequestContext:
    identity = UserIdentity(
        user_id=user_id or f"phase10-{role.value}",
        username=f"{role.value}@commercialbank.test",
        display_name="Phase 10 User",
        role=role,
        department="technology",
    )
    return TrustedRequestContext(
        identity=identity,
        access_scope=AccessScopeService(Settings(_env_file=None)).build(identity),
        rate_limit_remaining=10,
    )


def evidence(chunk_id: str, title: str, content: str, score: float) -> Evidence:
    return Evidence(
        evidence_id=f"ev_{chunk_id}",
        document_id=f"doc_{chunk_id}",
        chunk_id=chunk_id,
        title=title,
        content=content,
        metadata={"source_path": f"fixtures/{chunk_id}.md"},
        final_score=score,
    )


def test_reranker_promotes_query_coverage_and_preserves_candidate_ledger() -> None:
    candidates = [
        evidence("generic", "General operations", "Unrelated platform guidance.", 0.90),
        evidence(
            "relevant",
            "Payment incident INC-2026-004",
            "The payment incident INC-2026-004 exhausted its connection pool.",
            0.75,
        ),
    ]

    ranked = HybridLexicalReranker(lexical_weight=0.6).rerank(
        "Why did payment incident INC-2026-004 fail?", candidates, top_k=1
    )

    assert ranked[0].chunk_id == "relevant"
    assert ranked[0].metadata["reranked"] is True
    assert ranked[0].metadata["first_stage_score"] == 0.75
    assert {item.chunk_id for item in candidates} == {"generic", "relevant"}


@pytest.mark.asyncio
async def test_long_term_preferences_are_explicitly_stored_and_owner_isolated() -> None:
    repository = InMemoryConversationRepository(20, 8_000)

    await repository.upsert_preference("alice", "answer_style", "Use concise bullet points")
    await repository.upsert_preference("bob", "answer_style", "Use detailed prose")

    alice = await repository.list_preferences("alice")
    assert [(item.key, item.value) for item in alice] == [
        ("answer_style", "Use concise bullet points")
    ]
    assert await repository.delete_preference("alice", "answer_style") is True
    assert await repository.list_preferences("alice") == []
    assert len(await repository.list_preferences("bob")) == 1


@pytest.mark.asyncio
async def test_human_approval_executes_once_and_rejection_has_no_side_effect() -> None:
    gateway = ToolGateway(AuthorizationPolicy())
    store = DummyIncidentWriteStore()
    gateway.register(modify_incident_spec(store))
    service = ApprovalService(gateway)
    requester = context(user_id="phase10-requester")
    approver = context(user_id="phase10-approver")
    parameters = {
        "incident_id": "INC-2026-004",
        "status": "monitoring",
        "reason": "Recovery checks completed",
    }

    pending = await service.create("modify_incident", parameters, "Operational update", requester)
    assert pending.status is ApprovalStatus.PENDING
    assert store.updates == []

    with pytest.raises(AuthorizationError):
        await service.decide(pending.approval_id, True, requester)

    executed = await service.decide(pending.approval_id, True, approver)
    assert executed.status is ApprovalStatus.EXECUTED
    assert len(store.updates) == 1
    with pytest.raises(InvalidRequestError):
        await service.decide(pending.approval_id, True, approver)
    assert len(store.updates) == 1

    rejected = await service.create("modify_incident", parameters, "Reject this update", requester)
    rejected = await service.decide(rejected.approval_id, False, approver)
    assert rejected.status is ApprovalStatus.REJECTED
    assert len(store.updates) == 1


@pytest.mark.asyncio
async def test_shared_dependency_failure_degrades_instead_of_cascading() -> None:
    """A dependency outage must cost the turn its evidence, not the whole request.

    Every retrieval fails here, which is the butterfly-effect case: without containment
    at the tool boundary, one broken backend would surface as an unhandled exception in
    the API stream instead of an honest partial answer.
    """

    calls = 0

    async def failing_handler(parameters: Any, request_context: Any) -> dict[str, Any]:
        nonlocal calls
        calls += 1
        raise OSError("shared retrieval dependency unavailable")

    gateway = ToolGateway(AuthorizationPolicy())
    gateway.register(
        ToolSpec(
            name="knowledge_search",
            capability=Capability.KNOWLEDGE_SEARCH,
            input_model=KnowledgeSearchInput,
            handler=failing_handler,
        )
    )
    model = scripted_model(
        tool_turn(
            ("knowledge_search", {"query": "payment incidents"}),
            ("knowledge_search", {"query": "payment policies"}),
            ("knowledge_search", {"query": "payment runbooks"}),
        ),
        text_turn("SUMMARY: No authorized evidence could be retrieved.\nUNRESOLVED: Everything."),
    )
    agent = ResearchSubagent(
        ScopedToolbox(gateway, frozenset({"knowledge_search"})),
        ResearchLimits(
            max_tool_calls=20,
            max_model_calls=4,
            max_chunks_per_search=6,
            overall_timeout_seconds=5,
        ),
        model,
    )
    transitions = []

    async def capture(transition: Any) -> None:
        transitions.append(transition)

    result = await agent.run(
        "Compare payment incidents, policies, and runbooks", context(Role.ANALYST), capture
    )

    assert calls == 3
    assert result.evidence == []
    assert result.grounded is False
    assert any("unavailable" in warning for warning in result.warnings)
    degraded = [item for item in transitions if item.status == "degraded"]
    assert len(degraded) == 3
    assert all(item.metadata["error_type"] == "OSError" for item in degraded)
