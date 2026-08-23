"""Factory for the single production agent runtime."""

from dataclasses import dataclass
from typing import Any

from langchain_openai import ChatOpenAI
from pydantic import SecretStr

from orysys_assistant.agent.orchestrator import RootOrchestrator
from orysys_assistant.agent.research_agent import ResearchLimits, ResearchSubagent
from orysys_assistant.agent.subagents import (
    AnalysisSubagent,
    EnterpriseToolSubagent,
    KnowledgeSubagent,
)
from orysys_assistant.agent.toolbox import ScopedToolbox
from orysys_assistant.config import Settings
from orysys_assistant.domain.errors import InvalidRequestError
from orysys_assistant.tools.gateway import ToolGateway

ENTERPRISE_TOOLS = frozenset(
    {
        "get_employee",
        "search_employees",
        "get_service",
        "search_services",
        "get_incident",
        "search_incidents",
    }
)


@dataclass(frozen=True, slots=True)
class AgentDependencies:
    gateway: ToolGateway
    settings: Settings | None = None
    checkpointer: Any = None
    model: Any = None
    """Chat model for the root loop and every specialist. Injected directly by tests and
    evaluation runs so agent behaviour can be exercised without a provider credential."""


def build_root_orchestrator(dependencies: AgentDependencies) -> RootOrchestrator:
    """Wire the root agent to four specialists, each holding its own scoped toolbox.

    Tool visibility is decided here and nowhere else. A specialist can only ever call
    what its ``ScopedToolbox`` publishes, so the root's autonomy is bounded by which
    specialist it consults rather than by what it is asked not to do.
    """

    gateway = dependencies.gateway
    settings = dependencies.settings or Settings.model_construct()
    model = dependencies.model or _provider_model(settings)
    if model is None:
        raise InvalidRequestError(
            "The assistant requires a chat model. Configure OPENAI_API_KEY or inject a model."
        )

    knowledge = KnowledgeSubagent(
        ScopedToolbox(gateway, frozenset({"knowledge_search"})),
        model,
        max_tool_calls=settings.specialist_max_tool_calls,
        max_model_calls=settings.specialist_max_model_calls,
        overall_timeout_seconds=settings.specialist_overall_timeout_seconds,
    )
    research = ResearchSubagent(
        ScopedToolbox(gateway, frozenset({"knowledge_search"})),
        ResearchLimits.from_settings(settings),
        model,
        dependencies.checkpointer,
    )
    analysis = AnalysisSubagent(
        ScopedToolbox(gateway, frozenset({"knowledge_search", "structured_analysis"})),
        model,
        max_tool_calls=settings.specialist_max_tool_calls + 2,
        max_model_calls=settings.specialist_max_model_calls + 1,
        overall_timeout_seconds=settings.specialist_overall_timeout_seconds,
    )
    enterprise = EnterpriseToolSubagent(
        ScopedToolbox(gateway, ENTERPRISE_TOOLS),
        model,
        max_tool_calls=settings.specialist_max_tool_calls,
        max_model_calls=settings.specialist_max_model_calls,
        overall_timeout_seconds=settings.specialist_overall_timeout_seconds,
    )

    return RootOrchestrator(
        model=model,
        knowledge=knowledge,
        research=research,
        analysis=analysis,
        enterprise=enterprise,
        checkpointer=dependencies.checkpointer,
        max_tool_calls=settings.root_max_tool_calls,
        max_model_calls=settings.root_max_model_calls,
    )


def _provider_model(settings: Settings) -> Any:
    if not settings.openai_api_key:
        return None
    return ChatOpenAI(
        model=settings.agent_model,
        api_key=SecretStr(settings.openai_api_key),
        max_retries=settings.llm_retry_attempts,
        timeout=settings.request_timeout_seconds,
    )
