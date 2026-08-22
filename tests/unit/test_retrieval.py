import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from orysys_assistant.config import Settings
from orysys_assistant.domain.errors import InvalidRequestError
from orysys_assistant.domain.models import Role
from orysys_assistant.retrieval.chunking import SectionAwareChunker
from orysys_assistant.retrieval.embeddings import DeterministicHashEmbedding
from orysys_assistant.retrieval.ingestion import IngestionPipeline
from orysys_assistant.retrieval.models import (
    DocumentSection,
    IngestionManifest,
    ParsedDocument,
    SearchFilters,
    SparseVector,
)
from orysys_assistant.retrieval.parsing import MarkdownDocumentParser
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
    pipeline = IngestionPipeline(
        corpus_root=CORPUS_ROOT,
        manifest_path=manifest_path,
        namespace="commercial-bank",
        parser=MarkdownDocumentParser(CORPUS_ROOT),
        chunker=SectionAwareChunker("commercial-bank"),
        embeddings=embeddings,
        vector_store=store,
    )
    await pipeline.run()
    manifest = IngestionManifest.model_validate_json(manifest_path.read_text(encoding="utf-8"))
    retrieval = RetrievalService(
        vector_store=store,
        embeddings=embeddings,
        sparse_encoder=BM25SparseEncoder.from_dict(manifest.sparse_encoder),
    )
    return retrieval, store


def test_corpus_has_required_categories_metadata_and_deterministic_ids() -> None:
    paths = sorted(CORPUS_ROOT.rglob("*.md"))
    parser = MarkdownDocumentParser(CORPUS_ROOT)
    documents = [parser.parse(path) for path in paths]

    assert 30 <= len(documents) <= 50
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
        "commercial-bank", target_tokens=650, max_tokens=800, overlap_tokens=80
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

    assert first.documents == second.documents == 36
    assert first.chunks == second.chunks == 36
    assert store.count("commercial-bank") == 36
    assert store.count("another-namespace") == 0
    assert second.deleted_stale == 0


@pytest.mark.asyncio
async def test_hybrid_recall_at_five_and_zero_unauthorized_chunks(tmp_path: Path) -> None:
    retrieval, _ = await build_retrieval(tmp_path)
    scope_service = AccessScopeService(Settings(_env_file=None))
    dataset = json.loads((ROOT / "data" / "retrieval_evaluation.json").read_text())
    retrieved_expected = 0
    expected_total = 0

    for case in dataset["cases"]:
        scope = scope_service.build(user(Role(case["role"]), case["department"]))
        evidence = await retrieval.search(case["question"], scope, top_k=5)
        fixture_ids = {item.metadata["fixture_id"] for item in evidence}
        expected = set(case["expected_fixture_ids"])
        retrieved_expected += len(fixture_ids & expected)
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

    assert retrieved_expected / expected_total >= 0.80


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
    assert forbidden_department == []


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
