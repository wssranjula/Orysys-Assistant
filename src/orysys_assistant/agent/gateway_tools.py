"""Model-facing tool surface built from the deterministic gateway registrations.

An autonomous specialist chooses which tool to call and with what arguments. It never
chooses *who it is*: the trusted request context is injected from LangGraph runtime
context, so identity, role, and access scope cannot appear as model-authored arguments.
Every call still passes through :class:`ToolGateway`, which independently re-enforces
registration, RBAC capability, typed input, deadlines, result size, and audit logging.

The collector records what actually executed. Citations, evidence, and analysis results
are therefore reconstructed from observed tool traffic rather than from model prose.
"""

from collections.abc import Awaitable, Callable
from contextlib import suppress
from dataclasses import dataclass, field
from typing import Any

from langchain.agents.middleware import ModelCallLimitMiddleware, ToolCallLimitMiddleware
from langchain_core.messages import AIMessage
from langchain_core.tools import StructuredTool
from langgraph.config import get_stream_writer
from langgraph.runtime import get_runtime

from orysys_assistant.agent.models import AgentTransition
from orysys_assistant.agent.toolbox import ScopedToolbox
from orysys_assistant.domain.errors import (
    ApplicationError,
    AuthorizationError,
)
from orysys_assistant.retrieval.models import Evidence
from orysys_assistant.security.models import TrustedRequestContext

TransitionSink = Callable[[AgentTransition], Awaitable[None]]

MAX_TOOL_RESULT_CHARACTERS = 6_000
"""Cap on the evidence text handed back to the model for one tool call."""


@dataclass(slots=True)
class ToolInvocation:
    tool_name: str
    parameters: dict[str, Any]
    data: Any = None
    status: str = "completed"
    error_type: str | None = None


@dataclass(slots=True)
class SpecialistCollector:
    """Deterministic record of one specialist's tool traffic.

    Mutable by design and owned per request. It is the bridge between an autonomous
    loop and the frozen evidence, citation, and warning contracts the API returns.
    """

    evidence: dict[str, Evidence] = field(default_factory=dict)
    invocations: list[ToolInvocation] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    denied_tools: list[str] = field(default_factory=list)

    def ordered_evidence(self) -> list[Evidence]:
        return list(self.evidence.values())

    def results_for(self, tool_name: str) -> list[Any]:
        return [
            item.data
            for item in self.invocations
            if item.tool_name == tool_name and item.status == "completed" and item.data is not None
        ]

    def add_warning(self, warning: str) -> None:
        if warning not in self.warnings:
            self.warnings.append(warning)


@dataclass(frozen=True, slots=True)
class SpecialistContext:
    """Per-request runtime context handed to a specialist graph, never to the model."""

    request_context: TrustedRequestContext
    collector: SpecialistCollector
    agent_name: str = "specialist"
    transition_sink: TransitionSink | None = None


@dataclass(frozen=True, slots=True)
class SpecialistOutcome:
    """What one consultation produced, in the only terms the root is allowed to trust.

    ``report`` is model prose and carries no authority on its own. ``evidence`` and
    ``warnings`` come from the collector's record of executed calls, and ``grounded``
    states whether the specialist actually obtained material rather than whether it
    sounded confident. The root's reported status is derived from the latter three.
    """

    report: str
    evidence: list[Evidence] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    grounded: bool = False


def final_text(state: Any) -> str:
    """Read the last prose an agent *itself* wrote, ignoring non-text content blocks.

    Only assistant messages count. A loop that stops at its budget can leave a tool
    result as the newest message, and returning that would present a specialist's raw
    reply — scaffolding and all — as though the agent had written it.
    """

    messages = state.get("messages", []) if isinstance(state, dict) else []
    for message in reversed(messages):
        if not isinstance(message, AIMessage):
            continue
        content = getattr(message, "content", None)
        if isinstance(content, str) and content.strip():
            return content.strip()
        if isinstance(content, list):
            text = " ".join(
                str(part.get("text", ""))
                for part in content
                if isinstance(part, dict) and part.get("type") == "text"
            ).strip()
            if text:
                return text
    return ""


def budget_middleware(*, max_tool_calls: int, max_model_calls: int) -> list[Any]:
    """Execution budgets for one specialist run, enforced by the harness.

    A budget stated in a prompt is a request; a budget stated here is a fact. Tool and
    model call ceilings hold no matter what the model decides to do next.

    Typed as ``list[Any]`` because the harness middleware generics are invariant in the
    agent state parameter, which leaves no precise element type these classes satisfy.
    """

    return [
        ToolCallLimitMiddleware(run_limit=max_tool_calls, exit_behavior="continue"),
        ModelCallLimitMiddleware(run_limit=max_model_calls, exit_behavior="end"),
    ]


def build_gateway_tools(toolbox: ScopedToolbox) -> list[StructuredTool]:
    """Publish one LangChain tool per registration this agent is allowed to see."""
    return [
        _gateway_tool(toolbox, spec.name, spec.input_model, spec.description)
        for spec in toolbox.specs()
    ]


def _gateway_tool(
    toolbox: ScopedToolbox,
    tool_name: str,
    input_model: type[Any],
    description: str,
) -> StructuredTool:
    async def call(**parameters: Any) -> Any:
        runtime = get_runtime(SpecialistContext)
        context = runtime.context
        return await _execute(toolbox, tool_name, parameters, context)

    return StructuredTool.from_function(
        coroutine=call,
        name=tool_name,
        description=description or f"Call the approved read-only {tool_name} tool.",
        args_schema=input_model,
    )


async def _execute(
    toolbox: ScopedToolbox,
    tool_name: str,
    parameters: dict[str, Any],
    context: SpecialistContext,
) -> Any:
    """Run one gateway call and translate its outcome for an autonomous caller.

    Failure handling is deliberately asymmetric. A denial is a policy verdict, so it
    ends the turn rather than inviting the model to probe for a tool that will answer.
    Every other failure is contained here and returned as an ordinary tool result, so
    one broken dependency costs the specialist that call rather than the whole
    investigation, and its sibling calls still contribute their evidence.
    """

    await _emit(context, "tool_started", tool_name, "started", f"Tool {tool_name} started.")
    try:
        result = await toolbox.execute(tool_name, parameters, context.request_context)
    except AuthorizationError:
        context.collector.denied_tools.append(tool_name)
        context.collector.invocations.append(
            ToolInvocation(tool_name, parameters, status="denied", error_type="AuthorizationError")
        )
        await _emit(
            context,
            "tool_denied",
            tool_name,
            "denied",
            f"Tool {tool_name} was denied by role policy.",
            {"tool_name": tool_name, "error_type": "AuthorizationError"},
        )
        raise
    except ApplicationError as exc:
        return await _degraded(context, tool_name, parameters, type(exc).__name__, exc.message)
    except Exception as exc:
        # An unexpected dependency fault is not described back to the model, which has
        # no way to act on an internal detail and no need to see one.
        return await _degraded(
            context,
            tool_name,
            parameters,
            type(exc).__name__,
            "The tool failed unexpectedly and returned no data.",
        )

    evidence_count = _collect_evidence(context.collector, result)
    context.collector.invocations.append(ToolInvocation(tool_name, parameters, data=result))
    for warning in _result_warnings(result):
        context.collector.add_warning(warning)
    await _emit(
        context,
        "tool_completed",
        tool_name,
        "completed",
        f"Tool {tool_name} completed.",
        _completion_metadata(tool_name, parameters, result, evidence_count),
    )
    return _for_model(result)


async def _degraded(
    context: SpecialistContext,
    tool_name: str,
    parameters: dict[str, Any],
    error_type: str,
    message: str,
) -> dict[str, Any]:
    context.collector.invocations.append(
        ToolInvocation(tool_name, parameters, status="degraded", error_type=error_type)
    )
    context.collector.add_warning(f"The {tool_name} tool was unavailable: {error_type}.")
    await _emit(
        context,
        "tool_completed",
        tool_name,
        "degraded",
        f"Tool {tool_name} was unavailable.",
        {"tool_name": tool_name, "error_type": error_type},
    )
    return {"error": error_type, "message": message}


def _collect_evidence(collector: SpecialistCollector, result: Any) -> int:
    """Record authorized evidence so citations never depend on model output."""
    if not isinstance(result, dict) or not isinstance(result.get("evidence"), list):
        return 0
    collected = 0
    for item in result["evidence"]:
        with suppress(Exception):
            evidence = Evidence.model_validate(item)
            collector.evidence[evidence.evidence_id] = evidence
            collected += 1
    return collected


def _result_warnings(result: Any) -> list[str]:
    if not isinstance(result, dict):
        return []
    return [str(item) for item in result.get("warnings", []) if str(item)]


def _completion_metadata(
    tool_name: str, parameters: dict[str, Any], result: Any, evidence_count: int
) -> dict[str, Any]:
    metadata: dict[str, Any] = {"tool_name": tool_name}
    # The narrowing is now the model's choice, so the activity panel shows which filters
    # it actually applied rather than a filter the orchestrator picked on its behalf.
    filters = {
        key: parameters[key]
        for key in ("department", "document_type", "created_after", "created_before")
        if parameters.get(key) is not None
    }
    if filters:
        metadata["retrieval_filters"] = filters
    if not isinstance(result, dict):
        return metadata
    if evidence_count:
        metadata["evidence_count"] = evidence_count
        metadata["selected_evidence_count"] = int(
            result.get("selected_evidence_count", evidence_count)
        )
        metadata["candidate_count"] = int(result.get("candidate_count", evidence_count))
        metadata["retrieval_mode"] = str(result.get("retrieval_mode", "hybrid"))
    if "rows_processed" in result:
        metadata["rows_processed"] = int(result["rows_processed"])
    return metadata


def _for_model(result: Any) -> Any:
    """Shrink a tool result to what a model needs to reason about it.

    Evidence bodies are the dominant cost in a research loop, so chunks are summarized
    and the full records stay in the collector where citation resolution reads them.
    """

    if not isinstance(result, dict) or not isinstance(result.get("evidence"), list):
        return result
    trimmed = {key: value for key, value in result.items() if key != "evidence"}
    trimmed["evidence"] = [
        {
            "evidence_id": item.get("evidence_id"),
            "title": item.get("title"),
            "document_type": (item.get("metadata") or {}).get("document_type"),
            "created_date": (item.get("metadata") or {}).get("created_date"),
            "content": str(item.get("content", ""))[:MAX_TOOL_RESULT_CHARACTERS],
        }
        for item in result["evidence"]
    ]
    return trimmed


async def _emit(
    context: SpecialistContext,
    event_type: str,
    node: str,
    status: str,
    message: str,
    metadata: dict[str, Any] | None = None,
) -> None:
    """Publish one activity event on the graph's custom stream and the direct sink.

    Tool traffic is narrated where it happens, so the activity panel reflects what the
    specialist actually did rather than a hand-written description of what it should do.
    """

    transition = AgentTransition(
        event_type=event_type,
        agent=context.agent_name,
        node=node,
        status=status,
        message=message,
        metadata=metadata or {"tool_name": node},
    )
    with suppress(RuntimeError):
        get_stream_writer()(transition.model_dump(mode="json"))
    if context.transition_sink is not None:
        await context.transition_sink(transition)
