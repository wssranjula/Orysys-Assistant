"""Typed routing, transition, subagent, and root-output contracts."""

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from orysys_assistant.domain.models import Citation, ResponseStatus
from orysys_assistant.retrieval.models import Evidence


class AgentModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class AgentRoute(StrEnum):
    DIRECT_KNOWLEDGE = "direct_knowledge"
    RESEARCH = "research"
    ANALYSIS = "analysis"
    ENTERPRISE = "enterprise"
    OUT_OF_SCOPE = "out_of_scope"


class AgentTransition(AgentModel):
    event_type: str
    agent: str
    node: str
    status: str
    message: str
    metadata: dict[str, Any] = Field(default_factory=dict)


class AnalysisResult(AgentModel):
    operation: str
    rows_processed: int
    results: list[dict[str, Any]]
    warnings: list[str] = Field(default_factory=list)


class AgentExecutionResult(AgentModel):
    route: AgentRoute
    answer: str
    status: ResponseStatus = ResponseStatus.COMPLETE
    citations: list[Citation] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    evidence_ids: list[str] = Field(default_factory=list)
    evidence: list[Evidence] = Field(default_factory=list)


class AnswerToken(AgentModel):
    """One provisional slice of generated prose.

    Emitted while the response agent is still writing, so the API can forward it
    before the turn is validated.  The terminal response stays authoritative.
    """

    text: str = Field(min_length=1, max_length=8_000)
