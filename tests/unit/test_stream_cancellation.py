from types import SimpleNamespace
from uuid import uuid4

import pytest

from orysys_assistant.api.routes.chat import stream_chat_events
from orysys_assistant.config import Settings
from orysys_assistant.domain.models import ChatRequest


class DisconnectingRequest:
    def __init__(self) -> None:
        self.state = SimpleNamespace(request_id=uuid4())
        self.checks = 0

    async def is_disconnected(self) -> bool:
        self.checks += 1
        return self.checks >= 2


@pytest.mark.asyncio
async def test_stream_stops_without_final_event_after_disconnect() -> None:
    request = DisconnectingRequest()
    settings = Settings(langsmith_tracing=False, mock_token_delay_seconds=0, _env_file=None)

    events = [
        event
        async for event in stream_chat_events(
            request,  # type: ignore[arg-type]
            ChatRequest(message="disconnect this stream"),
            settings,
        )
    ]

    assert request.checks == 2
    assert all(event["event"] != "final" for event in events)
