"""Controlled root orchestration used by the API and deterministic tests."""

from collections.abc import AsyncIterator, Awaitable, Callable, Hashable
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
    AnswerToken,
    EnterpriseExecution,
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

MAX_ROUTE_HANDOFFS = 1
"""How many times one request may be handed to a second specialist."""

HANDOFF_ROUTES: dict[AgentRoute, AgentRoute] = {
    # A decomposed investigation found nothing; try one broad unfiltered lookup.
    AgentRoute.RESEARCH: AgentRoute.DIRECT_KNOWLEDGE,
    # There is nothing to aggregate, so fall back to answering from documents.
    AgentRoute.ANALYSIS: AgentRoute.DIRECT_KNOWLEDGE,
    # The system of record has no matching row; the documents may still describe it.
    AgentRoute.ENTERPRISE: AgentRoute.DIRECT_KNOWLEDGE,
    # Deliberately no direct_knowledge escalation. An empty authorized lookup usually
    # means the corpus holds nothing this user may read, and fanning out to research
    # only surfaces unrelated documents that dress a clean refusal up as a partial
    # answer. Every hand-off here narrows toward a cheaper, broader single lookup.
}


class RootAgentState(TypedDict):
    messages: Annotated[list[AnyMessage], add_messages]
    question: str
    route: AgentRoute | None
    result: AgentExecutionResult | None
    attempted_routes: list[str]
    retry_route: AgentRoute | None
    handoff_notes: list[str]
    handoffs: int


@dataclass(frozen=True, slots=True)
class RootAgentContext:
    request_context: TrustedRequestContext
    transition_sink: TransitionSink | None = None
    conversation_summary: str = ""
    thread_id: str | None = None
    long_term_memory: str = ""


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
        builder.add_node("assess", self._assess_node)
        builder.add_node("synthesize", self._synthesize_node)
        specialists: dict[Hashable, str] = {
            AgentRoute.DIRECT_KNOWLEDGE.value: "direct_knowledge",
            AgentRoute.RESEARCH.value: "research",
            AgentRoute.ANALYSIS.value: "analysis",
            AgentRoute.ENTERPRISE.value: "enterprise",
        }
        builder.add_edge(START, "route")
        builder.add_conditional_edges(
            "route",
            self._route_after_classification,
            {**specialists, AgentRoute.OUT_OF_SCOPE.value: "out_of_scope"},
        )
        # Every specialist reports back to one assessment node, which owns the single
        # bounded hand-off. Out-of-scope has nothing to assess.
        for node in specialists.values():
            builder.add_edge(node, "assess")
        builder.add_edge("out_of_scope", "synthesize")
        builder.add_conditional_edges(
            "assess",
            self._route_after_assessment,
            {**specialists, "synthesize": "synthesize"},
        )
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
        long_term_memory: str = "",
    ) -> AgentExecutionResult:
        final = await self.graph.ainvoke(
            self._initial_state(question),
            config=self._graph_config(thread_id),
            context=RootAgentContext(
                request_context=context,
                transition_sink=transition_sink,
                conversation_summary=conversation_summary,
                thread_id=thread_id,
                long_term_memory=long_term_memory,
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
        long_term_memory: str = "",
    ) -> AsyncIterator[AgentTransition | AnswerToken | AgentExecutionResult]:
        """Stream native LangGraph activity and answer tokens, then the typed result."""

        graph_context = RootAgentContext(
            request_context=context,
            # The compiled graph owns production conversation history. The explicit
            # summary remains a compatibility input when no checkpointer is configured.
            conversation_summary=conversation_summary if self._checkpointer is None else "",
            thread_id=thread_id,
            long_term_memory=long_term_memory,
        )
        async for part in self.graph.astream(
            self._initial_state(question),
            config=self._graph_config(thread_id),
            context=graph_context,
            stream_mode=["custom", "updates"],
            version="v2",
        ):
            if part["type"] == "custom":
                data = part["data"]
                token = data.get("answer_token") if isinstance(data, dict) else None
                yield (
                    AnswerToken(text=token)
                    if isinstance(token, str) and token
                    else AgentTransition.model_validate(data)
                )
                continue
            for node, update in part["data"].items():
                if node != "synthesize":
                    continue
                result = update.get("result") if isinstance(update, dict) else None
                if result is not None:
                    yield AgentExecutionResult.model_validate(result)

    @staticmethod
    def _initial_state(question: str) -> dict[str, Any]:
        return {
            "messages": [HumanMessage(content=question)],
            "question": question,
            "route": None,
            "result": None,
            "attempted_routes": [],
            "retry_route": None,
            "handoff_notes": [],
            "handoffs": 0,
        }

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
        full_context = "\n\n".join(
            item for item in (summary, runtime.context.long_term_memory) if item
        )
        decision = await self._router.route(state["question"], full_context)
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
            "question": self._with_conversation_context(state["question"], full_context),
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

    async def _assess_node(
        self, state: RootAgentState, runtime: Runtime[RootAgentContext]
    ) -> dict[str, Any]:
        """Decide whether the specialist result is usable or needs one hand-off.

        The supervisor classifies intent from wording alone, so it cannot know whether
        the corpus actually holds the answer.  This node is the feedback edge: when a
        specialist returns no usable evidence, one different specialist gets a turn
        before the request is given up on.
        """

        result = state["result"]
        if result is None:
            raise RuntimeError("The specialist node did not produce a result.")
        attempted = [*state["attempted_routes"], result.route.value]
        retry = self._handoff_route(result, attempted, state["handoffs"])
        if retry is None:
            return {
                "attempted_routes": attempted,
                "retry_route": None,
                "result": self._settled_result(result, state["handoffs"]),
            }

        note = (
            f"The {result.route.value.replace('_', ' ')} specialist found no usable evidence, "
            f"so the request was handed off to the {retry.value.replace('_', ' ')} specialist."
        )
        await self._emit(
            runtime.context.transition_sink,
            AgentTransition(
                event_type="handoff_completed",
                agent=self.name,
                node="handoff_assessment",
                status="completed",
                message=note,
                metadata={
                    "route": retry.value,
                    "from_route": result.route.value,
                    "handoff_hop": state["handoffs"] + 1,
                    "plan_summary": self._plan_summary(retry),
                },
            ),
        )
        return {
            "attempted_routes": attempted,
            "retry_route": retry,
            "handoffs": state["handoffs"] + 1,
            "handoff_notes": [*state["handoff_notes"], note],
        }

    @classmethod
    def _settled_result(
        cls, result: AgentExecutionResult, handoffs: int
    ) -> AgentExecutionResult:
        """Keep the reported status honest about the specialist that had to be replaced.

        A hand-off only happens because the originally selected specialist failed, so the
        turn is never a clean success.  If the replacement also came back empty, every
        specialist that ran found nothing and the turn is insufficient evidence.
        """

        if handoffs == 0:
            return result
        if cls._returned_nothing_usable(result):
            return result.model_copy(update={"status": ResponseStatus.INSUFFICIENT_EVIDENCE})
        if result.status is ResponseStatus.COMPLETE:
            return result.model_copy(update={"status": ResponseStatus.PARTIAL})
        return result

    @staticmethod
    def _route_after_assessment(state: RootAgentState) -> str:
        retry = state["retry_route"]
        return "synthesize" if retry is None else retry.value

    @classmethod
    def _handoff_route(
        cls, result: AgentExecutionResult, attempted: list[str], handoffs: int
    ) -> AgentRoute | None:
        if handoffs >= MAX_ROUTE_HANDOFFS or not cls._returned_nothing_usable(result):
            return None
        candidate = HANDOFF_ROUTES.get(result.route)
        if candidate is None or candidate.value in attempted:
            return None
        return candidate

    @staticmethod
    def _returned_nothing_usable(result: AgentExecutionResult) -> bool:
        if result.route is AgentRoute.ENTERPRISE:
            # Enterprise answers are tool payloads rather than document evidence.
            return result.status is ResponseStatus.INSUFFICIENT_EVIDENCE
        return not result.evidence

    async def _synthesize_node(self, state: RootAgentState) -> dict[str, Any]:
        result = state["result"]
        if result is None:
            raise RuntimeError("The specialist node did not produce a result.")
        if state["handoff_notes"]:
            result = result.model_copy(
                update={"warnings": [*result.warnings, *state["handoff_notes"]]}
            )
        if self._synthesizer is not None and result.route is not AgentRoute.OUT_OF_SCOPE:
            result = await self._synthesized(state["question"], result)
        return {
            "result": result,
            "messages": [AIMessage(content=result.answer)],
        }

    async def _synthesized(
        self, question: str, result: AgentExecutionResult
    ) -> AgentExecutionResult:
        """Stream the grounded answer, forwarding each chunk as it is generated.

        Chunks are provisional: validation and persistence still run afterwards and the
        terminal response remains authoritative.  A synthesis failure keeps the
        deterministic specialist answer so the turn degrades instead of breaking.
        """

        evidence = "\n\n".join(
            f"[{index}] {item.title}\n{unwrap_evidence(item.content)[:2_000]}"
            for index, item in enumerate(result.evidence, start=1)
        )
        prompt = (
            f"User question: {question}\n\n"
            f"Specialist result:\n{result.answer}\n\n"
            "Authorized evidence:\n"
            f"{evidence or 'No document evidence; use only the tool result.'}"
        )
        chunks: list[str] = []
        try:
            async for chunk in self._synthesizer.astream(prompt):
                chunks.append(chunk)
                self._emit_answer_token(chunk)
            answer = "".join(chunks).strip()
            if not answer:
                raise ValueError("The response agent produced an empty answer.")
            return result.model_copy(update={"answer": answer})
        except Exception as exc:
            return result.model_copy(
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

    @staticmethod
    def _emit_answer_token(text: str) -> None:
        with suppress(RuntimeError):
            get_stream_writer()({"answer_token": text})

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
        # An empty system-of-record read reports insufficient evidence; the assessment
        # node decides whether a document lookup should be tried instead.
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
