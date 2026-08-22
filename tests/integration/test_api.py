import json
from collections.abc import Iterator
from typing import Any
from uuid import UUID, uuid4

import httpx
import pytest

from orysys_assistant.agent.models import AgentExecutionResult, AgentRoute
from orysys_assistant.config import Settings
from orysys_assistant.main import create_app

TOKENS = {
    "viewer": "phase2-viewer-demo-token",
    "analyst": "phase2-analyst-demo-token",
    "administrator": "phase2-administrator-demo-token",
}


class FakeOrchestrator:
    name = "test_root_agent"

    def __init__(self) -> None:
        self.calls = 0

    async def run(self, message: str, context: Any, transition_sink: Any = None) -> Any:
        self.calls += 1
        return AgentExecutionResult(
            route=AgentRoute.DIRECT_KNOWLEDGE,
            answer=f"Grounded test answer for: {message}",
            warnings=["Test runtime uses fixture evidence."],
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
    assert conversation.json()["persistence"] == "not_available_in_phase_1"
    assert feedback.status_code == 202
    assert feedback.json() == {
        "accepted": True,
        "persistence": "not_available_in_phase_1",
    }


@pytest.mark.asyncio
async def test_login_and_missing_token_contract(
    client: httpx.AsyncClient, app: Any
) -> None:
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
