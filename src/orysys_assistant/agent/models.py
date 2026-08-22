"""Typed routing, transition, subagent, and root-output contracts."""

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from orysys_assistant.domain.models import Citation
from orysys_assistant.retrieval.models import Evidence


class AgentModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class AgentRoute(StrEnum):
    DIRECT_KNOWLEDGE = "direct_knowledge"
    RESEARCH = "research"
    ANALYSIS = "analysis"
    ENTERPRISE = "enterprise"


class AgentTransition(AgentModel):
    event_type: str
    agent: str
    node: str
    status: str
    message: str
    metadata: dict[str, Any] = Field(default_factory=dict)


class Finding(AgentModel):
    claim: str
    evidence_ids: list[str]
    occurrence_count: int | None = None


class ResearchResult(AgentModel):
    summary: str
    findings: list[Finding]
    evidence_ids: list[str]
    unresolved_questions: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    partial: bool = False


class AnalysisResult(AgentModel):
    operation: str
    rows_processed: int
    results: list[dict[str, Any]]
    warnings: list[str] = Field(default_factory=list)


class EnterpriseToolResult(AgentModel):
    tool_name: str
    data: dict[str, Any]
    source: str
    warnings: list[str] = Field(default_factory=list)


class ResearchExecution(AgentModel):
    result: ResearchResult
    evidence: list[Evidence]


class AnalysisExecution(AgentModel):
    result: AnalysisResult
    evidence: list[Evidence]


class EnterpriseExecution(AgentModel):
    result: EnterpriseToolResult


class AgentExecutionResult(AgentModel):
    route: AgentRoute
    answer: str
    citations: list[Citation] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    evidence_ids: list[str] = Field(default_factory=list)

