"""Factories for the controlled orchestrator and the production Deep Agent graph."""

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from deepagents import (
    GeneralPurposeSubagentProfile,
    HarnessProfile,
    SubAgent,
    create_deep_agent,
    register_harness_profile,
)
from deepagents.backends import FilesystemBackend
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.tools import StructuredTool

from orysys_assistant.agent.orchestrator import RootOrchestrator
from orysys_assistant.agent.router import IntentRouter
from orysys_assistant.agent.subagents import (
    AnalysisSubagent,
    EnterpriseToolSubagent,
    ResearchSubagent,
)
from orysys_assistant.agent.toolbox import ScopedToolbox
from orysys_assistant.security.models import TrustedRequestContext
from orysys_assistant.tools.gateway import ToolGateway

BUILTIN_TOOLS_BLOCKLIST = frozenset(
    {"ls", "read_file", "write_file", "edit_file", "glob", "grep", "execute"}
)
_profile_registered = False


@dataclass(frozen=True, slots=True)
class AgentDependencies:
    gateway: ToolGateway


def build_root_orchestrator(dependencies: AgentDependencies) -> RootOrchestrator:
    gateway = dependencies.gateway
    direct = ScopedToolbox(gateway, frozenset({"knowledge_search"}))
    research = ResearchSubagent(ScopedToolbox(gateway, frozenset({"knowledge_search"})))
    analysis = AnalysisSubagent(ScopedToolbox(gateway, frozenset({"knowledge_search"})))
    enterprise = EnterpriseToolSubagent(
        ScopedToolbox(
            gateway,
            frozenset(
                {
                    "employee_directory.lookup",
                    "service_catalog.search",
                    "incident_records.search",
                }
            ),
        )
    )
    return RootOrchestrator(
        router=IntentRouter(),
        direct_toolbox=direct,
        research=research,
        analysis=analysis,
        enterprise=enterprise,
    )


def _register_secure_profile() -> None:
    global _profile_registered  # noqa: PLW0603
    if _profile_registered:
        return
    register_harness_profile(
        "openai",
        HarnessProfile(
            excluded_tools=BUILTIN_TOOLS_BLOCKLIST,
            general_purpose_subagent=GeneralPurposeSubagentProfile(enabled=False),
        ),
    )
    _profile_registered = True


def build_deep_agent_graph(
    *,
    model: str | BaseChatModel,
    gateway: ToolGateway,
    context: TrustedRequestContext,
    project_root: Path,
) -> Any:
    """Build the provider-backed harness; API routing remains deterministic in Phase 4."""

    _register_secure_profile()
    knowledge_search = _gateway_tool(
        "knowledge_search", "Search authorized Commercial Bank evidence.", gateway, context
    )
    enterprise_tools = [
        _gateway_tool(name, f"Execute approved read-only enterprise tool {name}.", gateway, context)
        for name in (
            "employee_directory.lookup",
            "service_catalog.search",
            "incident_records.search",
        )
    ]
    subagents: list[SubAgent] = [
        {
            "name": "research",
            "description": "Investigate multi-document questions and return grounded findings.",
            "system_prompt": "Use only knowledge_search and cite every finding by evidence ID.",
            "tools": [knowledge_search],
        },
        {
            "name": "analysis",
            "description": "Perform approved structured analysis over authorized evidence.",
            "system_prompt": (
                "Retrieve evidence and return only the approved structured analysis schema."
            ),
            "tools": [knowledge_search],
        },
        {
            "name": "enterprise-tools",
            "description": "Read approved enterprise directory, service, and incident data.",
            "system_prompt": "Use only the supplied read-only enterprise tools.",
            "tools": enterprise_tools,
        },
    ]
    return create_deep_agent(
        model=model,
        tools=[knowledge_search],
        subagents=subagents,
        system_prompt=(
            "You are Commercial Bank's evidence-grounded assistant. Delegate only when needed. "
            "Never infer permissions, expose hidden reasoning, or invent citations."
        ),
        skills=["/skills"],
        backend=FilesystemBackend(root_dir=project_root, virtual_mode=True),
        name="commercial-bank-root-agent",
    )


def _gateway_tool(
    name: str,
    description: str,
    gateway: ToolGateway,
    context: TrustedRequestContext,
) -> StructuredTool:
    async def execute(query: str) -> Any:
        return await gateway.execute(name, {"query": query}, context)

    return StructuredTool.from_function(
        coroutine=execute,
        name=name,
        description=description,
    )
