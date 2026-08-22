"""Controlled root orchestration used by the API and deterministic tests."""

from collections.abc import Awaitable, Callable

from langsmith import traceable

from orysys_assistant.agent.models import (
    AgentExecutionResult,
    AgentRoute,
    AgentTransition,
    AnalysisExecution,
    EnterpriseExecution,
    ResearchExecution,
)
from orysys_assistant.agent.router import IntentRouter
from orysys_assistant.agent.subagents import (
    AnalysisSubagent,
    EnterpriseToolSubagent,
    ResearchSubagent,
    _first_sentence,
)
from orysys_assistant.agent.toolbox import ScopedToolbox
from orysys_assistant.domain.models import Citation, ResponseStatus
from orysys_assistant.retrieval.models import Evidence
from orysys_assistant.security.models import TrustedRequestContext

TransitionSink = Callable[[AgentTransition], Awaitable[None]]


class RootOrchestrator:
    name = "root_deep_agent"

    def __init__(
        self,
        *,
        router: IntentRouter,
        direct_toolbox: ScopedToolbox,
        research: ResearchSubagent,
        analysis: AnalysisSubagent,
        enterprise: EnterpriseToolSubagent,
    ) -> None:
        self._router = router
        self._direct_toolbox = direct_toolbox
        self._research = research
        self._analysis = analysis
        self._enterprise = enterprise

    @traceable(
        name="root-deep-agent-orchestration",
        run_type="chain",
        metadata={"agent": "root_deep_agent", "phase": 4},
    )
    async def run(
        self,
        question: str,
        context: TrustedRequestContext,
        transition_sink: TransitionSink | None = None,
    ) -> AgentExecutionResult:
        await self._emit(
            transition_sink,
            AgentTransition(
                event_type="agent_started",
                agent=self.name,
                node="intent_routing",
                status="started",
                message="Root agent is classifying the request.",
            ),
        )
        route = self._router.route(question)
        await self._emit(
            transition_sink,
            AgentTransition(
                event_type="routing_completed",
                agent=self.name,
                node="intent_routing",
                status="completed",
                message=f"Selected {route.value} route.",
                metadata={"route": route.value},
            ),
        )

        if route is AgentRoute.DIRECT_KNOWLEDGE:
            return await self._direct(question, context, transition_sink)
        if route is AgentRoute.RESEARCH:
            return await self._delegate_research(question, context, transition_sink)
        if route is AgentRoute.ANALYSIS:
            return await self._delegate_analysis(question, context, transition_sink)
        return await self._delegate_enterprise(question, context, transition_sink)

    async def _direct(
        self,
        question: str,
        context: TrustedRequestContext,
        sink: TransitionSink | None,
    ) -> AgentExecutionResult:
        await self._retrieval_transition(sink, "started", 0)
        result = await self._direct_toolbox.execute(
            "knowledge_search", {"query": question, "top_k": 6}, context
        )
        evidence = [Evidence.model_validate(item) for item in result["evidence"]]
        await self._retrieval_transition(sink, "completed", len(evidence))
        return AgentExecutionResult(
            route=AgentRoute.DIRECT_KNOWLEDGE,
            answer=self._evidence_answer(evidence),
            citations=self._citations(evidence),
            warnings=[] if evidence else ["No relevant authorized evidence was found."],
            evidence_ids=[item.evidence_id for item in evidence],
        )

    async def _delegate_research(
        self,
        question: str,
        context: TrustedRequestContext,
        sink: TransitionSink | None,
    ) -> AgentExecutionResult:
        await self._subagent_transition(sink, self._research.name, "started")
        execution = await self._research.run(question, context, sink)
        await self._subagent_transition(sink, self._research.name, "completed")
        return self._research_response(execution)

    async def _delegate_analysis(
        self,
        question: str,
        context: TrustedRequestContext,
        sink: TransitionSink | None,
    ) -> AgentExecutionResult:
        await self._subagent_transition(sink, self._analysis.name, "started")
        execution = await self._analysis.run(question, context)
        await self._subagent_transition(sink, self._analysis.name, "completed")
        return self._analysis_response(execution)

    async def _delegate_enterprise(
        self,
        question: str,
        context: TrustedRequestContext,
        sink: TransitionSink | None,
    ) -> AgentExecutionResult:
        await self._subagent_transition(sink, self._enterprise.name, "started")
        execution = await self._enterprise.run(question, context)
        await self._subagent_transition(sink, self._enterprise.name, "completed")
        return self._enterprise_response(execution)

    @staticmethod
    async def _emit(sink: TransitionSink | None, transition: AgentTransition) -> None:
        if sink is not None:
            await sink(transition)

    async def _retrieval_transition(
        self, sink: TransitionSink | None, status: str, evidence_count: int
    ) -> None:
        await self._emit(
            sink,
            AgentTransition(
                event_type=f"retrieval_{status}",
                agent=self.name,
                node="knowledge_search",
                status=status,
                message=(
                    "Searching authorized knowledge."
                    if status == "started"
                    else f"Retrieved {evidence_count} authorized evidence records."
                ),
                metadata={"evidence_count": evidence_count} if status == "completed" else {},
            ),
        )

    async def _subagent_transition(
        self, sink: TransitionSink | None, agent: str, status: str
    ) -> None:
        await self._emit(
            sink,
            AgentTransition(
                event_type=f"subagent_{status}",
                agent=agent,
                node="delegation",
                status=status,
                message=f"{agent.replace('_', ' ').title()} {status}.",
            ),
        )

    @staticmethod
    def _evidence_answer(evidence: list[Evidence]) -> str:
        if not evidence:
            return "I could not find authorized evidence that answers this question."
        lines = ["I found the following relevant Commercial Bank evidence:"]
        for index, item in enumerate(evidence, start=1):
            lines.append(f"- {item.title}: {_first_sentence(item.content)} [{index}]")
        return "\n".join(lines)

    @classmethod
    def _research_response(cls, execution: ResearchExecution) -> AgentExecutionResult:
        lines = [execution.result.summary]
        for finding in execution.result.findings:
            lines.append(f"- {finding.claim}")
        return AgentExecutionResult(
            route=AgentRoute.RESEARCH,
            answer="\n".join(lines),
            status=(
                ResponseStatus.PARTIAL if execution.result.partial else ResponseStatus.COMPLETE
            ),
            citations=cls._citations(execution.evidence),
            warnings=execution.result.warnings,
            evidence_ids=execution.result.evidence_ids,
        )

    @classmethod
    def _analysis_response(cls, execution: AnalysisExecution) -> AgentExecutionResult:
        lines = [
            f"Processed {execution.result.rows_processed} authorized evidence records using "
            f"{execution.result.operation}."
        ]
        for row in execution.result.results:
            lines.append(f"- {row['document_type']}: {row['count']}")
        return AgentExecutionResult(
            route=AgentRoute.ANALYSIS,
            answer="\n".join(lines),
            citations=cls._citations(execution.evidence),
            warnings=execution.result.warnings,
            evidence_ids=[item.evidence_id for item in execution.evidence],
        )

    @staticmethod
    def _enterprise_response(execution: EnterpriseExecution) -> AgentExecutionResult:
        result = execution.result
        answer = (
            f"Enterprise tool `{result.tool_name}` returned: {result.data}"
            if result.data
            else "The requested enterprise data source is not available."
        )
        return AgentExecutionResult(
            route=AgentRoute.ENTERPRISE,
            answer=answer,
            warnings=result.warnings,
        )

    @staticmethod
    def _citations(evidence: list[Evidence]) -> list[Citation]:
        return [
            Citation(
                citation_id=str(index),
                evidence_id=item.evidence_id,
                document_id=item.document_id,
                title=item.title,
                chunk_id=item.chunk_id,
                source_path=str(item.metadata["source_path"]),
            )
            for index, item in enumerate(evidence, start=1)
        ]
