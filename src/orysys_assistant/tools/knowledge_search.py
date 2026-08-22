"""Role-gated knowledge search tool backed by the hybrid retrieval service."""

from datetime import date
from typing import Any, cast

from pydantic import BaseModel, ConfigDict, Field

from orysys_assistant.domain.errors import RetrievalUnavailableError
from orysys_assistant.guardrails.content import RetrievedContentGuard
from orysys_assistant.guardrails.retry import retry_async
from orysys_assistant.retrieval.models import SearchFilters
from orysys_assistant.retrieval.service import RetrievalService
from orysys_assistant.security.authorization import Capability
from orysys_assistant.security.models import TrustedRequestContext
from orysys_assistant.tools.gateway import ToolSpec


class KnowledgeSearchInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    query: str = Field(min_length=2, max_length=2_000)
    department: str | None = Field(default=None, max_length=100)
    document_type: str | None = Field(default=None, max_length=100)
    created_after: date | None = None
    created_before: date | None = None
    top_k: int = Field(default=6, ge=1, le=12)


class KnowledgeSearchTool:
    def __init__(self, retrieval: RetrievalService, retries: int = 1) -> None:
        self._retrieval = retrieval
        self._retries = retries
        self._content_guard = RetrievedContentGuard()

    async def __call__(
        self,
        parameters: BaseModel,
        context: TrustedRequestContext,
    ) -> dict[str, Any]:
        request = cast(KnowledgeSearchInput, parameters)
        filters = SearchFilters(
            department=request.department,
            document_type=request.document_type,
            created_after=request.created_after,
            created_before=request.created_before,
        )
        evidence = await retry_async(
            lambda: self._retrieval.search(
                request.query, context.access_scope, filters, top_k=request.top_k
            ),
            retries=self._retries,
            retry_on=(RetrievalUnavailableError, OSError, TimeoutError),
        )
        evidence = self._content_guard.protect(evidence)
        warnings: list[str] = []
        if any(item.metadata.get("retrieval_degraded") for item in evidence):
            warnings.append("Sparse retrieval was unavailable; dense-only evidence was used.")
        if any(item.metadata.get("prompt_injection_flagged") for item in evidence):
            warnings.append("Suspicious instructions in retrieved content were quarantined.")
        candidate_count = max(
            (int(item.metadata.get("candidate_document_count", 0)) for item in evidence),
            default=0,
        )
        retrieval_mode = (
            "dense_only"
            if any(item.metadata.get("retrieval_degraded") for item in evidence)
            else "hybrid"
        )
        return {
            "evidence": [item.model_dump(mode="json") for item in evidence],
            "warnings": warnings,
            "candidate_count": candidate_count,
            "selected_evidence_count": len(evidence),
            "retrieval_mode": retrieval_mode,
        }


def knowledge_search_spec(retrieval: RetrievalService, retries: int = 1) -> ToolSpec:
    return ToolSpec(
        name="knowledge_search",
        capability=Capability.KNOWLEDGE_SEARCH,
        input_model=KnowledgeSearchInput,
        handler=KnowledgeSearchTool(retrieval, retries),
        timeout_seconds=15,
    )
