"""Server-Sent Event chat stream for the controlled Phase 4 agent runtime."""

import asyncio
import json
import re
from collections.abc import AsyncIterator
from time import perf_counter
from typing import cast
from uuid import UUID, uuid4

import structlog
from fastapi import APIRouter, Request
from sse_starlette.sse import EventSourceResponse

from orysys_assistant.agent.models import AgentTransition
from orysys_assistant.agent.orchestrator import RootOrchestrator
from orysys_assistant.api.dependencies import (
    ConversationRepositoryDependency,
    OutputValidatorDependency,
    RootOrchestratorDependency,
    SettingsDependency,
    TrustedChatContextDependency,
)
from orysys_assistant.config import Settings
from orysys_assistant.domain.errors import (
    ApplicationError,
    AuthorizationError,
    RetrievalUnavailableError,
    ToolTimeoutError,
)
from orysys_assistant.domain.models import (
    ActivityEvent,
    ActivityEventType,
    ActivityStatus,
    AnswerDelta,
    ChatRequest,
    FinalResponse,
    ResponseStatus,
)
from orysys_assistant.guardrails.input import InputGuard
from orysys_assistant.guardrails.output import OutputValidator
from orysys_assistant.memory.models import ConversationRecord
from orysys_assistant.memory.repository import ConversationRepository
from orysys_assistant.observability.logging import get_logger
from orysys_assistant.security.models import TrustedRequestContext

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
    context: TrustedRequestContext,
    orchestrator: RootOrchestrator,
    repository: ConversationRepository,
    conversation: ConversationRecord,
    output_validator: OutputValidator | None = None,
) -> AsyncIterator[dict[str, str]]:
    request_id: UUID = request.state.request_id
    conversation_id = conversation.conversation_id
    started = perf_counter()
    structlog.contextvars.bind_contextvars(
        conversation_id=str(conversation_id),
        user_id=context.identity.user_id,
        role=context.identity.role.value,
    )

    try:
        await _ensure_connected(request)
        yield _sse(
            "activity",
            _activity(
                event_type=ActivityEventType.AUTHENTICATION_COMPLETED,
                request_id=request_id,
                conversation_id=conversation_id,
                status=ActivityStatus.COMPLETED,
                message=f"Authenticated as {context.identity.role.value}.",
                node="authentication",
            ),
        )
        yield _sse(
            "activity",
            _activity(
                event_type=ActivityEventType.RATE_LIMIT_CHECKED,
                request_id=request_id,
                conversation_id=conversation_id,
                status=ActivityStatus.COMPLETED,
                message="Per-user request budget available.",
                node="rate_limit",
                metadata={"remaining": context.rate_limit_remaining},
            ),
        )
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
                event_type=ActivityEventType.MEMORY_LOADED,
                request_id=request_id,
                conversation_id=conversation_id,
                status=ActivityStatus.COMPLETED,
                message=f"Loaded {len(conversation.messages)} recent conversation messages.",
                node="conversation_memory",
                metadata={"evidence_count": len(conversation.evidence_ids)},
            ),
        )
        transitions: asyncio.Queue[AgentTransition] = asyncio.Queue()
        agent_task = asyncio.create_task(
            orchestrator.run(
                payload.message,
                context,
                transitions.put,
                conversation.summary,
                f"{context.identity.user_id}:{conversation_id}",
            )
        )
        try:
            async with asyncio.timeout(settings.request_timeout_seconds):
                while not agent_task.done() or not transitions.empty():
                    await _ensure_connected(request)
                    try:
                        transition = await asyncio.wait_for(transitions.get(), timeout=0.05)
                    except TimeoutError:
                        continue
                    yield _sse(
                        "activity",
                        _activity(
                            event_type=ActivityEventType(transition.event_type),
                            request_id=request_id,
                            conversation_id=conversation_id,
                            status=ActivityStatus(transition.status),
                            message=transition.message,
                            agent=transition.agent,
                            node=transition.node,
                            metadata=transition.metadata,
                        ),
                    )
                result = await agent_task
        except BaseException:
            if not agent_task.done():
                agent_task.cancel()
            await asyncio.gather(agent_task, return_exceptions=True)
            raise

        yield _sse(
            "activity",
            _activity(
                event_type=ActivityEventType.VALIDATION_STARTED,
                request_id=request_id,
                conversation_id=conversation_id,
                status=ActivityStatus.STARTED,
                message="Validating grounding, citations, and response policy.",
                node="output_validation",
            ),
        )
        validation = (output_validator or OutputValidator()).validate(result, context.access_scope)
        result = validation.result
        if validation.valid:
            yield _sse(
                "activity",
                _activity(
                    event_type=ActivityEventType.VALIDATION_STARTED,
                    request_id=request_id,
                    conversation_id=conversation_id,
                    status=ActivityStatus.COMPLETED,
                    message="Grounding and response contracts validated.",
                    node="output_validation",
                    metadata={"repaired": validation.repaired},
                ),
            )
        else:
            yield _sse(
                "activity",
                _activity(
                    event_type=ActivityEventType.VALIDATION_FAILED,
                    request_id=request_id,
                    conversation_id=conversation_id,
                    status=ActivityStatus.DEGRADED,
                    message="Response validation failed; returning insufficient evidence.",
                    node="output_validation",
                    metadata={"repair_attempted": validation.repaired},
                ),
            )

        conversation = await repository.append_turn(
            conversation_id,
            context.identity.user_id,
            payload.message,
            result.answer,
            result.evidence_ids,
        )
        yield _sse(
            "activity",
            _activity(
                event_type=ActivityEventType.MEMORY_UPDATED,
                request_id=request_id,
                conversation_id=conversation_id,
                status=ActivityStatus.COMPLETED,
                message="Saved the conversation turn and evidence references.",
                node="conversation_memory",
                metadata={"message_count": len(conversation.messages)},
            ),
        )

        yield _sse(
            "activity",
            _activity(
                event_type=ActivityEventType.ANSWER_STREAMING,
                request_id=request_id,
                conversation_id=conversation_id,
                status=ActivityStatus.STARTED,
                message="Streaming the grounded answer.",
                agent=orchestrator.name,
                node="answer_stream",
            ),
        )

        for sequence, token in enumerate(re.findall(r"\S+\s*", result.answer)):
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
                status=result.status,
                answer=result.answer,
                citations=result.citations,
                warnings=result.warnings,
                degraded=result.status is not ResponseStatus.COMPLETE,
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
    except (AuthorizationError, RetrievalUnavailableError, ToolTimeoutError, TimeoutError) as exc:
        logger.warning(
            "chat_stream_degraded", error_type=type(exc).__name__, result="insufficient_evidence"
        )
        yield _sse(
            "activity",
            _activity(
                event_type=ActivityEventType.REQUEST_FAILED,
                request_id=request_id,
                conversation_id=conversation_id,
                status=ActivityStatus.DEGRADED,
                message="A required authorized dependency was unavailable.",
                node="request_exit",
                metadata={"error_code": getattr(exc, "code", "execution_timeout")},
            ),
        )
        yield _sse(
            "final",
            FinalResponse(
                request_id=request_id,
                conversation_id=conversation_id,
                status=ResponseStatus.INSUFFICIENT_EVIDENCE,
                answer="I could not verify an answer from the authorized sources available.",
                warnings=["A required source or permission was unavailable."],
                degraded=True,
            ),
        )
    except ApplicationError as exc:
        logger.warning("chat_stream_failed", error_type=type(exc).__name__, result=exc.code)
        yield _sse(
            "activity",
            _activity(
                event_type=ActivityEventType.REQUEST_FAILED,
                request_id=request_id,
                conversation_id=conversation_id,
                status=ActivityStatus.FAILED,
                message="The request could not be completed safely.",
                node="request_exit",
                metadata={"error_code": exc.code},
            ),
        )
        yield _sse(
            "final",
            FinalResponse(
                request_id=request_id,
                conversation_id=conversation_id,
                status=ResponseStatus.FAILED,
                answer="The request could not be completed safely.",
                warnings=[exc.message],
                degraded=True,
            ),
        )
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
    context: TrustedChatContextDependency,
    orchestrator: RootOrchestratorDependency,
    repository: ConversationRepositoryDependency,
    output_validator: OutputValidatorDependency,
) -> EventSourceResponse:
    input_guard = cast(InputGuard, request.app.state.input_guard)
    input_guard.validate(payload)
    conversation = await repository.get_or_create(
        payload.conversation_id or uuid4(), context.identity.user_id
    )
    return EventSourceResponse(
        stream_chat_events(
            request,
            payload,
            settings,
            context,
            orchestrator,
            repository,
            conversation,
            output_validator,
        ),
        ping=15,
        headers={
            "Cache-Control": "no-cache, no-transform",
            "X-Accel-Buffering": "no",
        },
    )
