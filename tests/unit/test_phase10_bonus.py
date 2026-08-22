from typing import Any

import pytest

from orysys_assistant.agent.approval_graph import ApprovalService, ApprovalStatus
from orysys_assistant.agent.research_graph import ResearchLimits, ResearchWorkflow
from orysys_assistant.agent.toolbox import ScopedToolbox
from orysys_assistant.config import Settings
from orysys_assistant.domain.errors import InvalidRequestError
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


def context(role: Role = Role.ADMINISTRATOR) -> TrustedRequestContext:
    identity = UserIdentity(
        user_id=f"phase10-{role.value}",
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
    admin = context()
    parameters = {
        "incident_id": "INC-2026-004",
        "status": "monitoring",
        "reason": "Recovery checks completed",
    }

    pending = await service.create("modify_incident", parameters, "Operational update", admin)
    assert pending.status is ApprovalStatus.PENDING
    assert store.updates == []

    executed = await service.decide(pending.approval_id, True, admin)
    assert executed.status is ApprovalStatus.EXECUTED
    assert len(store.updates) == 1
    with pytest.raises(InvalidRequestError):
        await service.decide(pending.approval_id, True, admin)
    assert len(store.updates) == 1

    rejected = await service.create("modify_incident", parameters, "Reject this update", admin)
    rejected = await service.decide(rejected.approval_id, False, admin)
    assert rejected.status is ApprovalStatus.REJECTED
    assert len(store.updates) == 1


@pytest.mark.asyncio
async def test_all_worker_failures_open_circuit_without_recursive_fanout() -> None:
    async def failing_handler(parameters: Any, request_context: Any) -> dict[str, Any]:
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
    workflow = ResearchWorkflow(
        ScopedToolbox(gateway, frozenset({"knowledge_search"})),
        ResearchLimits(
            max_initial_tasks=4,
            max_followup_tasks=2,
            max_recursion_depth=2,
            max_parallel_workers=4,
            max_total_tool_calls=20,
            max_chunks_per_worker=6,
            worker_timeout_seconds=1,
            overall_timeout_seconds=5,
        ),
    )
    transitions = []

    async def capture(transition: Any) -> None:
        transitions.append(transition)

    result = await workflow.run(
        "Compare payment incidents, policies, and runbooks", context(Role.ANALYST), capture
    )

    assert result.result.partial is True
    assert any("contain" in warning for warning in result.result.warnings)
    assert not any(item.node == "followup_planner" for item in transitions)
    reducer = next(
        item
        for item in transitions
        if item.node == "reducer" and item.status == "completed"
    )
    assert reducer.metadata["failure_circuit_open"] is True
