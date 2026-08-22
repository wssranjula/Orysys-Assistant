"""Three static specialists with small, explicit tool surfaces."""

from collections import Counter
from typing import Any

from langsmith import traceable

from orysys_assistant.agent.models import (
    AnalysisExecution,
    AnalysisResult,
    EnterpriseExecution,
    EnterpriseToolResult,
    ResearchExecution,
)
from orysys_assistant.agent.research_graph import ResearchLimits, ResearchWorkflow, TransitionSink
from orysys_assistant.agent.toolbox import ScopedToolbox
from orysys_assistant.domain.errors import InvalidRequestError
from orysys_assistant.retrieval.models import Evidence
from orysys_assistant.security.models import TrustedRequestContext


def _evidence_from_tool(result: Any) -> list[Evidence]:
    if not isinstance(result, dict) or not isinstance(result.get("evidence"), list):
        raise InvalidRequestError("Knowledge search returned an invalid result contract.")
    return [Evidence.model_validate(item) for item in result["evidence"]]


class ResearchSubagent:
    name = "research_subagent"

    def __init__(self, toolbox: ScopedToolbox, limits: ResearchLimits) -> None:
        self.workflow = ResearchWorkflow(toolbox, limits)

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
    ) -> ResearchExecution:
        return await self.workflow.run(question, context, transition_sink)


class AnalysisSubagent:
    name = "analysis_subagent"

    def __init__(self, toolbox: ScopedToolbox) -> None:
        self._toolbox = toolbox

    @traceable(
        name="delegate-analysis-subagent",
        run_type="chain",
        metadata={"agent": "analysis_subagent", "delegated": True},
    )
    async def run(self, question: str, context: TrustedRequestContext) -> AnalysisExecution:
        evidence = _evidence_from_tool(
            await self._toolbox.execute(
                "knowledge_search",
                {"query": question, "top_k": 12},
                context,
            )
        )
        counts = Counter(str(item.metadata.get("document_type", "unknown")) for item in evidence)
        rows = [
            {"document_type": document_type, "count": count}
            for document_type, count in sorted(counts.items())
        ]
        return AnalysisExecution(
            result=AnalysisResult(
                operation="count_evidence_by_document_type",
                rows_processed=len(evidence),
                results=rows,
                warnings=(
                    ["Phase 6 will add the controlled structured-analysis tool."]
                    if evidence
                    else ["No authorized evidence was available for analysis."]
                ),
            ),
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
    async def run(self, question: str, context: TrustedRequestContext) -> EnterpriseExecution:
        tool_name = self._select_tool(question)
        try:
            data = await self._toolbox.execute(
                tool_name,
                {"query": question},
                context,
            )
        except InvalidRequestError:
            return EnterpriseExecution(
                result=EnterpriseToolResult(
                    tool_name=tool_name,
                    data={},
                    source="enterprise_tool_gateway",
                    warnings=["The requested enterprise data source is not available in Phase 4."],
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
    def _select_tool(question: str) -> str:
        normalized = question.lower()
        if "employee" in normalized or "person" in normalized:
            return "employee_directory.lookup"
        if "incident" in normalized:
            return "incident_records.search"
        return "service_catalog.search"


def _first_sentence(content: str, max_characters: int = 280) -> str:
    compact = " ".join(content.split())
    sentence = compact.split(". ", maxsplit=1)[0]
    return sentence[:max_characters].rstrip() + ("…" if len(sentence) > max_characters else "")
