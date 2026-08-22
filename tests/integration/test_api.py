import json
from collections.abc import Iterator
from typing import Any
from uuid import UUID, uuid4

import httpx
import pytest

from orysys_assistant.config import Settings
from orysys_assistant.main import create_app


@pytest.fixture
def app() -> Any:
    return create_app(
        Settings(
            langsmith_tracing=False,
            mock_token_delay_seconds=0,
            log_level="WARNING",
            _env_file=None,
        )
    )


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
    assert ready.json()["components"] == {"mock_agent": "ready"}
    UUID(live.headers["X-Request-ID"])


@pytest.mark.asyncio
async def test_invalid_chat_request_uses_error_contract(client: httpx.AsyncClient) -> None:
    response = await client.post("/v1/chat/stream", json={"message": "   "})

    assert response.status_code == 400
    payload = response.json()
    assert payload["error"]["code"] == "invalid_request"
    assert payload["error"]["retryable"] is False
    assert payload["error"]["request_id"] == response.headers["X-Request-ID"]
    assert "input" not in json.dumps(payload)


@pytest.mark.asyncio
async def test_chat_stream_separates_activity_tokens_and_final(client: httpx.AsyncClient) -> None:
    response = await client.post("/v1/chat/stream", json={"message": "Show me the policy"})

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
    conversation = await client.get(f"/v1/conversations/{conversation_id}")
    feedback = await client.post(
        "/v1/feedback",
        json={
            "conversation_id": str(conversation_id),
            "response_id": str(uuid4()),
            "rating": 1,
        },
    )

    assert conversation.status_code == 200
    assert conversation.json()["persistence"] == "not_available_in_phase_1"
    assert feedback.status_code == 202
    assert feedback.json() == {
        "accepted": True,
        "persistence": "not_available_in_phase_1",
    }
