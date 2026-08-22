from types import SimpleNamespace
from uuid import uuid4

import pytest

from orysys_assistant.api.routes.chat import stream_chat_events
from orysys_assistant.config import Settings
from orysys_assistant.domain.models import ChatRequest, Role
from orysys_assistant.security.models import AccessScope, TrustedRequestContext, UserIdentity


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
    context = TrustedRequestContext(
        identity=UserIdentity(
            user_id="user-viewer-01",
            username="viewer@commercialbank.test",
            display_name="Vina Perera",
            role=Role.VIEWER,
            department="retail-banking",
        ),
        access_scope=AccessScope(
            organization_id="commercial-bank",
            namespace="commercial-bank",
            allowed_access_levels=("internal",),
            allowed_departments=("retail-banking", "all-employees"),
        ),
        rate_limit_remaining=9,
    )

    events = [
        event
        async for event in stream_chat_events(
            request,  # type: ignore[arg-type]
            ChatRequest(message="disconnect this stream"),
            settings,
            context,
        )
    ]

    assert request.checks == 2
    assert all(event["event"] != "final" for event in events)
