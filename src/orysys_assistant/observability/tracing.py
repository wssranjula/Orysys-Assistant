"""Shared LangSmith client construction and chat-request trace helpers."""

from __future__ import annotations

from functools import lru_cache

from langsmith import Client
from langsmith.run_trees import RunTree

from orysys_assistant.agent.models import AgentExecutionResult
from orysys_assistant.guardrails.output import ValidationOutcome


@lru_cache(maxsize=4)
def get_langsmith_client(api_key: str, api_url: str) -> Client:
    """Return a shared client configured independently of process environment variables."""

    return Client(api_key=api_key, api_url=api_url)


def start_chat_request_trace(
    *,
    client: Client | None,
    project_name: str,
    enabled: bool,
    request_id: str,
    conversation_id: str,
    role: str,
    agent_name: str,
    message: str,
) -> RunTree | None:
    """Create and publish the top-level LangSmith run for one chat request."""

    if not enabled or client is None:
        return None

    run = RunTree(
        name="chat-request",
        run_type="chain",
        inputs={"message": message[:500]},
        project_name=project_name,
        ls_client=client,
        extra={
            "metadata": {
                "request_id": request_id,
                "conversation_id": conversation_id,
                "role": role,
                "agent_name": agent_name,
            }
        },
        tags=[role, f"request:{request_id}"],
    )
    run.post()
    return run


def finish_chat_request_trace(
    run: RunTree | None,
    *,
    result: AgentExecutionResult,
    validation: ValidationOutcome,
    duration_ms: float,
) -> None:
    """Close the chat-request run with route, validation, and timing metadata."""

    if run is None:
        return

    metadata = dict((run.extra or {}).get("metadata") or {})
    metadata.update(
        {
            "route": result.route.value,
            "status": result.status.value,
            "validation_valid": validation.valid,
            "validation_repaired": validation.repaired,
            "evidence_count": len(result.evidence),
            "citation_count": len(result.citations),
            "duration_ms": duration_ms,
        }
    )
    run.extra = {**(run.extra or {}), "metadata": metadata}
    run.tags = list(dict.fromkeys([*(run.tags or []), result.route.value, result.status.value]))
    run.end(
        outputs={
            "route": result.route.value,
            "status": result.status.value,
            "validation_valid": validation.valid,
            "evidence_count": len(result.evidence),
            "answer_preview": result.answer[:300],
            "duration_ms": duration_ms,
        },
        metadata=metadata,
    )
    run.patch()


def fail_chat_request_trace(
    run: RunTree | None, *, error: str, error_code: str = "request_failed"
) -> None:
    """Mark a chat-request run as failed when the stream exits before a validated result."""

    if run is None:
        return

    metadata = dict((run.extra or {}).get("metadata") or {})
    metadata["error_code"] = error_code
    run.extra = {**(run.extra or {}), "metadata": metadata}
    run.end(error=error[:500], metadata=metadata)
    run.patch()
