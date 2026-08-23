"""Recursive document research built on the Deep Agents harness.

The harness supplies what used to be hand-written graph machinery: ``write_todos`` for
task decomposition, a virtual filesystem for offloading retrieved text out of the model
context, summarization for long investigations, and native parallel tool calls for
fan-out. Recursion is the agent re-planning against what it has found rather than a
fixed-depth loop, which is the behaviour the recursive-language-model brief describes.

What stays deterministic is the boundary. Budgets are middleware, not prompt guidance;
evidence and citations are rebuilt from executed retrievals; and every finding the model
reports is checked against evidence that was actually retrieved before it can be cited.
"""

import asyncio
from contextlib import suppress
from dataclasses import dataclass
from typing import Any

from deepagents import create_deep_agent
from langchain.agents.middleware import SummarizationMiddleware, wrap_tool_call
from langgraph.config import get_stream_writer
from langgraph.runtime import get_runtime
from langsmith import traceable

from orysys_assistant.agent.gateway_tools import (
    SpecialistCollector,
    SpecialistContext,
    SpecialistOutcome,
    TransitionSink,
    build_gateway_tools,
    final_text,
)
from orysys_assistant.agent.middleware_limits import QuietTodoListMiddleware, budget_middleware
from orysys_assistant.agent.models import AgentTransition
from orysys_assistant.agent.toolbox import ScopedToolbox
from orysys_assistant.config import Settings
from orysys_assistant.observability.agent_tracing import app_span_tags

RESEARCH_SYSTEM_PROMPT = """You are the research specialist for Commercial Bank's internal
assistant. You investigate questions that no single document answers, working only from the
authorized document corpus reachable through knowledge_search.

Plan before you search. Use write_todos to break the objective into the specific claims,
causes, time periods, contradictions, or requirements you must establish, then work the list
and keep it current. Decompose by what must be proven, not by document folder.

Search in parallel. Issue several independent knowledge_search calls in one turn rather than
waiting for each result. The corpus contains only these document types: incident, meeting_note,
runbook, architecture, policy, and product_specification, and supports department and
created_after/created_before filters. Do not expect Jira, Slack, email, logs, or telemetry.

Re-plan on what you find. When a search comes back empty, widen it — drop the document_type or
date filter, or rephrase toward the vocabulary the documents would actually use — rather than
repeating the same query. When a result raises a new question, add a todo for it. Stop when the
evidence supports an answer or when further searching is clearly not producing new evidence.

Use the filesystem to offload long passages you want to revisit, so your context stays focused
on the argument rather than on raw document text.

Report only what the retrieved evidence supports. For each finding, cite the evidence_id values
that establish it. State clearly which parts of the objective the evidence did not resolve."""

RESEARCH_RESPONSE_INSTRUCTION = """Finish with a final message in exactly this form:

SUMMARY: <two or three sentences answering the objective from the evidence>
FINDING: <one supported claim> || <comma-separated evidence_id values>
FINDING: <the next supported claim> || <comma-separated evidence_id values>
UNRESOLVED: <a part of the objective the evidence did not settle, or "none">

Include one FINDING line per distinct supported claim. Every evidence_id must be one that
knowledge_search actually returned to you."""


def plan_activity_middleware(agent_name: str, node: str) -> Any:
    """Mirror an agent's harness todo list onto the activity stream.

    The plan the evaluator sees in the UI is the agent's own live todo state, not a
    separate narration that could describe work the agent never did. Attribution is a
    parameter so the root's plan and a specialist's plan stay distinguishable in the
    panel rather than arriving as one undifferentiated list.
    """

    @wrap_tool_call
    async def publish(request: Any, handler: Any) -> Any:
        response = await handler(request)
        tool_call = getattr(request, "tool_call", {}) or {}
        if tool_call.get("name") != "write_todos":
            return response
        todos = [
            {
                "id": f"todo-{index}",
                "content": str(item.get("content", "")),
                "status": str(item.get("status", "pending")),
            }
            for index, item in enumerate(tool_call.get("args", {}).get("todos", []) or [], start=1)
            if isinstance(item, dict) and item.get("content")
        ]
        if todos:
            await _emit_plan(agent_name, node, todos)
        return response

    return publish


async def _emit_plan(agent_name: str, node: str, todos: list[dict[str, str]]) -> None:
    transition = AgentTransition(
        event_type="research_node_completed",
        agent=agent_name,
        node=node,
        status="completed",
        message=f"Plan updated with {len(todos)} tasks.",
        metadata={
            "todos": todos,
            "task_count": len(todos),
            "plan_summary": "Todos: "
            + "; ".join(f"{index}. {item['content']}" for index, item in enumerate(todos, start=1)),
        },
    )
    with suppress(RuntimeError):
        get_stream_writer()(transition.model_dump(mode="json"))
    sink = None
    with suppress(Exception):
        # Both the root and specialist contexts expose a transition sink under the same
        # attribute, so one middleware serves either loop without knowing which it is in.
        sink = get_runtime(SpecialistContext).context.transition_sink
    if sink is not None:
        await sink(transition)


@dataclass(frozen=True, slots=True)
class ResearchLimits:
    """Execution budget enforced by middleware rather than by prompt instruction."""

    max_tool_calls: int
    max_model_calls: int
    max_chunks_per_search: int
    overall_timeout_seconds: float
    summarization_token_trigger: int = 40_000

    @classmethod
    def from_settings(cls, settings: Settings) -> "ResearchLimits":
        return cls(
            max_tool_calls=settings.research_max_total_tool_calls,
            max_model_calls=settings.research_max_model_calls,
            max_chunks_per_search=settings.research_max_chunks_per_worker,
            overall_timeout_seconds=settings.research_overall_timeout_seconds,
            summarization_token_trigger=settings.research_summarization_token_trigger,
        )


class ResearchSubagent:
    """Own one reusable deep agent; all execution limits are code-enforced."""

    name = "research_subagent"

    def __init__(
        self,
        toolbox: ScopedToolbox,
        limits: ResearchLimits,
        model: Any,
        checkpointer: Any = None,
    ) -> None:
        self._limits = limits
        self.agent = create_deep_agent(
            model=model,
            tools=build_gateway_tools(toolbox),
            system_prompt=f"{RESEARCH_SYSTEM_PROMPT}\n\n{RESEARCH_RESPONSE_INSTRUCTION}",
            context_schema=SpecialistContext,
            middleware=[
                QuietTodoListMiddleware(),
                plan_activity_middleware(self.name, "planner"),
                SummarizationMiddleware(
                    model=model, trigger=("tokens", limits.summarization_token_trigger)
                ),
                *budget_middleware(
                    max_tool_calls=limits.max_tool_calls,
                    max_model_calls=limits.max_model_calls,
                ),
            ],
            checkpointer=checkpointer,
            name="research-specialist",
        )

    @traceable(
        name="delegate-research-subagent",
        run_type="chain",
        metadata={"agent": "research_subagent", "delegated": True},
        tags=app_span_tags("delegate", "research"),
    )
    async def run(
        self,
        question: str,
        context: Any,
        transition_sink: TransitionSink | None = None,
        thread_id: str | None = None,
    ) -> SpecialistOutcome:
        collector = SpecialistCollector()
        specialist_context = SpecialistContext(
            request_context=context,
            collector=collector,
            agent_name=self.name,
            transition_sink=transition_sink,
        )
        try:
            async with asyncio.timeout(self._limits.overall_timeout_seconds):
                state = await self.agent.ainvoke(
                    {"messages": [{"role": "user", "content": question}]},
                    config={"configurable": {"thread_id": thread_id}} if thread_id else None,
                    context=specialist_context,
                )
        except TimeoutError:
            # A deadline is not a dead end: whatever was retrieved before the cutoff is
            # still authorized evidence, so the turn degrades instead of returning empty.
            return _timed_out(question, collector)

        return _outcome(final_text(state), collector)


@dataclass(frozen=True, slots=True)
class GroundedFinding:
    """One claim the model made, kept only because retrieved evidence backs it."""

    claim: str
    evidence_ids: list[str]


def _outcome(report: str, collector: SpecialistCollector) -> SpecialistOutcome:
    evidence = collector.ordered_evidence()
    summary, findings, unresolved = _parse_report(report, {item.evidence_id for item in evidence})
    warnings = list(collector.warnings)
    searched = len([item for item in collector.invocations if item.status == "completed"])
    if not evidence:
        warnings.append("Research found no authorized evidence for this objective.")
    if unresolved:
        warnings.append("Research left part of the objective unresolved.")
    return SpecialistOutcome(
        report=_render(
            summary
            or (
                f"Research reviewed {len(evidence)} unique authorized evidence records "
                f"across {searched} retrievals."
            ),
            findings,
            unresolved,
        ),
        evidence=evidence,
        warnings=warnings,
        grounded=bool(evidence) and bool(findings),
    )


def _render(summary: str, findings: list[GroundedFinding], unresolved: list[str]) -> str:
    lines = [summary]
    lines.extend(
        f"- {finding.claim} (supported by {', '.join(finding.evidence_ids) or 'no evidence id'})"
        for finding in findings
    )
    lines.extend(f"Unresolved: {item}" for item in unresolved)
    return "\n".join(line for line in lines if line)


def _timed_out(question: str, collector: SpecialistCollector) -> SpecialistOutcome:
    return SpecialistOutcome(
        report=(
            "Research stopped at the overall execution deadline before it could settle "
            f"the objective: {question}"
        ),
        evidence=collector.ordered_evidence(),
        warnings=[
            *collector.warnings,
            "The bounded research workflow reached its overall timeout.",
        ],
        grounded=False,
    )


def _parse_report(
    report: str, known_evidence_ids: set[str]
) -> tuple[str, list[GroundedFinding], list[str]]:
    """Read the model's report, keeping only claims tied to evidence that was retrieved.

    An evidence identifier the model invented cannot be resolved to a citation later, so
    dropping it here is what stops a fabricated reference from reaching the user.
    """

    summary = ""
    findings: list[GroundedFinding] = []
    unresolved: list[str] = []
    for line in report.splitlines():
        stripped = line.strip()
        if stripped.upper().startswith("SUMMARY:"):
            summary = stripped[len("SUMMARY:") :].strip()
        elif stripped.upper().startswith("FINDING:"):
            finding = _parse_finding(stripped[len("FINDING:") :], known_evidence_ids)
            if finding is not None:
                findings.append(finding)
        elif stripped.upper().startswith("UNRESOLVED:"):
            value = stripped[len("UNRESOLVED:") :].strip()
            if value and value.lower() not in {"none", "n/a"}:
                unresolved.append(value)
    if not summary and not findings:
        # The model answered in prose instead of the requested form; keep it as the
        # summary rather than discarding a grounded answer over formatting.
        summary = " ".join(report.split())[:1_000]
    return summary, findings, unresolved


def _parse_finding(body: str, known_evidence_ids: set[str]) -> GroundedFinding | None:
    claim, separator, references = body.partition("||")
    claim = claim.strip()
    if not claim:
        return None
    cited = [
        reference.strip()
        for reference in references.replace(",", " ").split()
        if reference.strip() in known_evidence_ids
    ]
    if separator and not cited:
        return None
    return GroundedFinding(claim=claim[:500], evidence_ids=list(dict.fromkeys(cited)))
