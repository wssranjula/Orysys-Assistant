"""Serializable observations, case scores, and aggregate evaluation report."""

from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class EvaluationModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ScenarioObservation(EvaluationModel):
    actual_route: str
    status: str
    answer: str
    citations: list[dict[str, Any]] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    degraded: bool = False
    activity_event_types: list[str] = Field(default_factory=list)
    citation_valid: bool = True
    unauthorized_evidence_count: int = 0
    grounded: bool = True
    permission_denied: bool = False
    first_token_latency_ms: float | None = None
    total_latency_ms: float


class GoldenCaseResult(EvaluationModel):
    case_id: str
    title: str
    expected_route: str
    actual_route: str
    expected_status: str
    actual_status: str
    route_correct: bool
    citation_valid: bool
    unauthorized_evidence_count: int
    grounded: bool
    permission_correct: bool
    completion_correct: bool
    degradation_clear: bool
    first_token_latency_ms: float | None = None
    total_latency_ms: float
    warnings: list[str] = Field(default_factory=list)


class EvaluationMetrics(EvaluationModel):
    cases: int
    route_accuracy: float
    citation_validity_rate: float
    unauthorized_evidence_rate: float
    groundedness_rate: float
    permission_accuracy: float
    completion_rate: float
    partial_answer_quality: float
    average_first_token_latency_ms: float | None
    average_total_latency_ms: float


class GoldenEvaluationReport(EvaluationModel):
    schema_version: str = "1.0"
    generated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    corpus: str = "data/golden_questions.json"
    runtime: str = "offline deterministic API with explicit fault injection"
    metrics: EvaluationMetrics
    results: list[GoldenCaseResult]
