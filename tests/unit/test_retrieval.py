import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from conftest import scripted_model, text_turn, tool_turn

from orysys_assistant.agent.research_agent import ResearchLimits, ResearchSubagent
from orysys_assistant.agent.toolbox import ScopedToolbox
from orysys_assistant.config import Settings
from orysys_assistant.domain.errors import InvalidRequestError, RetrievalUnavailableError
from orysys_assistant.domain.models import Role
from orysys_assistant.retrieval.chunking import SectionAwareChunker, build_chunker
from orysys_assistant.retrieval.embeddings import DeterministicHashEmbedding
from orysys_assistant.retrieval.ingestion import IngestionPipeline
from orysys_assistant.retrieval.models import (
    DocumentSection,
    IngestionManifest,
    ParsedDocument,
    SearchFilters,
    SearchMatch,
    SparseVector,
)
from orysys_assistant.retrieval.parsing import MarkdownDocumentParser
from orysys_assistant.retrieval.reranking import HybridLexicalReranker
from orysys_assistant.retrieval.service import RetrievalService
from orysys_assistant.retrieval.sparse_encoding import BM25SparseEncoder
from orysys_assistant.retrieval.vector_store import InMemoryVectorStore, PineconeVectorStore
from orysys_assistant.security.access_scope import AccessScopeService
from orysys_assistant.security.authorization import AuthorizationPolicy
from orysys_assistant.security.models import TrustedRequestContext, UserIdentity
from orysys_assistant.tools.gateway import ToolGateway
from orysys_assistant.tools.knowledge_search import knowledge_search_spec

ROOT = Path(__file__).parents[2]
CORPUS_ROOT = ROOT / "data" / "sample_documents"


def user(role: Role, department: str) -> UserIdentity:
    return UserIdentity(
        user_id=f"evaluation-{role.value}",
        username=f"{role.value}@commercialbank.test",
        display_name="Evaluation User",
        role=role,
        department=department,
    )


async def build_retrieval(tmp_path: Path) -> tuple[RetrievalService, InMemoryVectorStore]:
    store = InMemoryVectorStore()
    embeddings = DeterministicHashEmbedding(dimension=256)
    manifest_path = tmp_path / "manifest.json"
    settings = Settings(_env_file=None)
    pipeline = IngestionPipeline(
        corpus_root=CORPUS_ROOT,
        manifest_path=manifest_path,
        namespace="commercial-bank",
        parser=MarkdownDocumentParser(CORPUS_ROOT),
        chunker=build_chunker(
            settings.organization_id,
            target_tokens=settings.chunk_target_tokens,
            max_tokens=settings.chunk_max_tokens,
            overlap_tokens=settings.chunk_overlap_tokens,
            merge_sections=settings.chunk_merge_sections,
        ),
        embeddings=embeddings,
        vector_store=store,
    )
    await pipeline.run()
    manifest = IngestionManifest.model_validate_json(manifest_path.read_text(encoding="utf-8"))
    retrieval = RetrievalService(
        vector_store=store,
        embeddings=embeddings,
        sparse_encoder=BM25SparseEncoder.from_dict(manifest.sparse_encoder),
        reranker=HybridLexicalReranker(),
    )
    return retrieval, store


def test_corpus_has_required_categories_metadata_and_deterministic_ids() -> None:
    paths = sorted(CORPUS_ROOT.rglob("*.md"))
    parser = MarkdownDocumentParser(CORPUS_ROOT)
    documents = [parser.parse(path) for path in paths]

    assert len(documents) == 48
    assert {document.document_type for document in documents} == {
        "policy",
        "architecture",
        "runbook",
        "incident",
        "product_specification",
        "meeting_note",
    }
    assert {document.access_level for document in documents} == {
        "internal",
        "confidential",
        "restricted",
    }
    assert len({document.document_id for document in documents}) == len(documents)
    assert all(
        parser.parse(path).document_id == document.document_id
        for path, document in zip(paths, documents, strict=True)
    )
    injection = next(
        document for document in documents if document.fixture_id == "incident-injection-001"
    )
    assert "IGNORE ALL PREVIOUS INSTRUCTIONS" in " ".join(
        section.content for section in injection.sections
    )
    assert {
        "incident-payments-007",
        "incident-payments-008",
        "incident-payments-009",
        "incident-payments-010",
        "meeting-orion-readiness-001",
        "meeting-payments-reliability-003",
        "arch-instant-payments-continuity-001",
        "arch-payment-connection-governance-001",
        "runbook-payment-regional-failover-001",
        "runbook-payment-reconciliation-001",
        "policy-resilience-change-assurance-001",
        "spec-instant-payment-continuity-001",
    } <= {document.fixture_id for document in documents}


def test_section_chunking_is_bounded_overlapping_and_deterministic() -> None:
    document = ParsedDocument(
        document_id="a" * 64,
        fixture_id="large-test-document",
        title="Large Test Document",
        document_type="architecture",
        department="technology",
        access_level="internal",
        created_date="2025-01-01",
        source_path="architecture/large.md",
        checksum="b" * 64,
        sections=(DocumentSection(heading="Large section", content="word " * 2_000),),
    )
    chunker = SectionAwareChunker(
        "commercial-bank",
        target_tokens=650,
        max_tokens=800,
        overlap_tokens=80,
        merge_sections=True,
    )

    first = chunker.chunk(document)
    second = chunker.chunk(document)

    assert len(first) == 3
    assert all(chunk.token_count <= 800 for chunk in first)
    assert [chunk.metadata.chunk_id for chunk in first] == [
        chunk.metadata.chunk_id for chunk in second
    ]
    assert all(chunk.metadata.access_level == "internal" for chunk in first)


@pytest.mark.asyncio
async def test_reingestion_upserts_same_ids_without_duplicates(tmp_path: Path) -> None:
    store = InMemoryVectorStore()
    embeddings = DeterministicHashEmbedding(dimension=256)
    pipeline = IngestionPipeline(
        corpus_root=CORPUS_ROOT,
        manifest_path=tmp_path / "manifest.json",
        namespace="commercial-bank",
        parser=MarkdownDocumentParser(CORPUS_ROOT),
        chunker=SectionAwareChunker("commercial-bank"),
        embeddings=embeddings,
        vector_store=store,
    )

    first = await pipeline.run()
    second = await pipeline.run()

    assert first.documents == second.documents == 48
    assert first.chunks == second.chunks
    assert first.chunks > 48
    assert store.count("commercial-bank") == first.chunks
    assert store.count("another-namespace") == 0
    assert second.deleted_stale == 0


@pytest.mark.asyncio
async def test_hybrid_recall_at_five_and_zero_unauthorized_chunks(tmp_path: Path) -> None:
    retrieval, _ = await build_retrieval(tmp_path)
    scope_service = AccessScopeService(Settings(_env_file=None))
    dataset = json.loads((ROOT / "data" / "retrieval_evaluation.json").read_text())
    retrieved_expected = 0
    baseline_expected = 0
    expected_total = 0

    for case in dataset["cases"]:
        scope = scope_service.build(user(Role(case["role"]), case["department"]))
        reranker = retrieval._reranker  # noqa: SLF001
        retrieval._reranker = None  # noqa: SLF001
        baseline = await retrieval.search(case["question"], scope, top_k=5)
        retrieval._reranker = reranker  # noqa: SLF001
        evidence = await retrieval.search(case["question"], scope, top_k=5)
        fixture_ids = {item.metadata["fixture_id"] for item in evidence}
        baseline_fixture_ids = {item.metadata["fixture_id"] for item in baseline}
        expected = set(case["expected_fixture_ids"])
        retrieved_expected += len(fixture_ids & expected)
        baseline_expected += len(baseline_fixture_ids & expected)
        expected_total += len(expected)

        assert all(
            item.metadata["access_level"] in scope.allowed_access_levels for item in evidence
        )
        if scope.allowed_departments:
            assert all(
                item.metadata["department"] in scope.allowed_departments for item in evidence
            )
        assert all(item.evidence_id.startswith("ev_") for item in evidence)
        assert all(item.chunk_id for item in evidence)

    assert retrieved_expected >= baseline_expected
    assert retrieved_expected / expected_total >= 0.80


def test_hard_research_questions_reference_real_multi_source_evidence() -> None:
    dataset = json.loads((ROOT / "data" / "hard_research_questions.json").read_text())
    parser = MarkdownDocumentParser(CORPUS_ROOT)
    documents = [parser.parse(path) for path in sorted(CORPUS_ROOT.rglob("*.md"))]
    fixture_types = {document.fixture_id: document.document_type for document in documents}

    assert len(dataset["cases"]) == 8
    assert {case["id"] for case in dataset["cases"]} == {
        f"HRQ-{index:03d}" for index in range(1, 9)
    }
    for case in dataset["cases"]:
        fixtures = case["expected_fixture_ids"]
        assert len(fixtures) >= 4
        assert set(fixtures) <= fixture_types.keys()
        assert len({fixture_types[fixture_id] for fixture_id in fixtures}) >= 2
        assert case["research_challenge"]


@pytest.mark.asyncio
async def test_hard_control_audit_reaches_all_expected_multi_source_evidence(
    tmp_path: Path,
) -> None:
    retrieval, store = await build_retrieval(tmp_path)
    settings = Settings(_env_file=None)
    gateway = ToolGateway(AuthorizationPolicy())
    gateway.register(knowledge_search_spec(retrieval))
    identity = user(Role.ANALYST, "payments")
    context = TrustedRequestContext(
        identity=identity,
        access_scope=AccessScopeService(settings).build(identity),
        rate_limit_remaining=100,
    )
    dataset = json.loads((ROOT / "data" / "hard_research_questions.json").read_text())
    case = dataset["cases"][0]
    # The decomposition a competent planner would produce, fixed here so the assertion
    # measures retrieval reach across document families rather than model phrasing.
    model = scripted_model(
        tool_turn(
            (
                "knowledge_search",
                {
                    "query": "PAY-1224 regional failover duplicate authorizations",
                    "document_type": "incident",
                    "top_k": 4,
                },
            ),
            (
                "knowledge_search",
                {
                    "query": "PAY-1241 settlement consumer schema backlog",
                    "document_type": "incident",
                    "top_k": 4,
                },
            ),
            (
                "knowledge_search",
                {
                    "query": "PAY-1260 recovery drill false-green declaration",
                    "document_type": "incident",
                    "top_k": 4,
                },
            ),
            (
                "knowledge_search",
                {"query": "Project Orion payment failures 2026", "top_k": 8},
            ),
        ),
        tool_turn(
            (
                "knowledge_search",
                {
                    "query": "Project Orion readiness connection budget controls reported complete",
                    "document_type": "meeting_note",
                    "top_k": 8,
                },
            ),
            (
                "knowledge_search",
                {"query": "payment connection governance connection budget", "top_k": 8},
            ),
            ("knowledge_search", {"query": "payments reliability review actions", "top_k": 8}),
        ),
        text_turn("SUMMARY: Controls reported complete were later contradicted.\nUNRESOLVED: none"),
    )
    agent = ResearchSubagent(
        ScopedToolbox(gateway, frozenset({"knowledge_search"})),
        ResearchLimits.from_settings(settings),
        model,
    )
    transitions: list[Any] = []

    async def capture(transition: Any) -> None:
        transitions.append(transition)

    try:
        execution = await agent.run(case["question"], context, capture)
    finally:
        await store.close()

    observed = {item.metadata["fixture_id"] for item in execution.evidence}
    assert set(case["expected_fixture_ids"]) <= observed
    assert len(execution.evidence) >= 7
    assert {"knowledge_search"} <= {transition.node for transition in transitions}


@pytest.mark.asyncio
async def test_filters_narrow_scope_and_never_broaden_it(tmp_path: Path) -> None:
    retrieval, _ = await build_retrieval(tmp_path)
    scope_service = AccessScopeService(Settings(_env_file=None))
    analyst_scope = scope_service.build(user(Role.ANALYST, "payments"))

    incidents = await retrieval.search(
        "payment failures",
        analyst_scope,
        SearchFilters(document_type="incident", created_after="2025-05-01"),
        top_k=10,
    )
    forbidden_department = await retrieval.search(
        "fraud investigation playbook",
        analyst_scope,
        SearchFilters(department="fraud"),
        top_k=10,
    )

    assert incidents
    assert all(item.metadata["document_type"] == "incident" for item in incidents)
    assert all(item.metadata["created_date"] >= "2025-05-01" for item in incidents)
    assert all(isinstance(item.metadata["created_date_ordinal"], int) for item in incidents)
    assert forbidden_department == []


def test_hybrid_sparse_floor_keeps_dense_only_candidates() -> None:
    metadata = {
        "document_id": "doc-1",
        "title": "Policy",
        "content": "Remote work is permitted.",
        "page_number": -1,
    }
    service = RetrievalService(
        vector_store=InMemoryVectorStore(),
        embeddings=DeterministicHashEmbedding(dimension=256),
        sparse_encoder=BM25SparseEncoder(),
        minimum_sparse_score=0.1,
        reranker=None,
    )
    dense_only = SearchMatch(id="dense-only", score=0.9, metadata=dict(metadata))
    sparse_weak = SearchMatch(id="sparse-weak", score=0.05, metadata=dict(metadata))
    combined = service._combine([dense_only], [sparse_weak])
    filtered = [
        item
        for item in combined
        if item.sparse_score is None or item.sparse_score >= service._minimum_sparse_score
    ]

    assert any(item.chunk_id == "dense-only" for item in filtered)
    assert not any(item.chunk_id == "sparse-weak" for item in filtered)


@pytest.mark.asyncio
async def test_sparse_failure_uses_dense_only_but_dense_failure_fails_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    retrieval, store = await build_retrieval(tmp_path)
    scope = AccessScopeService(Settings(_env_file=None)).build(user(Role.VIEWER, "retail-banking"))

    async def unavailable(*args: Any, **kwargs: Any) -> Any:
        raise OSError("injected dependency failure")

    monkeypatch.setattr(store, "query_sparse", unavailable)
    dense_only = await retrieval.search("remote work policy", scope, top_k=3)

    assert dense_only
    assert all(item.metadata["retrieval_mode"] == "dense_only" for item in dense_only)
    assert all(item.metadata["retrieval_degraded"] is True for item in dense_only)

    monkeypatch.setattr(store, "query_dense", unavailable)
    with pytest.raises(RetrievalUnavailableError):
        await retrieval.search("remote work policy", scope, top_k=3)


@pytest.mark.asyncio
async def test_dense_sparse_hybrid_and_knowledge_tool_attribution(tmp_path: Path) -> None:
    retrieval, _ = await build_retrieval(tmp_path)
    scope = AccessScopeService(Settings(_env_file=None)).build(user(Role.VIEWER, "retail-banking"))
    context = TrustedRequestContext(
        identity=user(Role.VIEWER, "retail-banking"),
        access_scope=scope,
        rate_limit_remaining=5,
    )
    gateway = ToolGateway(AuthorizationPolicy())
    gateway.register(knowledge_search_spec(retrieval))

    result = await gateway.execute(
        "knowledge_search",
        {"query": "remote work during probation", "top_k": 5},
        context,
    )

    evidence = result["evidence"]
    assert evidence
    assert evidence[0]["document_id"]
    assert evidence[0]["chunk_id"]
    assert evidence[0]["dense_score"] is not None
    assert evidence[0]["sparse_score"] is not None
    assert any(item["metadata"]["fixture_id"] == "policy-remote-work-001" for item in evidence)

    with pytest.raises(InvalidRequestError):
        await gateway.execute(
            "knowledge_search",
            {"query": "restricted records", "namespace": "other-bank"},
            context,
        )


@pytest.mark.asyncio
async def test_pinecone_adapter_passes_trusted_namespace_and_filter() -> None:
    calls: list[dict[str, Any]] = []

    class FakeIndex:
        async def query(self, **kwargs: Any) -> Any:
            calls.append(kwargs)
            return SimpleNamespace(matches=[])

        async def close(self) -> None:
            return None

    store = PineconeVectorStore("test-key", "test-index", 3, host="test-host")
    store._index = FakeIndex()  # type: ignore[assignment]  # noqa: SLF001
    trusted_filter = {
        "organization": {"$eq": "commercial-bank"},
        "access_level": {"$in": ["internal"]},
    }

    await store.query_dense("commercial-bank", [1.0, 0.0, 0.0], trusted_filter, 5)
    await store.query_sparse(
        "commercial-bank",
        SparseVector(indices=[1], values=[2.0]),
        trusted_filter,
        5,
    )

    assert len(calls) == 2
    assert all(call["namespace"] == "commercial-bank" for call in calls)
    assert all(call["filter"] == trusted_filter for call in calls)
    assert calls[1]["sparse_vector"] == {"indices": [1], "values": [2.0]}
    await store.close()
