import json
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any
from uuid import UUID, uuid4

import httpx
import pytest

from orysys_assistant.agent.models import AgentExecutionResult, AgentRoute, AgentTransition
from orysys_assistant.api.routes import chat as chat_routes
from orysys_assistant.config import Settings
from orysys_assistant.domain.models import Citation
from orysys_assistant.main import create_app
from orysys_assistant.retrieval.models import Evidence

TOKENS = {
    "viewer": "phase2-viewer-demo-token",
    "analyst": "phase2-analyst-demo-token",
    "administrator": "phase2-administrator-demo-token",
}


class FakeOrchestrator:
    name = "test_root_agent"

    def __init__(self) -> None:
        self.calls = 0
        self.summaries: list[str] = []
        self.thread_ids: list[str | None] = []

    async def run(
        self,
        message: str,
        context: Any,
        transition_sink: Any = None,
        conversation_summary: str = "",
        thread_id: str | None = None,
    ) -> Any:
        self.calls += 1
        self.summaries.append(conversation_summary)
        self.thread_ids.append(thread_id)
        if transition_sink is not None:
            await transition_sink(
                AgentTransition(
                    event_type="routing_completed",
                    agent=self.name,
                    node="intent_routing",
                    status="completed",
                    message="Selected direct knowledge route.",
                    metadata={
                        "route": "direct_knowledge",
                        "plan_summary": "Search authorized evidence and validate citations.",
                        "raw_mcp_response": {"secret": "must not leave API"},
                    },
                )
            )
            await transition_sink(
                AgentTransition(
                    event_type="retrieval_completed",
                    agent=self.name,
                    node="knowledge_search",
                    status="completed",
                    message="Retrieved fixture evidence.",
                    metadata={
                        "candidate_count": 4,
                        "selected_evidence_count": 1,
                        "retrieval_mode": "hybrid",
                    },
                )
            )
        evidence = Evidence(
            evidence_id="ev_test_policy",
            document_id="policy-test-001",
            chunk_id="policy-test-001:purpose:0000",
            title="Test Policy",
            content="Fixture evidence for the integration test.",
            metadata={
                "access_level": "internal",
                "source_path": "fixtures/policy-test-001.md",
            },
            final_score=1.0,
        )
        return AgentExecutionResult(
            route=AgentRoute.DIRECT_KNOWLEDGE,
            answer=f"Grounded test answer for: {message} [1]",
            citations=[
                Citation(
                    citation_id="1",
                    evidence_id=evidence.evidence_id,
                    document_id=evidence.document_id,
                    title=evidence.title,
                    chunk_id=evidence.chunk_id,
                    source_path=str(evidence.metadata["source_path"]),
                )
            ],
            warnings=["Test runtime uses fixture evidence."],
            evidence_ids=[evidence.evidence_id],
            evidence=[evidence],
        )


class FakeAgentRuntime:
    def __init__(self) -> None:
        self.orchestrator = FakeOrchestrator()

    async def get_orchestrator(self) -> FakeOrchestrator:
        return self.orchestrator


def install_fake_agent(app: Any) -> FakeAgentRuntime:
    runtime = FakeAgentRuntime()
    app.state.agent_runtime = runtime
    return runtime


def auth_headers(role: str = "viewer") -> dict[str, str]:
    return {"Authorization": f"Bearer {TOKENS[role]}"}


@pytest.fixture
def app() -> Any:
    application = create_app(
        Settings(
            langsmith_tracing=False,
            mock_token_delay_seconds=0,
            log_level="WARNING",
            rate_limit_backend="memory",
            _env_file=None,
        )
    )
    install_fake_agent(application)
    return application


@pytest.fixture
async def client(app: Any) -> Iterator[httpx.AsyncClient]:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as test_client:
        yield test_client


def parse_sse(body: str) -> list[tuple[str, dict[str, Any]]]:
    parsed: list[tuple[str, dict[str, Any]]] = []
    event_name = "message"
    data_lines: list[str] = []
    for line in body.splitlines():
        if line.startswith("event:"):
            event_name = line.removeprefix("event:").strip()
        elif line.startswith("data:"):
            data_lines.append(line.removeprefix("data:").strip())
        elif not line and data_lines:
            parsed.append((event_name, json.loads("\n".join(data_lines))))
            event_name = "message"
            data_lines = []
    if data_lines:
        parsed.append((event_name, json.loads("\n".join(data_lines))))
    return parsed


@pytest.mark.asyncio
async def test_health_endpoints(client: httpx.AsyncClient) -> None:
    live = await client.get("/health/live")
    ready = await client.get("/health/ready")

    assert live.status_code == 200
    assert live.json() == {"status": "ok", "components": {}}
    assert ready.status_code == 200
    assert ready.json()["components"] == {
        "root_agent": "ready",
        "rate_limiter": "ready",
    }
    UUID(live.headers["X-Request-ID"])


@pytest.mark.asyncio
async def test_invalid_chat_request_uses_error_contract(client: httpx.AsyncClient) -> None:
    response = await client.post("/v1/chat/stream", json={"message": "   "}, headers=auth_headers())

    assert response.status_code == 400
    payload = response.json()
    assert payload["error"]["code"] == "invalid_request"
    assert payload["error"]["retryable"] is False
    assert payload["error"]["request_id"] == response.headers["X-Request-ID"]
    assert "input" not in json.dumps(payload)


@pytest.mark.asyncio
async def test_chat_stream_separates_activity_tokens_and_final(client: httpx.AsyncClient) -> None:
    response = await client.post(
        "/v1/chat/stream",
        json={"message": "Show me the policy"},
        headers=auth_headers(),
    )

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    events = parse_sse(response.text)
    event_names = [name for name, _ in events]
    assert "activity" in event_names
    assert "answer_delta" in event_names
    assert event_names[-1] == "final"

    final = events[-1][1]
    deltas = [event["text"] for name, event in events if name == "answer_delta"]
    assert "".join(deltas) == final["answer"]
    assert final["status"] == "complete"
    assert final["request_id"] == response.headers["X-Request-ID"]
    assert final["warnings"]


@pytest.mark.asyncio
async def test_chat_stream_uses_explicit_langsmith_configuration(
    client: httpx.AsyncClient,
    app: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = app.state.settings
    settings.langsmith_tracing = True
    settings.langsmith_api_key = "test-langsmith-key"
    settings.langsmith_endpoint = "https://langsmith.test"
    settings.langsmith_project = "test-project"
    fake_client = object()
    client_arguments: dict[str, str] = {}
    tracing_arguments: dict[str, Any] = {}

    def fake_client_factory(api_key: str, api_url: str) -> object:
        client_arguments.update(api_key=api_key, api_url=api_url)
        return fake_client

    @contextmanager
    def fake_tracing_context(**kwargs: Any) -> Iterator[None]:
        tracing_arguments.update(kwargs)
        yield

    monkeypatch.setattr(chat_routes, "get_langsmith_client", fake_client_factory)
    monkeypatch.setattr(chat_routes, "tracing_context", fake_tracing_context)

    response = await client.post(
        "/v1/chat/stream",
        json={"message": "Show me the leave policy"},
        headers=auth_headers(),
    )

    assert response.status_code == 200
    assert client_arguments == {
        "api_key": "test-langsmith-key",
        "api_url": "https://langsmith.test",
    }
    assert tracing_arguments["enabled"] is True
    assert tracing_arguments["client"] is fake_client
    assert tracing_arguments["project_name"] == "test-project"
    assert tracing_arguments["metadata"]["agent_name"] == "test_root_agent"


@pytest.mark.asyncio
async def test_placeholder_conversation_and_feedback_contracts(
    client: httpx.AsyncClient,
) -> None:
    conversation_id = uuid4()
    conversation = await client.get(f"/v1/conversations/{conversation_id}", headers=auth_headers())
    feedback = await client.post(
        "/v1/feedback",
        json={
            "conversation_id": str(conversation_id),
            "response_id": str(uuid4()),
            "rating": 1,
        },
        headers=auth_headers(),
    )

    assert conversation.status_code == 200
    assert conversation.json()["persistence"] == "in_memory"
    assert feedback.status_code == 202
    assert feedback.json() == {
        "accepted": True,
        "persistence": "not_available_in_phase_1",
    }


@pytest.mark.asyncio
async def test_multi_turn_memory_survives_requests_and_enforces_owner(
    client: httpx.AsyncClient, app: Any
) -> None:
    first = await client.post(
        "/v1/chat/stream",
        json={"message": "First memory turn"},
        headers=auth_headers("analyst"),
    )
    first_final = parse_sse(first.text)[-1][1]
    conversation_id = first_final["conversation_id"]
    second = await client.post(
        "/v1/chat/stream",
        json={"message": "Use that context", "conversation_id": conversation_id},
        headers=auth_headers("analyst"),
    )
    snapshot = await client.get(
        f"/v1/conversations/{conversation_id}", headers=auth_headers("analyst")
    )
    denied = await client.get(
        f"/v1/conversations/{conversation_id}", headers=auth_headers("viewer")
    )

    assert second.status_code == 200
    assert app.state.agent_runtime.orchestrator.summaries[0] == ""
    assert "First memory turn" in app.state.agent_runtime.orchestrator.summaries[1]
    assert app.state.agent_runtime.orchestrator.thread_ids[0].endswith(conversation_id)
    assert len(snapshot.json()["messages"]) == 4
    assert snapshot.json()["persistence"] == "in_memory"
    assert denied.status_code == 403


@pytest.mark.asyncio
async def test_login_and_missing_token_contract(client: httpx.AsyncClient, app: Any) -> None:
    runtime = app.state.agent_runtime
    login = await client.post(
        "/v1/auth/token",
        json={
            "username": "viewer@commercialbank.test",
            "password": "ViewerDemo!2026",
        },
    )
    unauthenticated = await client.post("/v1/chat/stream", json={"message": "hello"})

    assert login.status_code == 200
    assert login.json()["access_token"] == TOKENS["viewer"]
    assert login.json()["role"] == "viewer"
    assert unauthenticated.status_code == 401
    assert unauthenticated.json()["error"]["code"] == "authentication_failed"
    assert "text/event-stream" not in unauthenticated.headers.get("content-type", "")
    assert runtime.orchestrator.calls == 0


@pytest.mark.asyncio
async def test_bad_password_uses_generic_authentication_error(client: httpx.AsyncClient) -> None:
    response = await client.post(
        "/v1/auth/token",
        json={
            "username": "viewer@commercialbank.test",
            "password": "incorrect-password",
        },
    )

    assert response.status_code == 401
    assert response.json()["error"]["message"] == "Invalid username or password."


@pytest.mark.asyncio
async def test_rate_limit_returns_429_and_retry_after() -> None:
    settings = Settings(
        rate_limit_backend="memory",
        rate_limit_viewer_capacity=1,
        rate_limit_viewer_refill_per_minute=0.01,
        mock_token_delay_seconds=0,
        langsmith_tracing=False,
        log_level="WARNING",
        _env_file=None,
    )
    app = create_app(settings)
    runtime = install_fake_agent(app)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as limited_client:
        first = await limited_client.post(
            "/v1/chat/stream", json={"message": "first"}, headers=auth_headers()
        )
        second = await limited_client.post(
            "/v1/chat/stream", json={"message": "second"}, headers=auth_headers()
        )

    assert first.status_code == 200
    assert second.status_code == 429
    assert int(second.headers["Retry-After"]) > 0
    assert second.json()["error"]["code"] == "rate_limit_exceeded"
    assert runtime.orchestrator.calls == 1


@pytest.mark.asyncio
async def test_fabricated_citation_is_not_streamed_or_persisted(
    client: httpx.AsyncClient, app: Any
) -> None:
    class FabricatedCitationOrchestrator(FakeOrchestrator):
        async def run(self, *args: Any, **kwargs: Any) -> AgentExecutionResult:
            result = await super().run(*args, **kwargs)
            fabricated = result.citations[0].model_copy(
                update={"evidence_id": "ev_not_in_request_ledger"}
            )
            return result.model_copy(
                update={
                    "answer": "A fabricated policy statement. [1]",
                    "citations": [fabricated],
                }
            )

    app.state.agent_runtime.orchestrator = FabricatedCitationOrchestrator()
    response = await client.post(
        "/v1/chat/stream",
        json={"message": "Explain the leave policy"},
        headers=auth_headers(),
    )
    events = parse_sse(response.text)
    final = events[-1][1]
    activity = [event for name, event in events if name == "activity"]

    assert final["status"] == "insufficient_evidence"
    assert final["citations"] == []
    assert "fabricated policy" not in final["answer"].lower()
    assert any(event["event_type"] == "validation_failed" for event in activity)

    snapshot = await client.get(
        f"/v1/conversations/{final['conversation_id']}", headers=auth_headers()
    )
    assert "fabricated policy" not in json.dumps(snapshot.json()).lower()


@pytest.mark.asyncio
async def test_activity_stream_has_one_trace_and_allowlisted_metadata(
    client: httpx.AsyncClient,
) -> None:
    response = await client.post(
        "/v1/chat/stream",
        json={"message": "Show observable policy retrieval"},
        headers=auth_headers(),
    )
    events = parse_sse(response.text)
    activity = [payload for name, payload in events if name == "activity"]
    final = events[-1][1]

    assert {event["request_id"] for event in activity} == {final["request_id"]}
    assert any(event["event_type"] == "access_scope_built" for event in activity)
    routing = next(event for event in activity if event["event_type"] == "routing_completed")
    retrieval = next(event for event in activity if event["event_type"] == "retrieval_completed")
    assert routing["metadata"]["plan_summary"].startswith("Search authorized")
    assert "raw_mcp_response" not in routing["metadata"]
    assert retrieval["metadata"] == {
        "candidate_count": 4,
        "selected_evidence_count": 1,
        "retrieval_mode": "hybrid",
    }


@pytest.mark.asyncio
async def test_end_to_end_api_agent_retrieval_for_each_role() -> None:
    settings = Settings(
        rate_limit_backend="memory",
        memory_backend="memory",
        retrieval_backend="memory",
        mcp_backend="memory",
        mock_token_delay_seconds=0,
        log_level="WARNING",
        _env_file=None,
    )
    application = create_app(settings)
    transport = httpx.ASGITransport(app=application)
    scenarios = {
        "viewer": "What does the remote-work policy allow?",
        "analyst": "Count incidents by document type.",
        "administrator": "Explain the restricted fraud investigation playbook.",
    }
    async with httpx.AsyncClient(transport=transport, base_url="http://e2e") as end_to_end_client:
        finals = {}
        for role, question in scenarios.items():
            response = await end_to_end_client.post(
                "/v1/chat/stream",
                json={"message": question},
                headers=auth_headers(role),
            )
            finals[role] = parse_sse(response.text)[-1][1]

    await application.state.agent_runtime.close()
    await application.state.memory_runtime.close()
    await application.state.rate_limiter.close()

    assert all(result["status"] in {"complete", "partial"} for result in finals.values())
    assert all(result["citations"] for result in finals.values())
