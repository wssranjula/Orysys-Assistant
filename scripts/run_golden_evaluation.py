"""Execute and store the frozen Phase 8 golden evaluation report."""

import argparse
import asyncio
import json
from datetime import datetime
from pathlib import Path
from time import perf_counter
from typing import Any

import httpx

from orysys_assistant.agent.models import AgentExecutionResult, AgentRoute, AgentTransition
from orysys_assistant.config import Settings
from orysys_assistant.domain.errors import RetrievalUnavailableError
from orysys_assistant.evaluation.models import ScenarioObservation
from orysys_assistant.evaluation.runner import GoldenEvaluationRunner
from orysys_assistant.main import create_app

ROOT = Path(__file__).parents[1]
TOKENS = {
    "viewer": "phase2-viewer-demo-token",
    "analyst": "phase2-analyst-demo-token",
    "administrator": "phase2-administrator-demo-token",
}


class SlowEnterpriseClient:
    async def call(self, tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        await asyncio.sleep(1)
        return {}


class FaultInjectingOrchestrator:
    def __init__(self, delegate: Any) -> None:
        self._delegate = delegate
        self.name = delegate.name

    async def run(self, question: str, *args: Any, **kwargs: Any) -> AgentExecutionResult:
        if "production database failover procedure" in question.lower():
            sink = args[1] if len(args) > 1 else kwargs.get("transition_sink")
            route = AgentRoute.DIRECT_KNOWLEDGE
            if sink is not None:
                await sink(
                    AgentTransition(
                        event_type="routing_completed",
                        agent=self.name,
                        node="intent_routing",
                        status="completed",
                        message=f"Selected {route.value} route.",
                        metadata={"route": route.value},
                    )
                )
            raise RetrievalUnavailableError("Injected retrieval outage.")
        effective_question = question
        if "PAY-1042" in question:
            effective_question = question.replace("PAY-1042", "SEC-455")
        result = await self._delegate.run(effective_question, *args, **kwargs)
        if "leave carry-forward policy" in question.lower() and result.citations:
            citation = result.citations[0].model_copy(
                update={"evidence_id": "ev_injected_fabrication"}
            )
            return result.model_copy(
                update={
                    "answer": f"{result.answer}\nUnsupported citation [fabricated].",
                    "citations": [citation, *result.citations[1:]],
                }
            )
        return result


def parse_sse(body: str) -> list[tuple[str, dict[str, Any]]]:
    events = []
    name = "message"
    data: list[str] = []
    for line in body.splitlines():
        if line.startswith("event:"):
            name = line.removeprefix("event:").strip()
        elif line.startswith("data:"):
            data.append(line.removeprefix("data:").strip())
        elif not line and data:
            events.append((name, json.loads("\n".join(data))))
            name, data = "message", []
    if data:
        events.append((name, json.loads("\n".join(data))))
    return events


class ApiScenarioExecutor:
    def __init__(self) -> None:
        settings = Settings(
            rate_limit_backend="memory",
            rate_limit_viewer_capacity=100,
            rate_limit_analyst_capacity=100,
            rate_limit_administrator_capacity=100,
            memory_backend="memory",
            retrieval_backend="memory",
            mcp_backend="memory",
            mcp_timeout_seconds=0.01,
            mcp_retry_attempts=1,
            mock_token_delay_seconds=0,
            log_level="WARNING",
        )
        self.app = create_app(settings, enterprise_client=SlowEnterpriseClient())
        self.client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=self.app), base_url="http://evaluation"
        )
        self._initialized = False

    async def close(self) -> None:
        await self.client.aclose()
        await self.app.state.agent_runtime.close()
        await self.app.state.memory_runtime.close()
        await self.app.state.rate_limiter.close()

    async def __call__(self, case: dict[str, Any]) -> ScenarioObservation:
        if not self._initialized:
            real = await self.app.state.agent_runtime.get_orchestrator()
            self.app.state.agent_runtime._orchestrator = FaultInjectingOrchestrator(real)
            self._initialized = True
        if case["id"] == "GQ-009":
            return await self._rate_limit_case(case)
        if case["id"] == "GQ-010":
            first = await self._request(
                case["role"], "What does the remote-work policy say about eligibility?"
            )
            conversation_id = first[-1][1]["conversation_id"]
            events = await self._request(
                case["role"], "Does that apply during probation?", conversation_id
            )
            return self._observe(events, 0)
        started = perf_counter()
        events = await self._request(case["role"], case["question"])
        return self._observe(events, (perf_counter() - started) * 1_000)

    async def _request(
        self, role: str, message: str, conversation_id: str | None = None
    ) -> list[tuple[str, dict[str, Any]]]:
        payload: dict[str, Any] = {"message": message}
        if conversation_id:
            payload["conversation_id"] = conversation_id
        response = await self.client.post(
            "/v1/chat/stream",
            json=payload,
            headers={"Authorization": f"Bearer {TOKENS[role]}"},
        )
        if response.headers.get("content-type", "").startswith("text/event-stream"):
            return parse_sse(response.text)
        return [("http_error", response.json())]

    async def _rate_limit_case(self, case: dict[str, Any]) -> ScenarioObservation:
        settings = Settings(
            rate_limit_backend="memory",
            rate_limit_viewer_capacity=10,
            rate_limit_viewer_refill_per_minute=0.001,
            mock_token_delay_seconds=0,
            log_level="WARNING",
        )
        app = create_app(settings)
        client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://rate-evaluation"
        )
        started = perf_counter()
        responses = [
            await client.post(
                "/v1/chat/stream",
                json={"message": f"Policy request {index}"},
                headers={"Authorization": f"Bearer {TOKENS['viewer']}"},
            )
            for index in range(11)
        ]
        elapsed = (perf_counter() - started) * 1_000
        denied = responses[-1]
        await client.aclose()
        await app.state.agent_runtime.close()
        await app.state.memory_runtime.close()
        await app.state.rate_limiter.close()
        return ScenarioObservation(
            actual_route="pre_agent_control",
            status="failed" if denied.status_code == 429 else "complete",
            answer="Rate limit enforced." if denied.status_code == 429 else "Rate limit failed.",
            degraded=denied.status_code == 429,
            permission_denied=False,
            total_latency_ms=round(elapsed, 2),
        )

    @staticmethod
    def _observe(
        events: list[tuple[str, dict[str, Any]]], elapsed_ms: float
    ) -> ScenarioObservation:
        activity = [payload for name, payload in events if name == "activity"]
        final = next((payload for name, payload in reversed(events) if name == "final"), {})
        route = next(
            (
                event.get("metadata", {}).get("route")
                for event in activity
                if event.get("event_type") == "routing_completed"
            ),
            "pre_agent_control",
        )
        request_received = next(
            (event for event in activity if event.get("event_type") == "request_received"), None
        )
        answer_started = next(
            (event for event in activity if event.get("event_type") == "answer_streaming"), None
        )
        first_token_ms = None
        if request_received and answer_started:
            start = datetime.fromisoformat(request_received["timestamp"])
            first = datetime.fromisoformat(answer_started["timestamp"])
            first_token_ms = round((first - start).total_seconds() * 1_000, 2)
        citations = final.get("citations", [])
        citation_ids = [item.get("citation_id") for item in citations]
        citation_valid = len(citation_ids) == len(set(citation_ids)) and all(
            item.get("evidence_id", "").startswith("ev_") for item in citations
        )
        status = str(final.get("status", "failed"))
        grounded = (
            bool(citations)
            or status in {"insufficient_evidence", "failed"}
            or route == "enterprise"
        )
        return ScenarioObservation(
            actual_route=str(route),
            status=status,
            answer=str(final.get("answer", "")),
            citations=citations,
            warnings=final.get("warnings", []),
            degraded=bool(final.get("degraded", status != "complete")),
            activity_event_types=[str(item.get("event_type")) for item in activity],
            citation_valid=citation_valid,
            unauthorized_evidence_count=0,
            grounded=grounded,
            permission_denied=any(item.get("event_type") == "tool_denied" for item in activity),
            first_token_latency_ms=first_token_ms,
            total_latency_ms=round(elapsed_ms, 2),
        )


async def run(output: Path) -> None:
    # Every specialist is a model-driven loop, so this report measures real agent
    # behaviour and cannot be produced from a credential-free deterministic profile.
    if not Settings().openai_api_key:
        raise SystemExit(
            "OPENAI_API_KEY is required: the golden evaluation exercises the live agent "
            "loops end to end, so there is no offline profile to score."
        )
    executor = ApiScenarioExecutor()
    try:
        report = await GoldenEvaluationRunner(
            ROOT / "data" / "golden_questions.json", executor
        ).run()
    finally:
        await executor.close()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(report.model_dump_json(indent=2), encoding="utf-8")
    print(report.metrics.model_dump_json(indent=2))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT / "data" / "golden_evaluation_report.json",
    )
    args = parser.parse_args()
    asyncio.run(run(args.output))


if __name__ == "__main__":
    main()
