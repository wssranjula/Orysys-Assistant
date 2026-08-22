"""Compiled, bounded recursive research LangGraph."""

import asyncio
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import date
from typing import Any, Literal, TypedDict
from uuid import uuid4

from langgraph.graph import END, START, StateGraph
from langgraph.runtime import Runtime
from langsmith import traceable

from orysys_assistant.agent.models import (
    AgentTransition,
    Finding,
    ResearchExecution,
    ResearchPlan,
    ResearchResult,
    ResearchTask,
    ResearchTaskResult,
)
from orysys_assistant.agent.toolbox import ScopedToolbox
from orysys_assistant.config import Settings
from orysys_assistant.guardrails.content import unwrap_evidence
from orysys_assistant.retrieval.models import Evidence, SearchFilters
from orysys_assistant.security.models import AccessScope, TrustedRequestContext

TransitionSink = Callable[[AgentTransition], Awaitable[None]]


class ResearchState(TypedDict):
    request_id: str
    query: str
    access_scope: AccessScope
    filters: SearchFilters
    plan: ResearchPlan | None
    pending_tasks: list[ResearchTask]
    completed_tasks: list[ResearchTaskResult]
    evidence: dict[str, Evidence]
    findings: list[Finding]
    warnings: list[str]
    unresolved_questions: list[str]
    recursion_depth: int
    tool_calls_used: int
    partial: bool
    sufficient: bool


@dataclass(frozen=True, slots=True)
class ResearchGraphContext:
    request_context: TrustedRequestContext
    transition_sink: TransitionSink | None = None


@dataclass(frozen=True, slots=True)
class ResearchLimits:
    max_initial_tasks: int
    max_followup_tasks: int
    max_recursion_depth: int
    max_parallel_workers: int
    max_total_tool_calls: int
    max_chunks_per_worker: int
    worker_timeout_seconds: float
    overall_timeout_seconds: float

    @classmethod
    def from_settings(cls, settings: Settings) -> "ResearchLimits":
        return cls(
            max_initial_tasks=settings.research_max_initial_tasks,
            max_followup_tasks=settings.research_max_followup_tasks,
            max_recursion_depth=settings.research_max_recursion_depth,
            max_parallel_workers=settings.research_max_parallel_workers,
            max_total_tool_calls=settings.research_max_total_tool_calls,
            max_chunks_per_worker=settings.research_max_chunks_per_worker,
            worker_timeout_seconds=settings.research_worker_timeout_seconds,
            overall_timeout_seconds=settings.research_overall_timeout_seconds,
        )


class ResearchWorkflow:
    """Own one reusable compiled graph; all execution limits are code-enforced."""

    def __init__(
        self,
        toolbox: ScopedToolbox,
        limits: ResearchLimits,
        checkpointer: Any = None,
    ) -> None:
        self._toolbox = toolbox
        self._limits = limits
        self._checkpointer = checkpointer
        self.graph = self._compile()

    def _compile(self) -> Any:
        builder = StateGraph(ResearchState, context_schema=ResearchGraphContext)
        builder.add_node("normalize_scope", self._normalize_scope)
        builder.add_node("planner", self._planner)
        builder.add_node("workers", self._workers)
        builder.add_node("reducer", self._reducer)
        builder.add_node("coverage_check", self._coverage_check)
        builder.add_node("followup_planner", self._followup_planner)
        builder.add_node("finalize", self._finalize)
        builder.add_edge(START, "normalize_scope")
        builder.add_edge("normalize_scope", "planner")
        builder.add_edge("planner", "workers")
        builder.add_edge("workers", "reducer")
        builder.add_edge("reducer", "coverage_check")
        builder.add_conditional_edges(
            "coverage_check",
            self._next_after_coverage,
            {"followup": "followup_planner", "finalize": "finalize"},
        )
        builder.add_edge("followup_planner", "workers")
        builder.add_edge("finalize", END)
        return builder.compile(checkpointer=self._checkpointer)

    async def run(
        self,
        query: str,
        context: TrustedRequestContext,
        transition_sink: TransitionSink | None = None,
        thread_id: str | None = None,
    ) -> ResearchExecution:
        initial: ResearchState = {
            "request_id": str(uuid4()),
            "query": query,
            "access_scope": context.access_scope,
            "filters": SearchFilters(),
            "plan": None,
            "pending_tasks": [],
            "completed_tasks": [],
            "evidence": {},
            "findings": [],
            "warnings": [],
            "unresolved_questions": [],
            "recursion_depth": 0,
            "tool_calls_used": 0,
            "partial": False,
            "sufficient": False,
        }
        try:
            async with asyncio.timeout(self._limits.overall_timeout_seconds):
                final = await self.graph.ainvoke(
                    initial,
                    config={
                        "configurable": {
                            "thread_id": thread_id or initial["request_id"],
                        }
                    },
                    context=ResearchGraphContext(context, transition_sink),
                )
        except TimeoutError:
            return ResearchExecution(
                result=ResearchResult(
                    summary="Research stopped at the overall execution deadline.",
                    findings=[],
                    evidence_ids=[],
                    unresolved_questions=[query],
                    warnings=["The bounded research workflow reached its overall timeout."],
                    partial=True,
                ),
                evidence=[],
            )
        return ResearchExecution(
            result=ResearchResult(
                summary=self._summary(final),
                findings=final["findings"],
                evidence_ids=list(final["evidence"]),
                unresolved_questions=final["unresolved_questions"],
                warnings=final["warnings"],
                partial=final["partial"],
            ),
            evidence=list(final["evidence"].values()),
        )

    async def _normalize_scope(
        self, state: ResearchState, runtime: Runtime[ResearchGraphContext]
    ) -> dict[str, Any]:
        await self._node_event(runtime, "normalize_scope", "started")
        query = " ".join(state["query"].split())
        filters = self._query_filters(query)
        await self._node_event(runtime, "normalize_scope", "completed")
        return {"query": query, "filters": filters}

    async def _planner(
        self, state: ResearchState, runtime: Runtime[ResearchGraphContext]
    ) -> dict[str, Any]:
        await self._node_event(runtime, "planner", "started")
        tasks = self._initial_tasks(state["query"], state["filters"])
        plan = ResearchPlan(
            objective=state["query"],
            tasks=tasks[: self._limits.max_initial_tasks],
            aggregation_method="deduplicate evidence and aggregate recurring supported claims",
        )
        await self._node_event(runtime, "planner", "completed", {"task_count": len(plan.tasks)})
        return {"plan": plan, "pending_tasks": plan.tasks}

    async def _workers(
        self, state: ResearchState, runtime: Runtime[ResearchGraphContext]
    ) -> dict[str, Any]:
        available = max(0, self._limits.max_total_tool_calls - state["tool_calls_used"])
        tasks = state["pending_tasks"][:available]
        await self._node_event(runtime, "workers", "started", {"task_count": len(tasks)})
        semaphore = asyncio.Semaphore(self._limits.max_parallel_workers)
        results = await asyncio.gather(
            *(self._run_worker(task, runtime, semaphore) for task in tasks)
        )
        warnings = list(state["warnings"])
        if len(tasks) < len(state["pending_tasks"]):
            warnings.append("Research tool-call budget prevented some tasks from running.")
        await self._node_event(runtime, "workers", "completed", {"task_count": len(tasks)})
        return {
            "pending_tasks": [],
            "completed_tasks": [*state["completed_tasks"], *results],
            "tool_calls_used": state["tool_calls_used"] + len(tasks),
            "warnings": warnings,
        }

    @traceable(
        name="research-worker",
        run_type="chain",
        metadata={"agent": "research_subagent", "graph_node": "worker"},
    )
    async def _run_worker(
        self,
        task: ResearchTask,
        runtime: Runtime[ResearchGraphContext],
        semaphore: asyncio.Semaphore,
    ) -> ResearchTaskResult:
        async with semaphore:
            await self._node_event(runtime, f"worker:{task.task_id}", "started")
            parameters: dict[str, Any] = {
                "query": task.question,
                "top_k": self._limits.max_chunks_per_worker,
                **task.filters.model_dump(mode="json", exclude_none=True),
            }
            try:
                async with asyncio.timeout(self._limits.worker_timeout_seconds):
                    raw = await self._toolbox.execute(
                        "knowledge_search", parameters, runtime.context.request_context
                    )
                evidence = self._evidence_from_result(raw)
                findings = [
                    Finding(claim=self._claim(item), evidence_ids=[item.evidence_id])
                    for item in evidence
                ]
                result = ResearchTaskResult(
                    task_id=task.task_id,
                    status="completed",
                    findings=findings,
                    evidence=evidence,
                    warning=None if evidence else "No authorized evidence matched this task.",
                )
            except TimeoutError:
                result = ResearchTaskResult(
                    task_id=task.task_id,
                    status="failed",
                    warning="Retrieval timed out.",
                )
            except Exception as exc:
                result = ResearchTaskResult(
                    task_id=task.task_id,
                    status="failed",
                    warning=f"Worker failed safely: {type(exc).__name__}.",
                )
            await self._node_event(
                runtime,
                f"worker:{task.task_id}",
                "completed",
                {"status": result.status, "evidence_count": len(result.evidence)},
            )
            return result

    async def _reducer(
        self, state: ResearchState, runtime: Runtime[ResearchGraphContext]
    ) -> dict[str, Any]:
        await self._node_event(runtime, "reducer", "started")
        evidence = dict(state["evidence"])
        claims: dict[str, Finding] = {}
        warnings = list(state["warnings"])
        for result in state["completed_tasks"]:
            if result.warning and result.warning not in warnings:
                warnings.append(result.warning)
            for item in result.evidence:
                evidence[item.evidence_id] = item
            for finding in result.findings:
                key = self._normalize_claim(finding.claim)
                current = claims.get(key)
                if current is None:
                    claims[key] = finding
                else:
                    claims[key] = Finding(
                        claim=current.claim,
                        evidence_ids=list(
                            dict.fromkeys([*current.evidence_ids, *finding.evidence_ids])
                        ),
                        occurrence_count=(current.occurrence_count or 1) + 1,
                    )
        findings = list(claims.values())
        await self._node_event(
            runtime,
            "reducer",
            "completed",
            {"evidence_count": len(evidence), "finding_count": len(findings)},
        )
        return {"evidence": evidence, "findings": findings, "warnings": warnings}

    async def _coverage_check(
        self, state: ResearchState, runtime: Runtime[ResearchGraphContext]
    ) -> dict[str, Any]:
        await self._node_event(runtime, "coverage_check", "started")
        completed = [item for item in state["completed_tasks"] if item.status == "completed"]
        sufficient = len(state["evidence"]) >= 3 and len(completed) >= 2
        budget_exhausted = state["tool_calls_used"] >= self._limits.max_total_tool_calls
        depth_exhausted = state["recursion_depth"] >= self._limits.max_recursion_depth
        partial = not sufficient and (budget_exhausted or depth_exhausted)
        unresolved = [] if sufficient else self._unresolved(state)
        warnings = list(state["warnings"])
        if partial:
            warnings.append("Research limits were reached before evidence coverage was sufficient.")
        await self._node_event(
            runtime,
            "coverage_check",
            "completed",
            {"sufficient": sufficient, "partial": partial},
        )
        return {
            "sufficient": sufficient,
            "partial": partial,
            "unresolved_questions": unresolved,
            "warnings": list(dict.fromkeys(warnings)),
        }

    def _next_after_coverage(self, state: ResearchState) -> Literal["followup", "finalize"]:
        if state["sufficient"] or state["partial"]:
            return "finalize"
        if self._limits.max_followup_tasks == 0:
            return "finalize"
        return "followup"

    async def _followup_planner(
        self, state: ResearchState, runtime: Runtime[ResearchGraphContext]
    ) -> dict[str, Any]:
        await self._node_event(runtime, "followup_planner", "started")
        depth = state["recursion_depth"] + 1
        tasks = [
            ResearchTask(
                task_id=f"followup-{depth}-{index}",
                question=f"{state['query']} Cross-check {focus} evidence and missing context.",
                filters=SearchFilters(document_type=document_type),
                expected_output=f"Corroborating {focus} evidence",
            )
            for index, (focus, document_type) in enumerate(
                (("runbook", "runbook"), ("architecture", "architecture")), start=1
            )
        ][: self._limits.max_followup_tasks]
        await self._node_event(runtime, "followup_planner", "completed", {"task_count": len(tasks)})
        return {"pending_tasks": tasks, "recursion_depth": depth}

    async def _finalize(
        self, state: ResearchState, runtime: Runtime[ResearchGraphContext]
    ) -> dict[str, Any]:
        await self._node_event(runtime, "finalize", "started")
        partial = state["partial"] or not state["sufficient"]
        warnings = list(state["warnings"])
        if partial and not any("limits were reached" in item for item in warnings):
            warnings.append("Research returned the evidence available within configured limits.")
        await self._node_event(runtime, "finalize", "completed", {"partial": partial})
        return {"partial": partial, "warnings": list(dict.fromkeys(warnings))}

    def _initial_tasks(self, query: str, filters: SearchFilters) -> list[ResearchTask]:
        if self._is_yearly_incident_query(query):
            year = date.today().year - 1
            ranges = ((1, 3), (4, 6), (7, 9), (10, 12))
            return [
                ResearchTask(
                    task_id=f"initial-{index}",
                    question=f"{query} Focus on months {start:02d}-{end:02d} of {year}.",
                    filters=SearchFilters(
                        document_type="incident",
                        created_after=date(year, start, 1),
                        created_before=date(year, end, self._month_end(end)),
                    ),
                    expected_output="Payment incident, impact, and supported root cause",
                )
                for index, (start, end) in enumerate(ranges, start=1)
            ]
        task_types = ("incident", "meeting_note", "runbook", "architecture")
        return [
            ResearchTask(
                task_id=f"initial-{index}",
                question=f"{query} Focus on {document_type.replace('_', ' ')} evidence.",
                filters=SearchFilters(
                    department=filters.department,
                    document_type=document_type,
                    created_after=filters.created_after,
                    created_before=filters.created_before,
                ),
                expected_output="Grounded findings with evidence identifiers",
            )
            for index, document_type in enumerate(task_types, start=1)
        ]

    @staticmethod
    def _query_filters(query: str) -> SearchFilters:
        normalized = query.lower()
        return SearchFilters(document_type="incident" if "incident" in normalized else None)

    @staticmethod
    def _is_yearly_incident_query(query: str) -> bool:
        normalized = query.lower()
        return any(word in normalized for word in ("outage", "incident")) and any(
            phrase in normalized
            for phrase in ("last year", "previous year", "over the year", "annual")
        )

    @staticmethod
    def _month_end(month: int) -> int:
        return 31 if month in {3, 12} else 30

    @staticmethod
    def _evidence_from_result(result: Any) -> list[Evidence]:
        if not isinstance(result, dict) or not isinstance(result.get("evidence"), list):
            raise ValueError("Knowledge search returned an invalid result contract.")
        return [Evidence.model_validate(item) for item in result["evidence"]]

    @staticmethod
    def _claim(evidence: Evidence) -> str:
        compact = " ".join(unwrap_evidence(evidence.content).split())
        sentences = re.split(r"(?<=[.!?])\s+", compact)
        selected = next(
            (
                sentence
                for sentence in sentences
                if re.search(r"\b(root cause|caused|cause|contributed)\b", sentence, re.I)
            ),
            sentences[0],
        )
        return f"{evidence.title}: {selected[:320]}"

    @staticmethod
    def _normalize_claim(claim: str) -> str:
        return re.sub(r"[^a-z0-9]+", " ", claim.lower()).strip()

    @staticmethod
    def _unresolved(state: ResearchState) -> list[str]:
        failed = {
            item.task_id
            for item in state["completed_tasks"]
            if item.status != "completed" or not item.evidence
        }
        return [f"Evidence coverage remains incomplete for task {task_id}." for task_id in failed]

    @staticmethod
    def _summary(state: ResearchState) -> str:
        status = "Partial research" if state["partial"] else "Research complete"
        return (
            f"{status}: reviewed {len(state['evidence'])} unique authorized evidence records "
            f"across {state['tool_calls_used']} bounded retrieval tasks."
        )

    @staticmethod
    async def _node_event(
        runtime: Runtime[ResearchGraphContext],
        node: str,
        status: str,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        sink = runtime.context.transition_sink
        if sink is not None:
            await sink(
                AgentTransition(
                    event_type=f"research_node_{status}",
                    agent="research_subagent",
                    node=node,
                    status=status,
                    message=f"Research node {node.replace('_', ' ')} {status}.",
                    metadata=metadata or {},
                )
            )
