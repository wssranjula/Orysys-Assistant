"""Server-Sent Event chat stream for the Phase 1 mock agent."""

import asyncio
import json
import re
from collections.abc import AsyncIterator
from time import perf_counter
from uuid import UUID, uuid4

import structlog
from fastapi import APIRouter, Request
from sse_starlette.sse import EventSourceResponse

from orysys_assistant.api.dependencies import SettingsDependency
from orysys_assistant.config import Settings
from orysys_assistant.domain.models import (
    ActivityEvent,
    ActivityEventType,
    ActivityStatus,
    AnswerDelta,
    ChatRequest,
    FinalResponse,
    ResponseStatus,
)
from orysys_assistant.observability.logging import get_logger
from orysys_assistant.observability.tracing import run_traced_mock_agent

router = APIRouter(prefix="/v1/chat", tags=["chat"])
logger = get_logger()


class ClientDisconnectedError(Exception):
    """Internal control-flow signal; never exposed as a server error."""


def _sse(event: str, payload: ActivityEvent | AnswerDelta | FinalResponse) -> dict[str, str]:
    return {"event": event, "data": json.dumps(payload.model_dump(mode="json"))}


def _activity(
    *,
    event_type: ActivityEventType,
    request_id: UUID,
    conversation_id: UUID,
    status: ActivityStatus,
    message: str,
    agent: str | None = None,
    node: str | None = None,
    metadata: dict[str, object] | None = None,
) -> ActivityEvent:
    return ActivityEvent(
        event_type=event_type,
        request_id=request_id,
        conversation_id=conversation_id,
        status=status,
        message=message,
        agent=agent,
        node=node,
        metadata=metadata or {},
    )


async def _ensure_connected(request: Request) -> None:
    if await request.is_disconnected():
        raise ClientDisconnectedError


async def stream_chat_events(
    request: Request,
    payload: ChatRequest,
    settings: Settings,
) -> AsyncIterator[dict[str, str]]:
    request_id: UUID = request.state.request_id
    conversation_id = payload.conversation_id or uuid4()
    started = perf_counter()
    structlog.contextvars.bind_contextvars(conversation_id=str(conversation_id))

    try:
        await _ensure_connected(request)
        yield _sse(
            "activity",
            _activity(
                event_type=ActivityEventType.REQUEST_RECEIVED,
                request_id=request_id,
                conversation_id=conversation_id,
                status=ActivityStatus.COMPLETED,
                message="Request accepted by the API.",
                node="request_entry",
            ),
        )
        yield _sse(
            "activity",
            _activity(
                event_type=ActivityEventType.AGENT_STARTED,
                request_id=request_id,
                conversation_id=conversation_id,
                status=ActivityStatus.STARTED,
                message="Temporary Phase 1 agent started.",
                agent="mock_agent",
                node="mock_response",
            ),
        )

        answer = await run_traced_mock_agent(
            payload.message,
            enabled=settings.langsmith_tracing,
            api_key=settings.langsmith_api_key,
            project_name=settings.langsmith_project,
            metadata={"request_id": str(request_id), "conversation_id": str(conversation_id)},
        )

        yield _sse(
            "activity",
            _activity(
                event_type=ActivityEventType.ANSWER_STREAMING,
                request_id=request_id,
                conversation_id=conversation_id,
                status=ActivityStatus.STARTED,
                message="Streaming mock answer tokens.",
                agent="mock_agent",
                node="answer_stream",
            ),
        )

        for sequence, token in enumerate(re.findall(r"\S+\s*", answer)):
            await _ensure_connected(request)
            yield _sse(
                "answer_delta",
                AnswerDelta(
                    request_id=request_id,
                    conversation_id=conversation_id,
                    sequence=sequence,
                    text=token,
                ),
            )
            if settings.mock_token_delay_seconds:
                await asyncio.sleep(settings.mock_token_delay_seconds)

        yield _sse(
            "activity",
            _activity(
                event_type=ActivityEventType.VALIDATION_STARTED,
                request_id=request_id,
                conversation_id=conversation_id,
                status=ActivityStatus.COMPLETED,
                message="Phase 1 response contract validated.",
                node="output_validation",
            ),
        )
        duration_ms = round((perf_counter() - started) * 1_000, 2)
        yield _sse(
            "activity",
            _activity(
                event_type=ActivityEventType.REQUEST_COMPLETED,
                request_id=request_id,
                conversation_id=conversation_id,
                status=ActivityStatus.COMPLETED,
                message="Request completed.",
                node="request_exit",
                metadata={"duration_ms": duration_ms},
            ),
        )
        yield _sse(
            "final",
            FinalResponse(
                request_id=request_id,
                conversation_id=conversation_id,
                status=ResponseStatus.COMPLETE,
                answer=answer,
                warnings=["Phase 1 uses a mock agent; retrieval is not implemented yet."],
            ),
        )
        logger.info("chat_stream_completed", duration_ms=duration_ms, result="complete")
    except ClientDisconnectedError:
        logger.info(
            "chat_stream_cancelled",
            duration_ms=round((perf_counter() - started) * 1_000, 2),
            result="client_disconnected",
        )
        return
    except asyncio.CancelledError:
        logger.info(
            "chat_stream_cancelled",
            duration_ms=round((perf_counter() - started) * 1_000, 2),
            result="task_cancelled",
        )
        raise
    except Exception as exc:
        logger.exception("chat_stream_failed", error_type=type(exc).__name__, result="failed")
        failure = _activity(
            event_type=ActivityEventType.REQUEST_FAILED,
            request_id=request_id,
            conversation_id=conversation_id,
            status=ActivityStatus.FAILED,
            message="The request could not be completed.",
            node="request_exit",
        )
        yield _sse("activity", failure)
        yield _sse(
            "final",
            FinalResponse(
                request_id=request_id,
                conversation_id=conversation_id,
                status=ResponseStatus.FAILED,
                answer="The request could not be completed.",
                warnings=["An internal streaming error occurred."],
                degraded=True,
            ),
        )


@router.post("/stream")
async def stream_chat(
    request: Request,
    payload: ChatRequest,
    settings: SettingsDependency,
) -> EventSourceResponse:
    return EventSourceResponse(
        stream_chat_events(request, payload, settings),
        ping=15,
        headers={
            "Cache-Control": "no-cache, no-transform",
            "X-Accel-Buffering": "no",
        },
    )
