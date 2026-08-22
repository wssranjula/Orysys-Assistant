"""Role-gated knowledge search tool backed by the hybrid retrieval service."""

from datetime import date
from typing import Any, cast

from pydantic import BaseModel, ConfigDict, Field

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
    def __init__(self, retrieval: RetrievalService) -> None:
        self._retrieval = retrieval

    async def __call__(
        self,
        parameters: BaseModel,
        context: TrustedRequestContext,
    ) -> dict[str, Any]:
        request = cast(KnowledgeSearchInput, parameters)
        evidence = await self._retrieval.search(
            request.query,
            context.access_scope,
            SearchFilters(
                department=request.department,
                document_type=request.document_type,
                created_after=request.created_after,
                created_before=request.created_before,
            ),
            top_k=request.top_k,
        )
        return {"evidence": [item.model_dump(mode="json") for item in evidence]}


def knowledge_search_spec(retrieval: RetrievalService) -> ToolSpec:
    return ToolSpec(
        name="knowledge_search",
        capability=Capability.KNOWLEDGE_SEARCH,
        input_model=KnowledgeSearchInput,
        handler=KnowledgeSearchTool(retrieval),
        timeout_seconds=15,
    )
