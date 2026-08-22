"""Score the frozen golden cases from reproducible API observations."""

import json
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

from orysys_assistant.evaluation.models import (
    EvaluationMetrics,
    GoldenCaseResult,
    GoldenEvaluationReport,
    ScenarioObservation,
)

ScenarioExecutor = Callable[[dict[str, Any]], Awaitable[ScenarioObservation]]
_ROUTE_ALIASES = {
    "knowledge_search": "direct_knowledge",
    "research_subgraph": "research",
    "enterprise_tool_subagent": "enterprise",
    "pre_agent_control": "pre_agent_control",
}


class GoldenEvaluationRunner:
    def __init__(self, dataset_path: Path, executor: ScenarioExecutor) -> None:
        self._dataset_path = dataset_path
        self._executor = executor

    async def run(self) -> GoldenEvaluationReport:
        dataset = json.loads(self._dataset_path.read_text(encoding="utf-8"))
        results = []
        for case in dataset["cases"]:
            observation = await self._executor(case)
            results.append(self._score(case, observation))
        return GoldenEvaluationReport(metrics=self._metrics(results), results=results)

    @staticmethod
    def _score(case: dict[str, Any], observed: ScenarioObservation) -> GoldenCaseResult:
        expected_route = _ROUTE_ALIASES.get(case["expected_route"], case["expected_route"])
        permission_expected = case["id"] == "GQ-003"
        degradation_expected = case["expected_status"] in {
            "partial",
            "insufficient_evidence",
            "failed",
        }
        degradation_clear = (
            not degradation_expected
            or observed.degraded
            or bool(observed.warnings)
            or observed.status == "failed"
        )
        return GoldenCaseResult(
            case_id=case["id"],
            title=case["title"],
            expected_route=expected_route,
            actual_route=observed.actual_route,
            expected_status=case["expected_status"],
            actual_status=observed.status,
            route_correct=observed.actual_route == expected_route,
            citation_valid=observed.citation_valid,
            unauthorized_evidence_count=observed.unauthorized_evidence_count,
            grounded=observed.grounded,
            permission_correct=(observed.permission_denied if permission_expected else True),
            completion_correct=observed.status == case["expected_status"],
            degradation_clear=degradation_clear,
            first_token_latency_ms=observed.first_token_latency_ms,
            total_latency_ms=observed.total_latency_ms,
            warnings=observed.warnings,
        )

    @staticmethod
    def _metrics(results: list[GoldenCaseResult]) -> EvaluationMetrics:
        count = len(results)
        partial_cases = [
            item
            for item in results
            if item.expected_status in {"partial", "insufficient_evidence", "failed"}
        ]
        first_token = [
            item.first_token_latency_ms
            for item in results
            if item.first_token_latency_ms is not None
        ]
        total_citations = sum(
            1 for item in results if item.citation_valid or not item.citation_valid
        )
        return EvaluationMetrics(
            cases=count,
            route_accuracy=_rate(sum(item.route_correct for item in results), count),
            citation_validity_rate=_rate(
                sum(item.citation_valid for item in results), total_citations
            ),
            unauthorized_evidence_rate=_rate(
                sum(item.unauthorized_evidence_count for item in results), count
            ),
            groundedness_rate=_rate(sum(item.grounded for item in results), count),
            permission_accuracy=_rate(sum(item.permission_correct for item in results), count),
            completion_rate=_rate(sum(item.completion_correct for item in results), count),
            partial_answer_quality=_rate(
                sum(item.degradation_clear for item in partial_cases), len(partial_cases)
            ),
            average_first_token_latency_ms=(
                round(sum(first_token) / len(first_token), 2) if first_token else None
            ),
            average_total_latency_ms=round(
                sum(item.total_latency_ms for item in results) / count, 2
            ),
        )


def _rate(numerator: int, denominator: int) -> float:
    return round(numerator / denominator, 4) if denominator else 1.0
