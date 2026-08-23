import json
from pathlib import Path

import pytest

from orysys_assistant.evaluation.models import ScenarioObservation
from orysys_assistant.evaluation.runner import GoldenEvaluationRunner
from orysys_assistant.observability.activity import (
    project_activity_panel,
    sanitize_activity_metadata,
)

ROOT = Path(__file__).parents[2]


def test_activity_metadata_drops_prompts_secrets_and_raw_payloads() -> None:
    sanitized = sanitize_activity_metadata(
        {
            "candidate_count": 12,
            "retrieval_mode": "hybrid",
            "retrieval_filters": {
                "document_type": "incident",
                "namespace": "server-controlled",
            },
            "system_prompt": "hidden",
            "authorization": "Bearer secret",
            "raw_mcp_response": {"employee": "confidential"},
            "document_content": "restricted text",
        }
    )

    assert sanitized == {
        "candidate_count": 12,
        "retrieval_mode": "hybrid",
        "retrieval_filters": {"document_type": "incident"},
    }


def test_activity_projection_exposes_evaluator_summary_without_reasoning() -> None:
    events = [
        {
            "request_id": "trace-123",
            "event_type": "routing_completed",
            "status": "completed",
            "agent": "root_deep_agent",
            "node": "intent_routing",
            "metadata": {
                "plan_summary": "Run bounded research and validate findings.",
                "system_prompt": "must be removed",
            },
        },
        {
            "request_id": "trace-123",
            "event_type": "retrieval_completed",
            "status": "completed",
            "agent": "research_subagent",
            "node": "workers",
            "metadata": {
                "tool_name": "knowledge_search",
                "retrieval_mode": "hybrid",
                "candidate_count": 20,
                "selected_evidence_count": 6,
                "retrieval_filters": {"document_type": "incident"},
            },
        },
        {
            "request_id": "trace-123",
            "event_type": "research_node_completed",
            "status": "completed",
            "agent": "research_subagent",
            "node": "planner",
            "metadata": {
                "todos": [
                    {
                        "id": "initial-1",
                        "content": "Trace the PAY-1224 root-cause timeline.",
                        "status": "pending",
                        "hidden_reasoning": "must be removed",
                    }
                ]
            },
        },
        {
            "request_id": "trace-123",
            "event_type": "research_node_completed",
            "status": "completed",
            "agent": "research_subagent",
            "node": "worker:initial-1",
            "metadata": {
                "todo_id": "initial-1",
                "todo_content": "Trace the PAY-1224 root-cause timeline.",
                "todo_status": "completed",
            },
        },
        {
            "request_id": "trace-123",
            "event_type": "validation_failed",
            "status": "degraded",
            "node": "output_validation",
            "metadata": {"repair_attempted": True},
        },
    ]

    state = project_activity_panel(events)

    assert state.request_id == "trace-123"
    assert state.current_agent == "research_subagent"
    assert state.current_node == "output_validation"
    assert state.tool_name == "knowledge_search"
    assert state.candidate_count == 20
    assert state.selected_evidence_count == 6
    assert state.validation_status == "degraded"
    assert state.degraded is True
    assert state.research_todos == [
        {
            "id": "initial-1",
            "content": "Trace the PAY-1224 root-cause timeline.",
            "status": "completed",
        }
    ]
    assert "system_prompt" not in state.plan_summary


@pytest.mark.asyncio
async def test_golden_runner_scores_all_metrics_deterministically(tmp_path: Path) -> None:
    dataset = {
        "cases": [
            {
                "id": "GQ-001",
                "title": "Grounded",
                "expected_route": "knowledge_search",
                "expected_status": "complete",
            },
            {
                "id": "GQ-003",
                "title": "Denied",
                "expected_route": "enterprise_tool_subagent",
                "expected_status": "insufficient_evidence",
            },
        ]
    }
    path = tmp_path / "golden.json"
    path.write_text(json.dumps(dataset), encoding="utf-8")

    async def execute(case: dict[str, object]) -> ScenarioObservation:
        denied = case["id"] == "GQ-003"
        return ScenarioObservation(
            actual_route="enterprise" if denied else "direct_knowledge",
            status="insufficient_evidence" if denied else "complete",
            answer="Safe result",
            warnings=["Denied safely"] if denied else [],
            degraded=denied,
            permission_denied=denied,
            total_latency_ms=10,
        )

    report = await GoldenEvaluationRunner(path, execute).run()

    assert report.metrics.route_accuracy == 1
    assert report.metrics.citation_validity_rate == 1
    assert report.metrics.unauthorized_evidence_rate == 0
    assert report.metrics.permission_accuracy == 1
    assert report.metrics.completion_rate == 1
    assert report.metrics.partial_answer_quality == 1


def test_stored_golden_report_meets_phase8_security_exit_criteria() -> None:
    report = json.loads(
        (ROOT / "data" / "golden_evaluation_report.json").read_text(encoding="utf-8")
    )

    assert report["metrics"]["cases"] == 10
    assert report["metrics"]["citation_validity_rate"] == 1
    assert report["metrics"]["unauthorized_evidence_rate"] == 0
    assert report["metrics"]["route_accuracy"] == 1
    assert report["metrics"]["completion_rate"] == 1
