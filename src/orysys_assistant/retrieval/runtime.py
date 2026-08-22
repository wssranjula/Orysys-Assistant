"""Build the configured retrieval service and its owned resources."""

import asyncio
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from orysys_assistant.config import Settings
from orysys_assistant.domain.errors import InvalidRequestError
from orysys_assistant.memory.runtime import MemoryRuntime
from orysys_assistant.retrieval.chunking import SectionAwareChunker
from orysys_assistant.retrieval.embeddings import (
    DeterministicHashEmbedding,
    OpenAIEmbeddingProvider,
)
from orysys_assistant.retrieval.ingestion import IngestionPipeline
from orysys_assistant.retrieval.models import IngestionManifest
from orysys_assistant.retrieval.parsing import MarkdownDocumentParser
from orysys_assistant.retrieval.service import RetrievalService
from orysys_assistant.retrieval.sparse_encoding import BM25SparseEncoder
from orysys_assistant.retrieval.vector_store import (
    InMemoryVectorStore,
    PineconeVectorStore,
    VectorStore,
)
from orysys_assistant.tools.gateway import ToolGateway

if TYPE_CHECKING:
    from orysys_assistant.agent.orchestrator import RootOrchestrator
    from orysys_assistant.tools.mcp_client import EnterpriseClient


@dataclass(frozen=True, slots=True)
class RetrievalRuntime:
    service: RetrievalService
    vector_store: VectorStore

    async def close(self) -> None:
        await self.vector_store.close()


async def build_retrieval_runtime(
    settings: Settings,
    project_root: Path | None = None,
) -> RetrievalRuntime:
    """Build an offline memory runtime or connect to the configured Pinecone index."""
    root = (project_root or Path(__file__).resolve().parents[3]).resolve()
    if settings.retrieval_backend == "memory":
        return await _build_memory_runtime(settings, root)
    if settings.retrieval_backend == "pinecone":
        return _build_pinecone_runtime(settings, root)
    raise InvalidRequestError("The configured retrieval backend is not supported.")


async def _build_memory_runtime(settings: Settings, root: Path) -> RetrievalRuntime:
    corpus = root / "data" / "sample_documents"
    if not corpus.is_dir():
        raise RuntimeError(f"Sample corpus is missing: {corpus}")
    embeddings = DeterministicHashEmbedding(dimension=256)
    vector_store = InMemoryVectorStore()
    manifest_path = root / ".data" / "runtime_ingestion_manifest.json"
    await IngestionPipeline(
        corpus_root=corpus,
        manifest_path=manifest_path,
        namespace=settings.pinecone_namespace,
        parser=MarkdownDocumentParser(corpus),
        chunker=SectionAwareChunker(settings.organization_id),
        embeddings=embeddings,
        vector_store=vector_store,
    ).run()
    manifest = IngestionManifest.model_validate_json(manifest_path.read_text(encoding="utf-8"))
    return RetrievalRuntime(
        service=_service(
            settings,
            vector_store,
            embeddings,
            BM25SparseEncoder.from_dict(manifest.sparse_encoder),
        ),
        vector_store=vector_store,
    )


def _build_pinecone_runtime(settings: Settings, root: Path) -> RetrievalRuntime:
    if not settings.pinecone_api_key or not settings.openai_api_key:
        raise RuntimeError("Pinecone retrieval requires PINECONE_API_KEY and OPENAI_API_KEY.")
    runtime_manifest = root / ".data" / "ingestion_manifest.json"
    manifest_path = (
        runtime_manifest
        if runtime_manifest.is_file()
        else root / "data" / "ingestion_manifest.json"
    )
    manifest = IngestionManifest.model_validate_json(manifest_path.read_text(encoding="utf-8"))
    embeddings = OpenAIEmbeddingProvider(
        settings.openai_api_key,
        settings.embedding_model,
        settings.embedding_dimension,
    )
    if (
        manifest.embedding_model != embeddings.model_name
        or manifest.embedding_dimension != embeddings.dimension
    ):
        raise RuntimeError("The ingestion manifest does not match the configured embedding model.")
    vector_store = PineconeVectorStore(
        settings.pinecone_api_key,
        settings.pinecone_index,
        settings.embedding_dimension,
        host=settings.pinecone_host,
    )
    return RetrievalRuntime(
        service=_service(
            settings,
            vector_store,
            embeddings,
            BM25SparseEncoder.from_dict(manifest.sparse_encoder),
        ),
        vector_store=vector_store,
    )


def _service(
    settings: Settings,
    vector_store: VectorStore,
    embeddings: DeterministicHashEmbedding | OpenAIEmbeddingProvider,
    sparse_encoder: BM25SparseEncoder,
) -> RetrievalService:
    return RetrievalService(
        vector_store=vector_store,
        embeddings=embeddings,
        sparse_encoder=sparse_encoder,
        dense_weight=settings.retrieval_dense_weight,
        sparse_weight=settings.retrieval_sparse_weight,
        candidate_count=settings.retrieval_candidate_count,
        minimum_sparse_score=settings.retrieval_min_sparse_score,
    )


class AgentRuntimeManager:
    """Lazily initialize one process-local retrieval and orchestration runtime."""

    def __init__(
        self,
        settings: Settings,
        gateway: ToolGateway,
        memory_runtime: MemoryRuntime,
        project_root: Path | None = None,
        enterprise_client: "EnterpriseClient | None" = None,
    ) -> None:
        self._settings = settings
        self._gateway = gateway
        self._memory_runtime = memory_runtime
        self._project_root = project_root
        self._enterprise_client = enterprise_client
        self._lock = asyncio.Lock()
        self._runtime: RetrievalRuntime | None = None
        self._orchestrator: RootOrchestrator | None = None

    async def get_orchestrator(self) -> "RootOrchestrator":
        if self._orchestrator is None:
            async with self._lock:
                if self._orchestrator is None:
                    from orysys_assistant.agent.build_agent import (
                        AgentDependencies,
                        build_root_orchestrator,
                    )
                    from orysys_assistant.tools.enterprise import enterprise_tool_specs
                    from orysys_assistant.tools.knowledge_search import knowledge_search_spec
                    from orysys_assistant.tools.mcp_client import (
                        InMemoryEnterpriseClient,
                        MCPClientAdapter,
                    )
                    from orysys_assistant.tools.python_analysis import python_analysis_spec

                    await self._memory_runtime.start()
                    self._runtime = await build_retrieval_runtime(
                        self._settings, self._project_root
                    )
                    self._gateway.register(
                        knowledge_search_spec(
                            self._runtime.service, self._settings.retrieval_retry_attempts
                        )
                    )
                    self._gateway.register(
                        python_analysis_spec(self._settings.analysis_max_records)
                    )
                    enterprise_client = self._enterprise_client or (
                        InMemoryEnterpriseClient()
                        if self._settings.mcp_backend == "memory"
                        else MCPClientAdapter(
                            self._settings.mcp_server_url,
                            self._settings.mcp_timeout_seconds,
                        )
                    )
                    for spec in enterprise_tool_specs(
                        enterprise_client,
                        self._settings.mcp_timeout_seconds,
                        self._settings.mcp_max_result_bytes,
                        self._settings.mcp_retry_attempts,
                    ):
                        self._gateway.register(spec)
                    self._orchestrator = build_root_orchestrator(
                        AgentDependencies(
                            self._gateway,
                            self._settings,
                            self._memory_runtime.checkpointer,
                        )
                    )
        return self._orchestrator

    async def close(self) -> None:
        if self._runtime is not None:
            await self._runtime.close()
