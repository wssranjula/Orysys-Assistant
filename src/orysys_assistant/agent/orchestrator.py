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
from orysys_assistant.guardrails.content import unwrap_evidence
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
        metadata={"agent": "root_deep_agent", "phase": 6},
    )
    async def run(
        self,
        question: str,
        context: TrustedRequestContext,
        transition_sink: TransitionSink | None = None,
        conversation_summary: str = "",
        thread_id: str | None = None,
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
                metadata={
                    "route": route.value,
                    "plan_summary": self._plan_summary(route),
                },
            ),
        )

        grounded_question = self._with_conversation_context(question, conversation_summary)
        if route is AgentRoute.DIRECT_KNOWLEDGE:
            return await self._direct(grounded_question, context, transition_sink)
        if route is AgentRoute.RESEARCH:
            return await self._delegate_research(
                grounded_question, context, transition_sink, thread_id
            )
        if route is AgentRoute.ANALYSIS:
            return await self._delegate_analysis(grounded_question, context, transition_sink)
        return await self._delegate_enterprise(grounded_question, context, transition_sink)

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
        warnings = [str(item) for item in result.get("warnings", [])]
        await self._retrieval_transition(
            sink,
            "completed",
            len(evidence),
            {
                "candidate_count": int(result.get("candidate_count", len(evidence))),
                "selected_evidence_count": len(evidence),
                "retrieval_mode": str(result.get("retrieval_mode", "hybrid")),
                "tool_name": "knowledge_search",
            },
        )
        return AgentExecutionResult(
            route=AgentRoute.DIRECT_KNOWLEDGE,
            answer=self._evidence_answer(evidence),
            status=(
                ResponseStatus.PARTIAL
                if any("dense-only" in warning for warning in warnings)
                else ResponseStatus.COMPLETE
            ),
            citations=self._citations(evidence),
            warnings=warnings
            if evidence
            else [*warnings, "No relevant authorized evidence was found."],
            evidence_ids=[item.evidence_id for item in evidence],
            evidence=evidence,
        )

    async def _delegate_research(
        self,
        question: str,
        context: TrustedRequestContext,
        sink: TransitionSink | None,
        thread_id: str | None,
    ) -> AgentExecutionResult:
        await self._subagent_transition(sink, self._research.name, "started")
        execution = await self._research.run(question, context, sink, thread_id)
        await self._subagent_transition(sink, self._research.name, "completed")
        return self._research_response(execution)

    async def _delegate_analysis(
        self,
        question: str,
        context: TrustedRequestContext,
        sink: TransitionSink | None,
    ) -> AgentExecutionResult:
        await self._subagent_transition(sink, self._analysis.name, "started")
        execution = await self._analysis.run(question, context, sink)
        await self._subagent_transition(sink, self._analysis.name, "completed")
        return self._analysis_response(execution)

    async def _delegate_enterprise(
        self,
        question: str,
        context: TrustedRequestContext,
        sink: TransitionSink | None,
    ) -> AgentExecutionResult:
        await self._subagent_transition(sink, self._enterprise.name, "started")
        execution = await self._enterprise.run(question, context, sink)
        await self._subagent_transition(sink, self._enterprise.name, "completed")
        if not execution.result.data:
            await self._retrieval_transition(sink, "started", 0)
            fallback = await self._direct_toolbox.execute(
                "knowledge_search", {"query": question, "top_k": 6}, context
            )
            evidence = [Evidence.model_validate(item) for item in fallback["evidence"]]
            await self._retrieval_transition(
                sink,
                "completed",
                len(evidence),
                {
                    "candidate_count": int(fallback.get("candidate_count", len(evidence))),
                    "selected_evidence_count": len(evidence),
                    "retrieval_mode": str(fallback.get("retrieval_mode", "hybrid")),
                    "tool_name": "knowledge_search",
                },
            )
            if evidence:
                return AgentExecutionResult(
                    route=AgentRoute.DIRECT_KNOWLEDGE,
                    answer=(
                        "Enterprise data was unavailable. I found these authorized documents:\n"
                        + "\n".join(
                            f"- {item.title}: "
                            f"{_first_sentence(unwrap_evidence(item.content))} [{index}]"
                            for index, item in enumerate(evidence, start=1)
                        )
                    ),
                    status=ResponseStatus.PARTIAL,
                    citations=self._citations(evidence),
                    warnings=execution.result.warnings,
                    evidence_ids=[item.evidence_id for item in evidence],
                    evidence=evidence,
                )
        return self._enterprise_response(execution)

    @staticmethod
    async def _emit(sink: TransitionSink | None, transition: AgentTransition) -> None:
        if sink is not None:
            await sink(transition)

    async def _retrieval_transition(
        self,
        sink: TransitionSink | None,
        status: str,
        evidence_count: int,
        metadata: dict[str, object] | None = None,
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
                metadata=(
                    {"evidence_count": evidence_count, **(metadata or {})}
                    if status == "completed"
                    else {}
                ),
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
            lines.append(
                f"- {item.title}: {_first_sentence(unwrap_evidence(item.content))} [{index}]"
            )
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
            evidence=execution.evidence,
        )

    @classmethod
    def _analysis_response(cls, execution: AnalysisExecution) -> AgentExecutionResult:
        lines = [
            f"Processed {execution.result.rows_processed} authorized evidence records using "
            f"{execution.result.operation}."
        ]
        for row in execution.result.results:
            label = row.get("value", row.get("date", "unknown"))
            suffix = f" ({row['percentage']}%)" if "percentage" in row else ""
            lines.append(f"- {label}: {row['count']}{suffix}")
        return AgentExecutionResult(
            route=AgentRoute.ANALYSIS,
            answer="\n".join(lines),
            citations=cls._citations(execution.evidence),
            warnings=execution.result.warnings,
            evidence_ids=[item.evidence_id for item in execution.evidence],
            evidence=execution.evidence,
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
            status=(
                ResponseStatus.PARTIAL
                if result.data and result.warnings
                else ResponseStatus.INSUFFICIENT_EVIDENCE
                if not result.data
                else ResponseStatus.COMPLETE
            ),
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

    @staticmethod
    def _with_conversation_context(question: str, summary: str) -> str:
        if not summary:
            return question
        return f"{question}\n\nPrior conversation summary:\n{summary}"

    @staticmethod
    def _plan_summary(route: AgentRoute) -> str:
        return {
            AgentRoute.DIRECT_KNOWLEDGE: "Search authorized knowledge and validate citations.",
            AgentRoute.RESEARCH: "Delegate bounded multi-source research, then validate findings.",
            AgentRoute.ANALYSIS: "Retrieve authorized records and run controlled analysis.",
            AgentRoute.ENTERPRISE: "Call one approved read-only enterprise tool with fallback.",
        }[route]
