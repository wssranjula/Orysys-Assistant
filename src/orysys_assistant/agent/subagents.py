"""Autonomous specialists with small, gateway-enforced tool surfaces.

Each specialist is a tool-calling loop: the model decides which approved tool to call,
with what arguments, and how many times within its budget. What it cannot decide is the
boundary. Tool visibility comes from a ``ScopedToolbox``, permission from the gateway's
RBAC check, and the returned evidence, citations, and analysis figures are rebuilt from
the collector's record of executed calls rather than from anything the model wrote.
"""

import asyncio
from typing import Any

from langchain.agents import create_agent
from langsmith import traceable

from orysys_assistant.agent.gateway_tools import (
    SpecialistCollector,
    SpecialistContext,
    SpecialistOutcome,
    TransitionSink,
    budget_middleware,
    build_gateway_tools,
    final_text,
)
from orysys_assistant.agent.models import AnalysisResult
from orysys_assistant.agent.toolbox import ScopedToolbox
from orysys_assistant.guardrails.content import unwrap_evidence
from orysys_assistant.retrieval.models import Evidence

KNOWLEDGE_SYSTEM_PROMPT = """You are the knowledge specialist for Commercial Bank's internal
assistant. Answer the question strictly from the authorized document corpus.

Search first, then answer. If the first search returns nothing useful, try once or twice more
with different wording or a narrower document_type filter before concluding the corpus has no
answer — users rarely phrase questions the way documents are written. Run independent searches
in parallel in a single turn rather than one at a time.

Ground every statement in retrieved evidence and never rely on general knowledge about banking.
If the authorized evidence does not answer the question, say so plainly instead of guessing.
Keep the answer concise and factual."""

ANALYSIS_SYSTEM_PROMPT = """You are the analysis specialist for Commercial Bank's internal
assistant. You answer quantitative questions — counts, shares, rankings, distributions, and
trends — over the authorized document corpus.

Work in two stages. First use knowledge_search to gather the records the question is about,
searching in parallel and with filters when that produces a cleaner population. Then call
structured_analysis over those records, choosing the operation and field that actually answer
the question rather than defaulting to a generic count.

The records you pass to structured_analysis must be built from retrieved evidence metadata, and
every record must contain the field you are aggregating. If the retrieved population is too
small or too inconsistent to support a defensible figure, say so rather than computing a
misleading number. Report the figures the tool returned; never estimate or adjust them
yourself."""

ENTERPRISE_SYSTEM_PROMPT = """You are the enterprise systems specialist for Commercial Bank's
internal assistant. You answer questions from live systems of record: the employee directory,
the service catalog, and the incident system.

Choose the tool that matches the question. Use a get_* tool when the request contains an exact
identifier, and a search_* tool otherwise. Chain calls when the answer needs it — for example,
search the service catalog to find a service, then look up the owning employee — and issue
independent lookups in parallel in a single turn.

If a lookup returns nothing, the record does not exist in that system; say so rather than
substituting a guess. If a tool reports that it was unavailable, state that the system could not
be reached. Report only field values these tools returned."""


class KnowledgeSubagent:
    """Focused document question answering with model-chosen queries and filters."""

    name = "knowledge_subagent"

    def __init__(
        self,
        toolbox: ScopedToolbox,
        model: Any,
        *,
        max_tool_calls: int = 6,
        max_model_calls: int = 5,
        overall_timeout_seconds: float = 45,
    ) -> None:
        self._overall_timeout_seconds = overall_timeout_seconds
        self._agent = create_agent(
            model=model,
            tools=build_gateway_tools(toolbox),
            system_prompt=KNOWLEDGE_SYSTEM_PROMPT,
            context_schema=SpecialistContext,
            middleware=budget_middleware(
                max_tool_calls=max_tool_calls, max_model_calls=max_model_calls
            ),
            name="knowledge-specialist",
        )

    @traceable(
        name="delegate-knowledge-subagent",
        run_type="chain",
        metadata={"agent": "knowledge_subagent", "delegated": True},
    )
    async def run(
        self,
        question: str,
        context: Any,
        transition_sink: TransitionSink | None = None,
    ) -> SpecialistOutcome:
        collector = SpecialistCollector()
        report = await _run_agent(
            self._agent,
            question,
            context,
            collector,
            self.name,
            transition_sink,
            self._overall_timeout_seconds,
        )
        evidence = collector.ordered_evidence()
        warnings = list(collector.warnings)
        if not evidence:
            warnings.append("No relevant authorized evidence was found.")
        return SpecialistOutcome(
            report=report or evidence_summary(evidence),
            evidence=evidence,
            warnings=warnings,
            grounded=bool(evidence),
        )


class AnalysisSubagent:
    """Quantitative answering where the model selects the aggregation, not the code."""

    name = "analysis_subagent"

    def __init__(
        self,
        toolbox: ScopedToolbox,
        model: Any,
        *,
        max_tool_calls: int = 8,
        max_model_calls: int = 6,
        overall_timeout_seconds: float = 45,
    ) -> None:
        self._overall_timeout_seconds = overall_timeout_seconds
        self._agent = create_agent(
            model=model,
            tools=build_gateway_tools(toolbox),
            system_prompt=ANALYSIS_SYSTEM_PROMPT,
            context_schema=SpecialistContext,
            middleware=budget_middleware(
                max_tool_calls=max_tool_calls, max_model_calls=max_model_calls
            ),
            name="analysis-specialist",
        )

    @traceable(
        name="delegate-analysis-subagent",
        run_type="chain",
        metadata={"agent": "analysis_subagent", "delegated": True},
    )
    async def run(
        self,
        question: str,
        context: Any,
        transition_sink: TransitionSink | None = None,
    ) -> SpecialistOutcome:
        collector = SpecialistCollector()
        report = await _run_agent(
            self._agent,
            question,
            context,
            collector,
            self.name,
            transition_sink,
            self._overall_timeout_seconds,
        )
        evidence = collector.ordered_evidence()
        warnings = list(collector.warnings)
        # The model narrates; the aggregation itself has to have been executed by the
        # tool. A persuasive figure with no recorded analysis behind it is reported as
        # an incomplete answer rather than accepted as one.
        aggregation = _analysis_result(collector)
        if aggregation is None:
            warnings.append("No controlled aggregation was completed for this question.")
        return SpecialistOutcome(
            report=report or evidence_summary(evidence),
            evidence=evidence,
            warnings=warnings,
            grounded=bool(evidence) and aggregation is not None,
        )


class EnterpriseToolSubagent:
    """System-of-record lookups where the model picks the tool and its arguments."""

    name = "enterprise_tool_subagent"

    def __init__(
        self,
        toolbox: ScopedToolbox,
        model: Any,
        *,
        max_tool_calls: int = 6,
        max_model_calls: int = 5,
        overall_timeout_seconds: float = 45,
    ) -> None:
        self._overall_timeout_seconds = overall_timeout_seconds
        self._agent = create_agent(
            model=model,
            tools=build_gateway_tools(toolbox),
            system_prompt=ENTERPRISE_SYSTEM_PROMPT,
            context_schema=SpecialistContext,
            middleware=budget_middleware(
                max_tool_calls=max_tool_calls, max_model_calls=max_model_calls
            ),
            name="enterprise-specialist",
        )

    @traceable(
        name="delegate-enterprise-tool-subagent",
        run_type="chain",
        metadata={"agent": "enterprise_tool_subagent", "delegated": True},
    )
    async def run(
        self,
        question: str,
        context: Any,
        transition_sink: TransitionSink | None = None,
    ) -> SpecialistOutcome:
        collector = SpecialistCollector()
        report = await _run_agent(
            self._agent,
            question,
            context,
            collector,
            self.name,
            transition_sink,
            self._overall_timeout_seconds,
        )
        records = _enterprise_records(collector)
        return SpecialistOutcome(
            report=(report or "The requested enterprise data source returned no matching record."),
            warnings=list(collector.warnings),
            grounded=bool(records),
        )


async def _run_agent(
    agent: Any,
    question: str,
    request_context: Any,
    collector: SpecialistCollector,
    agent_name: str,
    transition_sink: TransitionSink | None,
    overall_timeout_seconds: float,
) -> str:
    """Run one specialist loop and return its prose, with tool traffic in the collector."""
    specialist_context = SpecialistContext(
        request_context=request_context,
        collector=collector,
        agent_name=agent_name,
        transition_sink=transition_sink,
    )
    try:
        async with asyncio.timeout(overall_timeout_seconds):
            state = await agent.ainvoke(
                {"messages": [{"role": "user", "content": question}]},
                context=specialist_context,
            )
    except TimeoutError:
        collector.add_warning(
            f"The {agent_name.replace('_', ' ')} reached its execution time limit."
        )
        return final_text({"messages": []})
    return final_text(state)


def _analysis_result(collector: SpecialistCollector) -> AnalysisResult | None:
    """Rebuild the reported figures from the last analysis the tool actually ran."""
    results = collector.results_for("structured_analysis")
    if not results:
        return None
    try:
        return AnalysisResult.model_validate(results[-1])
    except Exception:
        return None


def _enterprise_records(collector: SpecialistCollector) -> dict[str, Any]:
    """Summarize the system-of-record calls that ran, so status stays evidence-based."""
    payload: dict[str, Any] = {}
    for invocation in collector.invocations:
        if invocation.status != "completed":
            continue
        records = _records(invocation.data)
        if records:
            payload[invocation.tool_name] = records
    return payload


def _records(data: Any) -> dict[str, Any]:
    """Keep only the fields of a tool payload that actually carried a record.

    Every enterprise search returns its envelope whether or not it matched, so an empty
    list has to read as "no record" rather than as data. Otherwise a miss would look
    like a successful lookup and the request would never be handed to the corpus.
    """

    if not isinstance(data, dict):
        return {}
    return {key: value for key, value in data.items() if value}


def _first_sentence(content: str, max_characters: int = 280) -> str:
    compact = " ".join(content.split())
    sentence = compact.split(". ", maxsplit=1)[0]
    return sentence[:max_characters].rstrip() + ("…" if len(sentence) > max_characters else "")


def evidence_summary(evidence: list[Evidence]) -> str:
    """Fallback prose when a specialist loop ends without writing an answer."""
    if not evidence:
        return "I could not find authorized evidence that answers this question."
    lines = ["I found the following relevant Commercial Bank evidence:"]
    for index, item in enumerate(evidence, start=1):
        lines.append(f"- {item.title}: {_first_sentence(unwrap_evidence(item.content))} [{index}]")
    return "\n".join(lines)
