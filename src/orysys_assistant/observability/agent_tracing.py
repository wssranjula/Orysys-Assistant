"""LangSmith tracing helpers for agent middleware and application spans."""

from __future__ import annotations

from langchain.agents.middleware import configure_trace_policy
from langchain.agents.middleware.types import omit_payload
from langgraph.types import TracePolicy

APP_SPAN_TAG = "app-span"
MIDDLEWARE_SPAN_TAG = "middleware-span"
TOOL_NAME_PREFIXES = ("consult_", "search_", "get_", "modify_")
KNOWN_TOOL_RUN_NAMES = frozenset(
    {
        "knowledge_search",
        "structured_analysis",
        "modify_incident",
        "tool-gateway-execution",
    }
)

QUIET_MIDDLEWARE_TRACE_POLICY = TracePolicy(
    process_inputs=omit_payload,
    process_outputs=omit_payload,
)


def configure_agent_tracing(*, quiet_middleware: bool = True) -> None:
    """Configure process-wide agent tracing defaults for LangSmith readability."""

    if quiet_middleware:
        configure_trace_policy(QUIET_MIDDLEWARE_TRACE_POLICY)
    else:
        configure_trace_policy(None)


def app_span_tags(*extra: str) -> list[str]:
    """Return LangSmith tags that mark an intentional application span."""

    return list(dict.fromkeys([APP_SPAN_TAG, *extra]))


def is_tool_run_name(name: str | None) -> bool:
    """Return True when a LangSmith run name matches a registered gateway tool."""

    if not name:
        return False
    if name in KNOWN_TOOL_RUN_NAMES:
        return True
    return name.startswith(TOOL_NAME_PREFIXES)


def is_middleware_run_name(name: str | None) -> bool:
    """Return True when a LangSmith run name belongs to LangChain agent middleware."""

    if not name:
        return False
    return (
        "Middleware" in name
        or name.startswith("Quiet")
        or name.startswith("DelegationOnceLimit")
        or name in {"model", "tools", "publish.awrap_tool_call", "publish.wrap_tool_call"}
        or name.startswith("TodoListMiddleware")
        or name.startswith("SummarizationMiddleware")
        or name.startswith("FilesystemMiddleware")
        or name.startswith("SubAgentMiddleware")
        or name.startswith("AnthropicPromptCachingMiddleware")
        or name.startswith("PatchToolCallsMiddleware")
    )


def is_app_run_name(name: str | None) -> bool:
    """Return True when a LangSmith run name is an application-level span."""

    if not name:
        return False
    if is_middleware_run_name(name):
        return False
    if is_tool_run_name(name):
        return True
    return name in {
        "chat-request",
        "root-orchestrator",
        "root-deep-agent-orchestration",
        "output-validation",
        "hybrid-knowledge-retrieval",
        "tool-gateway-execution",
        "authorization-decision",
        "consult_knowledge_specialist",
        "consult_research_specialist",
        "consult_analysis_specialist",
        "consult_enterprise_specialist",
        "delegate-knowledge-subagent",
        "delegate-research-subagent",
        "delegate-analysis-subagent",
        "delegate-enterprise-tool-subagent",
        "knowledge-specialist",
        "research-specialist",
        "analysis-specialist",
        "enterprise-specialist",
    } or name.startswith("consult_") or name.startswith("delegate-")


def collapse_run_names(names: list[str]) -> dict[str, list[str]]:
    """Group LangSmith run names into application and middleware buckets."""

    app: list[str] = []
    middleware: list[str] = []
    other: list[str] = []
    for name in sorted(set(names)):
        if is_app_run_name(name):
            app.append(name)
        elif is_middleware_run_name(name):
            middleware.append(name)
        else:
            other.append(name)
    return {"app": app, "middleware": middleware, "other": other}
