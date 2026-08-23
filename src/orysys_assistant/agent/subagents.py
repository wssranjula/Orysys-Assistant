"""Three static specialists with small, explicit tool surfaces."""

import re
from typing import Any

from langsmith import traceable

from orysys_assistant.agent.models import (
    AgentTransition,
    AnalysisExecution,
    AnalysisResult,
    EnterpriseExecution,
    EnterpriseToolResult,
    ResearchExecution,
)
from orysys_assistant.agent.research_graph import ResearchLimits, ResearchWorkflow, TransitionSink
from orysys_assistant.agent.research_planner import ResearchPlanner
from orysys_assistant.agent.toolbox import ScopedToolbox
from orysys_assistant.domain.errors import AuthorizationError, InvalidRequestError, ToolTimeoutError
from orysys_assistant.retrieval.models import Evidence
from orysys_assistant.security.models import TrustedRequestContext


def _evidence_from_tool(result: Any) -> list[Evidence]:
    if not isinstance(result, dict) or not isinstance(result.get("evidence"), list):
        raise InvalidRequestError("Knowledge search returned an invalid result contract.")
    return [Evidence.model_validate(item) for item in result["evidence"]]


class ResearchSubagent:
    name = "research_subagent"

    def __init__(
        self,
        toolbox: ScopedToolbox,
        limits: ResearchLimits,
        checkpointer: Any,
        planner: ResearchPlanner | None = None,
    ) -> None:
        self.workflow = ResearchWorkflow(toolbox, limits, checkpointer, planner)

    @traceable(
        name="delegate-research-subagent",
        run_type="chain",
        metadata={"agent": "research_subagent", "delegated": True},
    )
    async def run(
        self,
        question: str,
        context: TrustedRequestContext,
        transition_sink: TransitionSink | None = None,
        thread_id: str | None = None,
    ) -> ResearchExecution:
        return await self.workflow.run(question, context, transition_sink, thread_id)


class AnalysisSubagent:
    name = "analysis_subagent"

    def __init__(self, toolbox: ScopedToolbox) -> None:
        self._toolbox = toolbox

    @traceable(
        name="delegate-analysis-subagent",
        run_type="chain",
        metadata={"agent": "analysis_subagent", "delegated": True},
    )
    async def run(
        self,
        question: str,
        context: TrustedRequestContext,
        transition_sink: TransitionSink | None = None,
    ) -> AnalysisExecution:
        evidence = _evidence_from_tool(
            await self._toolbox.execute(
                "knowledge_search",
                {"query": question, "top_k": 12},
                context,
            )
        )
        if transition_sink is not None:
            await transition_sink(
                AgentTransition(
                    event_type="tool_started",
                    agent=self.name,
                    node="structured_analysis",
                    status="started",
                    message="Controlled structured analysis started.",
                    metadata={"tool_name": "structured_analysis"},
                )
            )
        try:
            raw = await self._toolbox.execute(
                "structured_analysis",
                {
                    "operation": "count_by",
                    "records": [
                        {
                            "document_type": item.metadata.get("document_type", "unknown"),
                            "created_date": item.metadata.get("created_date"),
                            "fixture_id": item.metadata.get("fixture_id"),
                        }
                        for item in evidence
                    ],
                    "field": "document_type",
                },
                context,
            )
        except AuthorizationError:
            if transition_sink is not None:
                await transition_sink(
                    AgentTransition(
                        event_type="tool_denied",
                        agent=self.name,
                        node="structured_analysis",
                        status="denied",
                        message="Structured analysis was denied by role policy.",
                    )
                )
            raise
        result = AnalysisResult.model_validate(raw)
        if transition_sink is not None:
            await transition_sink(
                AgentTransition(
                    event_type="tool_completed",
                    agent=self.name,
                    node="structured_analysis",
                    status="completed",
                    message="Controlled structured analysis completed.",
                    metadata={
                        "rows_processed": result.rows_processed,
                        "tool_name": "structured_analysis",
                    },
                )
            )
        return AnalysisExecution(
            result=result,
            evidence=evidence,
        )


class EnterpriseToolSubagent:
    name = "enterprise_tool_subagent"

    def __init__(self, toolbox: ScopedToolbox) -> None:
        self._toolbox = toolbox

    @traceable(
        name="delegate-enterprise-tool-subagent",
        run_type="tool",
        metadata={"agent": "enterprise_tool_subagent", "delegated": True},
    )
    async def run(
        self,
        question: str,
        context: TrustedRequestContext,
        transition_sink: TransitionSink | None = None,
    ) -> EnterpriseExecution:
        tool_name, parameters = self._select_tool(question)
        if transition_sink is not None:
            await transition_sink(
                AgentTransition(
                    event_type="tool_started",
                    agent=self.name,
                    node=tool_name,
                    status="started",
                    message=f"Read-only enterprise tool {tool_name} started.",
                    metadata={"tool_name": tool_name},
                )
            )
        try:
            data = await self._toolbox.execute(tool_name, parameters, context)
        except AuthorizationError:
            if transition_sink is not None:
                await transition_sink(
                    AgentTransition(
                        event_type="tool_denied",
                        agent=self.name,
                        node=tool_name,
                        status="denied",
                        message=f"Enterprise tool {tool_name} was denied by role policy.",
                    )
                )
            raise
        except (InvalidRequestError, ToolTimeoutError) as exc:
            if transition_sink is not None:
                await transition_sink(
                    AgentTransition(
                        event_type="tool_completed",
                        agent=self.name,
                        node=tool_name,
                        status="degraded",
                        message=f"Enterprise tool {tool_name} was unavailable.",
                        metadata={
                            "error_type": type(exc).__name__,
                            "tool_name": tool_name,
                        },
                    )
                )
            return EnterpriseExecution(
                result=EnterpriseToolResult(
                    tool_name=tool_name,
                    data={},
                    source="enterprise_tool_gateway",
                    warnings=[f"The enterprise tool was unavailable: {type(exc).__name__}."],
                )
            )
        if transition_sink is not None:
            await transition_sink(
                AgentTransition(
                    event_type="tool_completed",
                    agent=self.name,
                    node=tool_name,
                    status="completed",
                    message=f"Read-only enterprise tool {tool_name} completed.",
                    metadata={"tool_name": tool_name},
                )
            )
        return EnterpriseExecution(
            result=EnterpriseToolResult(
                tool_name=tool_name,
                data=data if isinstance(data, dict) else {"result": data},
                source="enterprise_tool_gateway",
            )
        )

    @staticmethod
    def _select_tool(question: str) -> tuple[str, dict[str, Any]]:
        normalized = question.lower()
        employee_id = re.search(r"\bEMP-[0-9]{3}\b", question, re.I)
        service_id = re.search(r"\bSVC-[A-Z]+-[0-9]{3}\b", question, re.I)
        incident_id = re.search(r"\bINC-[0-9]{4}-[0-9]{3}\b", question, re.I)
        if employee_id:
            return "get_employee", {"employee_id": employee_id.group().upper()}
        if service_id:
            return "get_service", {"service_id": service_id.group().upper()}
        if incident_id:
            return "get_incident", {"incident_id": incident_id.group().upper()}
        if "employee" in normalized or "person" in normalized:
            return "search_employees", {"name": question}
        if "incident" in normalized:
            return "search_incidents", {"query": question}
        return "search_services", {"query": question}


def _first_sentence(content: str, max_characters: int = 280) -> str:
    compact = " ".join(content.split())
    sentence = compact.split(". ", maxsplit=1)[0]
    return sentence[:max_characters].rstrip() + ("…" if len(sentence) > max_characters else "")
