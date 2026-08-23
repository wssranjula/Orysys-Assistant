"""Autonomous root agent whose entire tool surface is specialist delegation.

The root is a tool-calling loop. It decides which specialist to consult, how to phrase
the objective, whether one consultation was enough, and how to write the final answer.
That replaces the classifier, the four specialist nodes, the hand-off feedback edge,
and the separate synthesis pass with one loop the model drives.

What the model does not decide is the boundary. It cannot reach a document, a record,
or a system directly: every capability sits behind a specialist that owns a
gateway-scoped toolbox. Delegation depth is a middleware budget rather than prompt
guidance. Most importantly, the route, status, evidence, and citations this module
returns are rebuilt from the delegations that actually ran, so the answer's provenance
describes observed work rather than anything the model claimed about its own work.
"""

from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import suppress
from dataclasses import dataclass, field
from typing import Any
from uuid import uuid4

from langchain.agents import create_agent
from langchain.agents.middleware import TodoListMiddleware, ToolCallLimitMiddleware
from langchain_core.tools import StructuredTool
from langgraph.config import get_stream_writer
from langgraph.runtime import get_runtime
from langsmith import traceable
from pydantic import BaseModel, Field

from orysys_assistant.agent.gateway_tools import (
    SpecialistOutcome,
    TransitionSink,
    budget_middleware,
    final_text,
)
from orysys_assistant.agent.models import (
    AgentExecutionResult,
    AgentRoute,
    AgentTransition,
    AnswerToken,
)
from orysys_assistant.agent.research_agent import ResearchSubagent, plan_activity_middleware
from orysys_assistant.agent.subagents import (
    AnalysisSubagent,
    EnterpriseToolSubagent,
    KnowledgeSubagent,
    evidence_summary,
)
from orysys_assistant.domain.models import Citation, ResponseStatus
from orysys_assistant.retrieval.models import Evidence
from orysys_assistant.security.models import TrustedRequestContext

ROOT_QUESTION_MAX_CHARACTERS = 12_000
"""Bound on the question plus conversation context handed to the root loop."""

CAPABILITIES_ANSWER = (
    "I’m the Commercial Bank organizational assistant. I can help you:\n"
    "- find authorized information in internal policies, runbooks, incidents, "
    "architecture, product specifications, and meeting notes;\n"
    "- investigate and compare evidence across multiple internal sources;\n"
    "- calculate approved counts, percentages, distributions, and trends; and\n"
    "- look up approved employee, service-catalog, ownership, on-call, and "
    "incident records.\n\n"
    "I can’t help with unrelated general questions, entertainment, personal "
    "advice, or actions outside these approved read-only duties."
)
"""The answer returned whenever the root resolves a turn without consulting anyone.

Answering from the model's own parameters is the one failure this system cannot detect
downstream: there is no evidence ledger to check the claim against. So an undelegated
turn is treated as out of scope by construction rather than trusted as a shortcut.
"""

ROOT_SYSTEM_PROMPT = """You are the root agent for Commercial Bank's internal organizational
assistant. You never answer from your own knowledge of banking, policy, or the organization.
Everything you assert must come from a specialist you consulted on this turn.

You have four specialists. Choose by what the question needs:
- consult_knowledge_specialist: a focused policy or factual lookup one document can settle.
- consult_research_specialist: investigation, comparison, recurring patterns, contradictions,
  or synthesis that needs several documents. Use this when the question spans document
  families such as incidents, meeting notes, runbooks, architecture, policies, or
  specifications, even if it also mentions incident records.
- consult_analysis_specialist: counts, percentages, rankings, distributions, or trends.
- consult_enterprise_specialist: a system-of-record lookup in the employee directory, the
  service catalog, or the incident system.

Each specialist is stateless and cannot see the user or this conversation, so put the full
objective in the request, including any context from earlier turns that it needs.

If a specialist reports that it found nothing, consider whether a different one would have the
answer — a missing catalog record may still be described in the documents — and consult it.
Do not re-consult a specialist that already came back empty for the same objective. Consult
several specialists in one turn when the question genuinely has independent parts.

If the request is a greeting, a question about what you can do, or anything outside these
approved read-only duties, do not consult anyone; say so directly.

Write the final answer yourself from what the specialists reported. Cite with the exact
bracketed markers listed under "Authorized evidence" in their replies, placing each marker on
the statement it supports. Never invent a marker, and never cite evidence you were not shown.
Say plainly which parts of the question the evidence did not settle. Do not narrate your
process or announce which specialist you are about to consult; just consult it and then answer.
"""


class DelegationRequest(BaseModel):
    """The objective handed to a specialist, authored by the root model."""

    request: str = Field(
        description=(
            "The complete objective for the specialist, written as a standalone "
            "instruction. It cannot see the user or the conversation, so include every "
            "detail it needs, and state what it should report back."
        ),
        min_length=1,
        max_length=4_000,
    )


@dataclass(frozen=True, slots=True)
class DelegationRecord:
    """One consultation, as observed rather than as described."""

    route: AgentRoute
    grounded: bool
    evidence_added: int


@dataclass(slots=True)
class DelegationLedger:
    """Everything the specialists actually produced during one request.

    Citation markers are positions in this ledger, so the numbering a specialist is told
    to use is the numbering the returned citations carry. Evidence is keyed by identifier
    and appended once, which keeps markers stable when two specialists surface the same
    record.
    """

    evidence: dict[str, Evidence] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    records: list[DelegationRecord] = field(default_factory=list)

    def record(self, route: AgentRoute, outcome: SpecialistOutcome) -> None:
        added = 0
        for item in outcome.evidence:
            if item.evidence_id not in self.evidence:
                self.evidence[item.evidence_id] = item
                added += 1
        for warning in outcome.warnings:
            if warning not in self.warnings:
                self.warnings.append(warning)
        self.records.append(DelegationRecord(route, outcome.grounded, added))

    def ordered_evidence(self) -> list[Evidence]:
        return list(self.evidence.values())

    def citations(self) -> list[Citation]:
        return [
            Citation(
                citation_id=str(index),
                evidence_id=item.evidence_id,
                document_id=item.document_id,
                title=item.title,
                chunk_id=item.chunk_id,
                source_path=str(item.metadata["source_path"]),
            )
            for index, item in enumerate(self.ordered_evidence(), start=1)
        ]

    def route(self) -> AgentRoute:
        """Report the specialist that carried the turn, preferring one that delivered.

        A consultation that produced evidence is what the answer actually rests on, so
        it names the route even when the model tried something else first. This keeps
        the reported route tied to observed work: a turn with document evidence can
        never report a route that would let it skip grounding validation.
        """

        if not self.records:
            return AgentRoute.OUT_OF_SCOPE
        for item in self.records:
            if item.evidence_added:
                return item.route
        return self.records[-1].route

    def status(self) -> ResponseStatus:
        if not self.records:
            return ResponseStatus.COMPLETE
        if not any(item.grounded for item in self.records):
            return ResponseStatus.INSUFFICIENT_EVIDENCE
        if self.warnings or not all(item.grounded for item in self.records):
            return ResponseStatus.PARTIAL
        return ResponseStatus.COMPLETE


@dataclass(frozen=True, slots=True)
class RootAgentContext:
    """Per-request runtime context for the root loop, never visible to the model."""

    request_context: TrustedRequestContext
    ledger: DelegationLedger
    transition_sink: TransitionSink | None = None
    thread_id: str | None = None


SpecialistRunner = Callable[
    [str, TrustedRequestContext, TransitionSink, str | None], Awaitable[SpecialistOutcome]
]


@dataclass(frozen=True, slots=True)
class SpecialistBinding:
    """How one specialist is published to the root model as a single tool."""

    route: AgentRoute
    tool_name: str
    agent_name: str
    description: str
    plan_summary: str
    run: SpecialistRunner


class RootOrchestrator:
    name = "root_deep_agent"

    def __init__(
        self,
        *,
        model: Any,
        knowledge: KnowledgeSubagent,
        research: ResearchSubagent,
        analysis: AnalysisSubagent,
        enterprise: EnterpriseToolSubagent,
        checkpointer: Any = None,
        max_tool_calls: int = 8,
        max_model_calls: int = 6,
    ) -> None:
        self._bindings = _bindings(knowledge, research, analysis, enterprise)
        self._checkpointer = checkpointer
        tools = [_delegation_tool(binding) for binding in self._bindings]
        # Typed as Any because the compiled graph's overloads are keyed on literal state
        # and stream-mode types that a runtime-built input mapping cannot satisfy.
        self.graph: Any = create_agent(
            model=model,
            tools=tools,
            system_prompt=ROOT_SYSTEM_PROMPT,
            context_schema=RootAgentContext,
            middleware=[
                TodoListMiddleware(),
                plan_activity_middleware(self.name, "root_planner"),
                # One consultation per specialist. The prompt asks the model not to
                # re-ask a specialist that came back empty; this makes it so, and caps
                # the number of specialists a single turn can spend at four.
                *[
                    ToolCallLimitMiddleware(
                        tool_name=tool.name, run_limit=1, exit_behavior="continue"
                    )
                    for tool in tools
                ],
                *budget_middleware(max_tool_calls=max_tool_calls, max_model_calls=max_model_calls),
            ],
            checkpointer=checkpointer,
            name="root-orchestrator",
        )

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
        ledger = DelegationLedger()
        await self._started(transition_sink)
        state = await self.graph.ainvoke(
            self._input(question, conversation_summary, long_term_memory),
            config=self._config(thread_id),
            context=RootAgentContext(
                request_context=context,
                ledger=ledger,
                transition_sink=transition_sink,
                thread_id=thread_id,
            ),
        )
        return _result(final_text(state), ledger)

    async def stream(
        self,
        question: str,
        context: TrustedRequestContext,
        conversation_summary: str = "",
        thread_id: str | None = None,
        long_term_memory: str = "",
    ) -> AsyncIterator[AgentTransition | AnswerToken | AgentExecutionResult]:
        """Stream activity and the root's own answer tokens, then the typed result."""

        ledger = DelegationLedger()
        yield _started_transition()
        answer = ""
        async for mode, part in self.graph.astream(
            self._input(
                question,
                # The compiled graph owns production conversation history. The explicit
                # summary remains a compatibility input when no checkpointer is set.
                conversation_summary if self._checkpointer is None else "",
                long_term_memory,
            ),
            config=self._config(thread_id),
            context=RootAgentContext(
                request_context=context,
                ledger=ledger,
                transition_sink=None,
                thread_id=thread_id,
            ),
            stream_mode=["custom", "messages"],
        ):
            if mode == "custom":
                yield AgentTransition.model_validate(part)
                continue
            text = _chunk_text(part)
            if text:
                answer += text
                yield AnswerToken(text=text)
        yield _result(answer.strip(), ledger)

    async def _started(self, sink: TransitionSink | None) -> None:
        await _publish(sink, _started_transition())

    @staticmethod
    def _input(question: str, conversation_summary: str, long_term_memory: str) -> dict[str, Any]:
        context = "\n\n".join(item for item in (conversation_summary, long_term_memory) if item)
        return {
            "messages": [{"role": "user", "content": _with_conversation_context(question, context)}]
        }

    def _config(self, thread_id: str | None) -> dict[str, Any] | None:
        if self._checkpointer is None:
            return None
        return {"configurable": {"thread_id": thread_id or str(uuid4())}}


def _bindings(
    knowledge: KnowledgeSubagent,
    research: ResearchSubagent,
    analysis: AnalysisSubagent,
    enterprise: EnterpriseToolSubagent,
) -> list[SpecialistBinding]:
    async def run_knowledge(
        request: str, context: TrustedRequestContext, sink: TransitionSink, thread_id: str | None
    ) -> SpecialistOutcome:
        return await knowledge.run(request, context, sink)

    async def run_research(
        request: str, context: TrustedRequestContext, sink: TransitionSink, thread_id: str | None
    ) -> SpecialistOutcome:
        return await research.run(request, context, sink, thread_id)

    async def run_analysis(
        request: str, context: TrustedRequestContext, sink: TransitionSink, thread_id: str | None
    ) -> SpecialistOutcome:
        return await analysis.run(request, context, sink)

    async def run_enterprise(
        request: str, context: TrustedRequestContext, sink: TransitionSink, thread_id: str | None
    ) -> SpecialistOutcome:
        return await enterprise.run(request, context, sink)

    return [
        SpecialistBinding(
            route=AgentRoute.DIRECT_KNOWLEDGE,
            tool_name="consult_knowledge_specialist",
            agent_name=knowledge.name,
            description=(
                "Consult the knowledge specialist for a focused lookup in the authorized "
                "document corpus — policies, runbooks, incidents, architecture, product "
                "specifications, and meeting notes. Best when one document settles the "
                "question. Returns its findings and the evidence you may cite."
            ),
            plan_summary="Search authorized knowledge and validate citations.",
            run=run_knowledge,
        ),
        SpecialistBinding(
            route=AgentRoute.RESEARCH,
            tool_name="consult_research_specialist",
            agent_name=research.name,
            description=(
                "Consult the research specialist to investigate across many documents: "
                "comparisons, recurring causes, contradictions, timelines, or synthesis "
                "spanning several document families. It plans, searches in parallel, and "
                "re-plans on what it finds. Slower than a lookup; use it when one document "
                "cannot answer. Returns grounded findings and the evidence you may cite."
            ),
            plan_summary="Run bounded multi-source research and validate findings.",
            run=run_research,
        ),
        SpecialistBinding(
            route=AgentRoute.ANALYSIS,
            tool_name="consult_analysis_specialist",
            agent_name=analysis.name,
            description=(
                "Consult the analysis specialist for quantitative questions over the "
                "document corpus — counts, shares, rankings, distributions, and trends. It "
                "retrieves the population and runs a controlled aggregation over it. State "
                "which figure you need. Returns the computed result and citable evidence."
            ),
            plan_summary="Retrieve authorized records and run controlled analysis.",
            run=run_analysis,
        ),
        SpecialistBinding(
            route=AgentRoute.ENTERPRISE,
            tool_name="consult_enterprise_specialist",
            agent_name=enterprise.name,
            description=(
                "Consult the enterprise specialist for a live system-of-record lookup: the "
                "employee directory, the service catalog, or the incident system. Use it for "
                "ownership, on-call, staffing, and single incident records. It reads systems, "
                "not documents, so it returns field values rather than citable evidence."
            ),
            plan_summary="Call approved read-only enterprise tools with fallback.",
            run=run_enterprise,
        ),
    ]


def _delegation_tool(binding: SpecialistBinding) -> StructuredTool:
    """Publish one specialist as a tool the root can call, narrating it as it runs.

    The activity stream is written here rather than by the model, so the panel reflects
    consultations that happened. An authorization denial is deliberately left to
    propagate: a refused capability ends the request instead of becoming a tool result
    the model could route around.
    """

    async def call(request: str) -> str:
        runtime = get_runtime(RootAgentContext)
        context = runtime.context
        relay = _relay_sink(context.transition_sink)
        await relay(_routing_transition(binding, context.ledger.records))
        await relay(_subagent_transition(binding.agent_name, "started"))
        outcome = await binding.run(request, context.request_context, relay, context.thread_id)
        context.ledger.record(binding.route, outcome)
        await relay(_subagent_transition(binding.agent_name, "completed"))
        return _reply(outcome, context.ledger)

    return StructuredTool.from_function(
        coroutine=call,
        name=binding.tool_name,
        description=binding.description,
        args_schema=DelegationRequest,
    )


def _reply(outcome: SpecialistOutcome, ledger: DelegationLedger) -> str:
    """Hand the specialist's report back with the citation markers it earned.

    Markers are assigned from the ledger, not by the specialist and not by the root, so
    the number the model is told to write is the number the returned citation carries.
    """

    sections = [outcome.report]
    evidence = ledger.ordered_evidence()
    if evidence:
        sections.append(
            "Authorized evidence you may cite (use these exact markers):\n"
            + "\n".join(f"[{index}] {item.title}" for index, item in enumerate(evidence, start=1))
        )
    if outcome.warnings:
        sections.append("Limitations: " + " ".join(outcome.warnings))
    if not outcome.grounded:
        sections.append(
            "This specialist found nothing usable. Do not consult it again for this "
            "objective; either try a different specialist or say the answer is unavailable."
        )
    return "\n\n".join(sections)


def _result(answer: str, ledger: DelegationLedger) -> AgentExecutionResult:
    """Assemble the turn from what ran, not from what the model said it did."""
    route = ledger.route()
    evidence = ledger.ordered_evidence()
    if route is AgentRoute.OUT_OF_SCOPE:
        return AgentExecutionResult(route=route, answer=CAPABILITIES_ANSWER)
    return AgentExecutionResult(
        route=route,
        answer=answer or evidence_summary(evidence),
        status=ledger.status(),
        citations=ledger.citations(),
        warnings=list(ledger.warnings),
        evidence_ids=[item.evidence_id for item in evidence],
        evidence=evidence,
    )


def _relay_sink(sink: TransitionSink | None) -> TransitionSink:
    """Fan one specialist's activity onto the graph stream and any direct sink."""
    writer: Callable[[Any], None] | None = None
    with suppress(RuntimeError):
        writer = get_stream_writer()

    async def relay(transition: AgentTransition) -> None:
        if writer is not None:
            writer(transition.model_dump(mode="json"))
        if sink is not None:
            await sink(transition)

    return relay


async def _publish(sink: TransitionSink | None, transition: AgentTransition) -> None:
    with suppress(RuntimeError):
        get_stream_writer()(transition.model_dump(mode="json"))
    if sink is not None:
        await sink(transition)


def _started_transition() -> AgentTransition:
    return AgentTransition(
        event_type="agent_started",
        agent=RootOrchestrator.name,
        node="intent_routing",
        status="started",
        message="Root agent is deciding which specialists the request needs.",
    )


def _routing_transition(
    binding: SpecialistBinding, previous: list[DelegationRecord]
) -> AgentTransition:
    """Narrate a delegation, distinguishing the first choice from a recovery.

    A second consultation after an empty one is the hand-off the graph used to encode as
    a fixed edge. It is now the model's decision, so it is reported when it happens
    rather than assumed from a table.
    """

    empty = [item for item in previous if not item.grounded]
    if not previous or not empty:
        return AgentTransition(
            event_type="routing_completed",
            agent=RootOrchestrator.name,
            node="intent_routing",
            status="completed",
            message=f"Selected {binding.route.value} route.",
            metadata={"route": binding.route.value, "plan_summary": binding.plan_summary},
        )
    origin = empty[-1].route
    return AgentTransition(
        event_type="handoff_completed",
        agent=RootOrchestrator.name,
        node="handoff_assessment",
        status="completed",
        message=(
            f"The {origin.value.replace('_', ' ')} specialist found no usable evidence, "
            f"so the request was handed off to the "
            f"{binding.route.value.replace('_', ' ')} specialist."
        ),
        metadata={
            "route": binding.route.value,
            "from_route": origin.value,
            "handoff_hop": len(empty),
            "plan_summary": binding.plan_summary,
        },
    )


def _subagent_transition(agent: str, status: str) -> AgentTransition:
    return AgentTransition(
        event_type=f"subagent_{status}",
        agent=agent,
        node="delegation",
        status=status,
        message=f"{agent.replace('_', ' ').title()} {status}.",
    )


def _chunk_text(part: Any) -> str:
    """Read the root's own answer text out of one streamed message chunk.

    Specialist loops run inside a delegation tool, so their prose arrives tagged with the
    tool node. Filtering on the root's model node is what keeps a specialist's internal
    reasoning out of the answer the user sees.
    """

    if not isinstance(part, tuple) or len(part) != 2:
        return ""
    chunk, metadata = part
    if not isinstance(metadata, dict) or metadata.get("langgraph_node") != "model":
        return ""
    content = getattr(chunk, "content", None)
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(
            str(block.get("text", ""))
            for block in content
            if isinstance(block, dict) and block.get("type") in {"text", None}
        )
    return ""


def _with_conversation_context(question: str, summary: str) -> str:
    question = question.strip()
    if len(question) >= ROOT_QUESTION_MAX_CHARACTERS:
        return question[:ROOT_QUESTION_MAX_CHARACTERS]
    if not summary:
        return question

    separator = "\n\nPrior conversation summary:\n"
    remaining = ROOT_QUESTION_MAX_CHARACTERS - len(question) - len(separator)
    if remaining <= 0:
        return question
    return f"{question}{separator}{summary[-remaining:]}"
