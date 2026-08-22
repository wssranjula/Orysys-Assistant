"""Controlled root orchestration used by the API and deterministic tests."""

from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import suppress
from dataclasses import dataclass
from typing import Annotated, Any, TypedDict
from uuid import uuid4

from langchain_core.messages import AIMessage, AnyMessage, HumanMessage
from langgraph.config import get_stream_writer
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages
from langgraph.runtime import Runtime
from langsmith import traceable

from orysys_assistant.agent.models import (
    AgentExecutionResult,
    AgentRoute,
    AgentTransition,
    AnalysisExecution,
    EnterpriseExecution,
    GroundedAnswerDraft,
    ResearchExecution,
)
from orysys_assistant.agent.router import AgentRouter
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
from orysys_assistant.tools.knowledge_search import KNOWLEDGE_QUERY_MAX_LENGTH

TransitionSink = Callable[[AgentTransition], Awaitable[None]]


class RootAgentState(TypedDict):
    messages: Annotated[list[AnyMessage], add_messages]
    question: str
    route: AgentRoute | None
    result: AgentExecutionResult | None


@dataclass(frozen=True, slots=True)
class RootAgentContext:
    request_context: TrustedRequestContext
    transition_sink: TransitionSink | None = None
    conversation_summary: str = ""
    thread_id: str | None = None


class RootOrchestrator:
    name = "root_deep_agent"

    def __init__(
        self,
        *,
        router: AgentRouter,
        direct_toolbox: ScopedToolbox,
        research: ResearchSubagent,
        analysis: AnalysisSubagent,
        enterprise: EnterpriseToolSubagent,
        checkpointer: Any = None,
        synthesizer: Any = None,
    ) -> None:
        self._router = router
        self._direct_toolbox = direct_toolbox
        self._research = research
        self._analysis = analysis
        self._enterprise = enterprise
        self._checkpointer = checkpointer
        self._synthesizer = synthesizer
        self.graph = self._compile()

    def _compile(self) -> Any:
        builder = StateGraph(RootAgentState, context_schema=RootAgentContext)
        builder.add_node("route", self._route_node)
        builder.add_node("direct_knowledge", self._direct_node)
        builder.add_node("research", self._research_node)
        builder.add_node("analysis", self._analysis_node)
        builder.add_node("enterprise", self._enterprise_node)
        builder.add_node("out_of_scope", self._out_of_scope_node)
        builder.add_node("synthesize", self._synthesize_node)
        builder.add_edge(START, "route")
        builder.add_conditional_edges(
            "route",
            self._route_after_classification,
            {
                AgentRoute.DIRECT_KNOWLEDGE.value: "direct_knowledge",
                AgentRoute.RESEARCH.value: "research",
                AgentRoute.ANALYSIS.value: "analysis",
                AgentRoute.ENTERPRISE.value: "enterprise",
                AgentRoute.OUT_OF_SCOPE.value: "out_of_scope",
            },
        )
        for node in (
            "direct_knowledge",
            "research",
            "analysis",
            "enterprise",
            "out_of_scope",
        ):
            builder.add_edge(node, "synthesize")
        builder.add_edge("synthesize", END)
        return builder.compile(checkpointer=self._checkpointer)

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
        final = await self.graph.ainvoke(
            {
                "messages": [HumanMessage(content=question)],
                "question": question,
                "route": None,
                "result": None,
            },
            config=self._graph_config(thread_id),
            context=RootAgentContext(
                request_context=context,
                transition_sink=transition_sink,
                conversation_summary=conversation_summary,
                thread_id=thread_id,
            ),
        )
        result = final["result"]
        if result is None:
            raise RuntimeError("The agent graph completed without a result.")
        return AgentExecutionResult.model_validate(result)

    async def stream(
        self,
        question: str,
        context: TrustedRequestContext,
        conversation_summary: str = "",
        thread_id: str | None = None,
    ) -> AsyncIterator[AgentTransition | AgentExecutionResult]:
        """Stream native LangGraph updates followed by the final typed result."""

        graph_context = RootAgentContext(
            request_context=context,
            # The compiled graph owns production conversation history. The explicit
            # summary remains a compatibility input when no checkpointer is configured.
            conversation_summary=conversation_summary if self._checkpointer is None else "",
            thread_id=thread_id,
        )
        async for part in self.graph.astream(
            {
                "messages": [HumanMessage(content=question)],
                "question": question,
                "route": None,
                "result": None,
            },
            config=self._graph_config(thread_id),
            context=graph_context,
            stream_mode=["custom", "updates"],
            version="v2",
        ):
            if part["type"] == "custom":
                yield AgentTransition.model_validate(part["data"])
                continue
            for node, update in part["data"].items():
                if node != "synthesize":
                    continue
                result = update.get("result") if isinstance(update, dict) else None
                if result is not None:
                    yield AgentExecutionResult.model_validate(result)

    def _graph_config(self, thread_id: str | None) -> dict[str, Any] | None:
        if self._checkpointer is None:
            return None
        return {"configurable": {"thread_id": thread_id or str(uuid4())}}

    async def _route_node(
        self, state: RootAgentState, runtime: Runtime[RootAgentContext]
    ) -> dict[str, Any]:
        await self._emit(
            runtime.context.transition_sink,
            AgentTransition(
                event_type="agent_started",
                agent=self.name,
                node="intent_routing",
                status="started",
                message="Supervisor agent is classifying the request.",
            ),
        )
        summary = runtime.context.conversation_summary or self._message_history(state["messages"])
        decision = await self._router.route(state["question"], summary)
        route = decision.route
        await self._emit(
            runtime.context.transition_sink,
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
        return {
            "route": route,
            "question": self._with_conversation_context(
                state["question"], summary
            ),
        }

    @staticmethod
    def _route_after_classification(state: RootAgentState) -> str:
        route = state["route"]
        if route is None:
            raise RuntimeError("The routing node did not select a route.")
        return route.value

    async def _direct_node(
        self, state: RootAgentState, runtime: Runtime[RootAgentContext]
    ) -> dict[str, AgentExecutionResult]:
        return {
            "result": await self._direct(
                state["question"], runtime.context.request_context, runtime.context.transition_sink
            )
        }

    async def _research_node(
        self, state: RootAgentState, runtime: Runtime[RootAgentContext]
    ) -> dict[str, AgentExecutionResult]:
        relay = self._relay_sink(runtime.context)
        return {
            "result": await self._delegate_research(
                state["question"],
                runtime.context.request_context,
                relay,
                runtime.context.thread_id,
            )
        }

    @staticmethod
    def _relay_sink(context: RootAgentContext) -> TransitionSink:
        writer: Callable[[Any], None] | None = None
        with suppress(RuntimeError):
            writer = get_stream_writer()

        async def relay(transition: AgentTransition) -> None:
            if writer is not None:
                writer(transition.model_dump(mode="json"))
            if context.transition_sink is not None:
                await context.transition_sink(transition)

        return relay

    async def _analysis_node(
        self, state: RootAgentState, runtime: Runtime[RootAgentContext]
    ) -> dict[str, AgentExecutionResult]:
        return {
            "result": await self._delegate_analysis(
                state["question"],
                runtime.context.request_context,
                self._relay_sink(runtime.context),
            )
        }

    async def _enterprise_node(
        self, state: RootAgentState, runtime: Runtime[RootAgentContext]
    ) -> dict[str, AgentExecutionResult]:
        return {
            "result": await self._delegate_enterprise(
                state["question"],
                runtime.context.request_context,
                self._relay_sink(runtime.context),
            )
        }

    @staticmethod
    def _out_of_scope_node(state: RootAgentState) -> dict[str, AgentExecutionResult]:
        return {
            "result": AgentExecutionResult(
                route=AgentRoute.OUT_OF_SCOPE,
                answer=(
                    "I’m the Commercial Bank organizational assistant. I can help you:\n"
                    "- find authorized information in internal policies, runbooks, incidents, "
                    "architecture, product specifications, and meeting notes;\n"
                    "- investigate and compare evidence across multiple internal sources;\n"
                    "- calculate approved counts, percentages, distributions, and trends; and\n"
                    "- look up approved employee, service-catalog, ownership, on-call, and "
                    "incident records.\n\n"
                    "I can’t help with unrelated general questions, entertainment, personal "
                    "advice, or actions outside these approved read-only duties."
                ),
            )
        }

    async def _synthesize_node(self, state: RootAgentState) -> dict[str, Any]:
        result = state["result"]
        if result is None:
            raise RuntimeError("The specialist node did not produce a result.")
        if self._synthesizer is not None and result.route is not AgentRoute.OUT_OF_SCOPE:
            evidence = "\n\n".join(
                f"[{index}] {item.title}\n{unwrap_evidence(item.content)[:2_000]}"
                for index, item in enumerate(result.evidence, start=1)
            )
            prompt = (
                f"User question: {state['question']}\n\n"
                f"Specialist result:\n{result.answer}\n\n"
                "Authorized evidence:\n"
                f"{evidence or 'No document evidence; use only the tool result.'}"
            )
            try:
                generated = await self._synthesizer.ainvoke(
                    {"messages": [{"role": "user", "content": prompt}]}
                )
                draft = GroundedAnswerDraft.model_validate(generated["structured_response"])
                result = result.model_copy(update={"answer": draft.answer})
            except Exception as exc:
                result = result.model_copy(
                    update={
                        "status": (
                            ResponseStatus.PARTIAL
                            if result.status is ResponseStatus.COMPLETE
                            else result.status
                        ),
                        "warnings": [
                            *result.warnings,
                            f"Model synthesis was unavailable: {type(exc).__name__}.",
                        ],
                    }
                )
        return {
            "result": result,
            "messages": [AIMessage(content=result.answer)],
        }

    @staticmethod
    def _message_history(messages: list[AnyMessage]) -> str:
        prior = messages[:-1]
        transcript = "\n".join(
            f"{message.type.title()}: {message.content}"
            for message in prior[-20:]
            if isinstance(message.content, str)
        )
        return transcript[-8_000:]

    @staticmethod
    def _plan_summary(route: AgentRoute) -> str:
        return {
            AgentRoute.DIRECT_KNOWLEDGE: "Search authorized knowledge and validate citations.",
            AgentRoute.RESEARCH: "Run bounded multi-source research and validate findings.",
            AgentRoute.ANALYSIS: "Retrieve authorized records and run controlled analysis.",
            AgentRoute.ENTERPRISE: "Call one approved read-only enterprise tool with fallback.",
            AgentRoute.OUT_OF_SCOPE: "Explain the assistant's approved capabilities and duties.",
        }[route]

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
        with suppress(RuntimeError):
            get_stream_writer()(transition.model_dump(mode="json"))
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
        question = question.strip()
        if len(question) >= KNOWLEDGE_QUERY_MAX_LENGTH:
            return question[:KNOWLEDGE_QUERY_MAX_LENGTH]
        if not summary:
            return question

        separator = "\n\nPrior conversation summary:\n"
        remaining = KNOWLEDGE_QUERY_MAX_LENGTH - len(question) - len(separator)
        if remaining <= 0:
            return question
        return f"{question}{separator}{summary[-remaining:]}"
