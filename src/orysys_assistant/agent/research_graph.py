"""Compiled, bounded recursive research LangGraph."""

import asyncio
import operator
import re
from collections.abc import Awaitable, Callable, Sequence
from contextlib import suppress
from dataclasses import dataclass
from datetime import date
from typing import Annotated, Any, Literal, TypedDict
from uuid import uuid4

from langgraph.config import get_stream_writer
from langgraph.graph import END, START, StateGraph
from langgraph.runtime import Runtime
from langgraph.types import Send
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
from orysys_assistant.agent.research_planner import ResearchPlanner
from orysys_assistant.agent.toolbox import ScopedToolbox
from orysys_assistant.config import Settings
from orysys_assistant.guardrails.content import unwrap_evidence
from orysys_assistant.retrieval.models import Evidence, SearchFilters
from orysys_assistant.security.models import AccessScope, TrustedRequestContext

TransitionSink = Callable[[AgentTransition], Awaitable[None]]

_TASK_FIELD_PATTERN = re.compile(
    r"(?:^|\|)\s*(SEARCH|SOURCE|VERIFY)\s*:\s*(.*?)(?=\s*\|\s*(?:SEARCH|SOURCE|VERIFY)\s*:|$)",
    re.IGNORECASE,
)
_SUPPORTED_DOCUMENT_TYPES = {
    "incident",
    "meeting_note",
    "runbook",
    "architecture",
    "policy",
    "product_specification",
}
_IDENTIFIER_PATTERN = re.compile(r"\b(?:PAY|OR|CG|INC|SVC)-\d+\b", re.IGNORECASE)
_YEAR_PATTERN = re.compile(r"\b(20\d{2})\b")


class ResearchState(TypedDict):
    request_id: str
    query: str
    access_scope: AccessScope
    filters: SearchFilters
    plan: ResearchPlan | None
    pending_tasks: list[ResearchTask]
    task: ResearchTask | None
    worker_results: Annotated[list[ResearchTaskResult], operator.add]
    completed_tasks: list[ResearchTaskResult]
    evidence: dict[str, Evidence]
    findings: list[Finding]
    warnings: list[str]
    unresolved_questions: list[str]
    recursion_depth: int
    tool_calls_used: int
    partial: bool
    sufficient: bool
    failure_circuit_open: bool


@dataclass(frozen=True, slots=True)
class ResearchGraphContext:
    request_context: TrustedRequestContext
    transition_sink: TransitionSink | None = None
    worker_semaphore: asyncio.Semaphore | None = None


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
        planner: ResearchPlanner | None = None,
    ) -> None:
        self._toolbox = toolbox
        self._limits = limits
        self._checkpointer = checkpointer
        self._planner_agent = planner
        self.graph = self._compile()

    def _compile(self) -> Any:
        builder = StateGraph(ResearchState, context_schema=ResearchGraphContext)
        builder.add_node("normalize_scope", self._normalize_scope)
        builder.add_node("planner", self._planner)
        builder.add_node("workers", self._workers)
        builder.add_node("worker", self._worker)
        builder.add_node("reducer", self._reducer)
        builder.add_node("coverage_check", self._coverage_check)
        builder.add_node("followup_planner", self._followup_planner)
        builder.add_node("finalize", self._finalize)
        builder.add_edge(START, "normalize_scope")
        builder.add_edge("normalize_scope", "planner")
        builder.add_edge("planner", "workers")
        builder.add_conditional_edges("workers", self._dispatch_workers)
        builder.add_edge("worker", "reducer")
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
            "task": None,
            "worker_results": [],
            "completed_tasks": [],
            "evidence": {},
            "findings": [],
            "warnings": [],
            "unresolved_questions": [],
            "recursion_depth": 0,
            "tool_calls_used": 0,
            "partial": False,
            "sufficient": False,
            "failure_circuit_open": False,
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
                    context=ResearchGraphContext(
                        context,
                        transition_sink,
                        asyncio.Semaphore(self._limits.max_parallel_workers),
                    ),
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
        await self._node_event(
            runtime,
            "normalize_scope",
            "completed",
            {"retrieval_filters": filters.model_dump(mode="json", exclude_none=True)},
        )
        return {"query": query, "filters": filters}

    async def _planner(
        self, state: ResearchState, runtime: Runtime[ResearchGraphContext]
    ) -> dict[str, Any]:
        await self._node_event(runtime, "planner", "started")
        warnings = list(state["warnings"])
        if self._planner_agent is None:
            tasks = self._initial_tasks(state["query"], state["filters"])
        else:
            try:
                tasks = await self._planned_tasks(
                    state["query"],
                    SearchFilters(),
                    max_tasks=self._limits.max_initial_tasks,
                )
            except Exception as exc:
                tasks = self._initial_tasks(state["query"], state["filters"])
                warnings.append(
                    f"Model-generated planning was unavailable; used bounded fallback: "
                    f"{type(exc).__name__}."
                )
        plan = ResearchPlan(
            objective=state["query"],
            tasks=tasks[: self._limits.max_initial_tasks],
            aggregation_method="deduplicate evidence and aggregate recurring supported claims",
        )
        await self._node_event(
            runtime,
            "planner",
            "completed",
            {
                "task_count": len(plan.tasks),
                "plan_summary": self._plan_summary(plan.tasks),
                "todos": [
                    {"id": task.task_id, "content": task.question, "status": "pending"}
                    for task in plan.tasks
                ],
            },
        )
        return {"plan": plan, "pending_tasks": plan.tasks, "warnings": warnings}

    async def _workers(
        self, state: ResearchState, runtime: Runtime[ResearchGraphContext]
    ) -> dict[str, Any]:
        available = max(0, self._limits.max_total_tool_calls - state["tool_calls_used"])
        tasks = state["pending_tasks"][:available]
        await self._node_event(runtime, "workers", "started", {"task_count": len(tasks)})
        warnings = list(state["warnings"])
        if len(tasks) < len(state["pending_tasks"]):
            warnings.append("Research tool-call budget prevented some tasks from running.")
        return {
            "pending_tasks": tasks,
            "warnings": warnings,
        }

    @staticmethod
    def _dispatch_workers(state: ResearchState) -> list[Send] | Literal["reducer"]:
        if not state["pending_tasks"]:
            return "reducer"
        return [Send("worker", {"task": task}) for task in state["pending_tasks"]]

    async def _worker(
        self, state: ResearchState, runtime: Runtime[ResearchGraphContext]
    ) -> dict[str, list[ResearchTaskResult]]:
        task = state.get("task")
        if task is None:
            raise RuntimeError("A research worker was dispatched without a task.")
        semaphore = runtime.context.worker_semaphore
        if semaphore is None:
            return {"worker_results": [await self._run_worker(task, runtime)]}
        async with semaphore:
            return {"worker_results": [await self._run_worker(task, runtime)]}

    @traceable(
        name="research-worker",
        run_type="chain",
        metadata={"agent": "research_subagent", "graph_node": "worker"},
    )
    async def _run_worker(
        self,
        task: ResearchTask,
        runtime: Runtime[ResearchGraphContext],
    ) -> ResearchTaskResult:
        await self._node_event(
            runtime,
            f"worker:{task.task_id}",
            "started",
            {
                "todo_id": task.task_id,
                "todo_content": task.question,
                "todo_status": "in_progress",
            },
            message=f"Researching: {task.question[:240]}",
        )
        worker_metadata: dict[str, Any] = {}
        parameters: dict[str, Any] = {
            "query": task.search_query or task.question,
            "top_k": self._limits.max_chunks_per_worker,
            **task.filters.model_dump(mode="json", exclude_none=True),
        }
        try:
            async with asyncio.timeout(self._limits.worker_timeout_seconds):
                raw = await self._toolbox.execute(
                    "knowledge_search", parameters, runtime.context.request_context
                )
            evidence = self._evidence_from_result(raw)
            coverage_satisfied = self._task_has_coverage(task, evidence)
            worker_metadata = {
                "candidate_count": int(raw.get("candidate_count", len(evidence))),
                "selected_evidence_count": len(evidence),
                "retrieval_mode": str(raw.get("retrieval_mode", "hybrid")),
                "retrieval_filters": task.filters.model_dump(mode="json", exclude_none=True),
                "tool_name": "knowledge_search",
            }
            findings = [
                Finding(claim=self._claim(item), evidence_ids=[item.evidence_id])
                for item in evidence
            ]
            result = ResearchTaskResult(
                task_id=task.task_id,
                status="completed" if coverage_satisfied else "incomplete",
                findings=findings,
                evidence=evidence,
                warning=(None if coverage_satisfied else self._coverage_warning(task, evidence)),
                coverage_satisfied=coverage_satisfied,
            )
        except TimeoutError:
            result = ResearchTaskResult(
                task_id=task.task_id,
                status="failed",
                warning="Retrieval timed out.",
                failure_kind="dependency_timeout",
                retryable=True,
            )
        except Exception as exc:
            result = ResearchTaskResult(
                task_id=task.task_id,
                status="failed",
                warning=f"Worker failed safely: {type(exc).__name__}.",
                failure_kind=type(exc).__name__,
            )
        await self._node_event(
            runtime,
            f"worker:{task.task_id}",
            "completed",
            {
                "status": result.status,
                "evidence_count": len(result.evidence),
                "todo_id": task.task_id,
                "todo_content": task.question,
                "todo_status": ("completed" if result.coverage_satisfied else "pending"),
                **worker_metadata,
            },
            message=f"Research task {result.status}: {task.question[:220]}",
        )
        return result

    async def _reducer(
        self, state: ResearchState, runtime: Runtime[ResearchGraphContext]
    ) -> dict[str, Any]:
        await self._node_event(runtime, "reducer", "started")
        completed_tasks = state["worker_results"]
        evidence = dict(state["evidence"])
        claims: dict[str, Finding] = {}
        warnings = list(state["warnings"])
        for result in completed_tasks:
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
        pending_ids = {task.task_id for task in state["pending_tasks"]}
        cycle_results = [item for item in completed_tasks if item.task_id in pending_ids]
        failed_results = [item for item in cycle_results if item.status == "failed"]
        failure_circuit_open = bool(cycle_results) and len(failed_results) == len(cycle_results)
        if failure_circuit_open:
            warnings.append(
                "All workers in this collaboration round failed; follow-up fan-out was "
                "stopped to contain a likely shared dependency failure."
            )
        await self._node_event(
            runtime,
            "reducer",
            "completed",
            {
                "evidence_count": len(evidence),
                "finding_count": len(findings),
                "failed_worker_count": len(failed_results),
                "failure_circuit_open": failure_circuit_open,
            },
        )
        await self._node_event(
            runtime, "workers", "completed", {"task_count": len(state["pending_tasks"])}
        )
        return {
            "pending_tasks": [],
            "completed_tasks": completed_tasks,
            "tool_calls_used": len(completed_tasks),
            "evidence": evidence,
            "findings": findings,
            "warnings": warnings,
            "failure_circuit_open": failure_circuit_open,
        }

    async def _coverage_check(
        self, state: ResearchState, runtime: Runtime[ResearchGraphContext]
    ) -> dict[str, Any]:
        await self._node_event(runtime, "coverage_check", "started")
        plan_tasks = state["plan"].tasks if state["plan"] else []
        all_evidence = list(state["evidence"].values())
        covered_task_ids = {
            task.task_id for task in plan_tasks if self._task_has_coverage(task, all_evidence)
        }
        sufficient = (
            bool(plan_tasks)
            and len(state["evidence"]) >= 3
            and len(covered_task_ids) == len(plan_tasks)
        )
        budget_exhausted = state["tool_calls_used"] >= self._limits.max_total_tool_calls
        depth_exhausted = state["recursion_depth"] >= self._limits.max_recursion_depth
        partial = not sufficient and (
            budget_exhausted or depth_exhausted or state["failure_circuit_open"]
        )
        unresolved = [] if sufficient else self._unresolved(state, covered_task_ids)
        warnings = list(state["warnings"])
        if not sufficient and (budget_exhausted or depth_exhausted):
            warnings.append("Research limits were reached before evidence coverage was sufficient.")
        await self._node_event(
            runtime,
            "coverage_check",
            "completed",
            {
                "sufficient": sufficient,
                "partial": partial,
                "covered_task_count": len(covered_task_ids),
                "planned_task_count": len(plan_tasks),
                "failure_circuit_open": state["failure_circuit_open"],
            },
        )
        return {
            "sufficient": sufficient,
            "partial": partial,
            "unresolved_questions": unresolved,
            "warnings": list(dict.fromkeys(warnings)),
        }

    def _next_after_coverage(self, state: ResearchState) -> Literal["followup", "finalize"]:
        if state["sufficient"] or state["partial"] or state["failure_circuit_open"]:
            return "finalize"
        if self._limits.max_followup_tasks == 0:
            return "finalize"
        return "followup"

    async def _followup_planner(
        self, state: ResearchState, runtime: Runtime[ResearchGraphContext]
    ) -> dict[str, Any]:
        await self._node_event(runtime, "followup_planner", "started")
        depth = state["recursion_depth"] + 1
        warnings = list(state["warnings"])
        tasks = self._gap_followup_tasks(state, depth)
        await self._node_event(
            runtime,
            "followup_planner",
            "completed",
            {
                "task_count": len(tasks),
                "plan_summary": self._plan_summary(tasks),
                "todos": [
                    {"id": task.task_id, "content": task.question, "status": "pending"}
                    for task in tasks
                ],
            },
        )
        return {
            "pending_tasks": tasks,
            "recursion_depth": depth,
            "warnings": warnings,
        }

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
                    search_query=f"payment incidents {year} root cause impact",
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
                search_query=self._fallback_search_query(query, document_type),
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

    async def _planned_tasks(
        self,
        query: str,
        filters: SearchFilters,
        *,
        max_tasks: int,
        prefix: str = "initial",
        completed_tasks: Sequence[str] = (),
        evidence_titles: Sequence[str] = (),
    ) -> list[ResearchTask]:
        if self._planner_agent is None:
            raise RuntimeError("No model-generated research planner is configured.")
        questions = await self._planner_agent.plan(
            query,
            max_tasks=max_tasks,
            completed_tasks=completed_tasks,
            evidence_titles=evidence_titles,
        )
        return [
            self._compile_planned_task(
                question,
                filters,
                task_id=f"{prefix}-{index}",
            )
            for index, question in enumerate(questions[:max_tasks], start=1)
        ]

    def _fallback_followup_tasks(self, query: str, depth: int) -> list[ResearchTask]:
        return [
            ResearchTask(
                task_id=f"followup-{depth}-{index}",
                question=f"{query} Cross-check {focus} evidence and missing context.",
                search_query=self._fallback_search_query(query, document_type),
                filters=SearchFilters(document_type=document_type),
                expected_output=f"Corroborating {focus} evidence",
            )
            for index, (focus, document_type) in enumerate(
                (("runbook", "runbook"), ("architecture", "architecture")), start=1
            )
        ][: self._limits.max_followup_tasks]

    def _gap_followup_tasks(self, state: ResearchState, depth: int) -> list[ResearchTask]:
        """Retry only uncovered plan requirements with their structured retrieval scope."""
        if state["plan"] is None:
            return self._fallback_followup_tasks(state["query"], depth)
        evidence = list(state["evidence"].values())
        missing = [
            task for task in state["plan"].tasks if not self._task_has_coverage(task, evidence)
        ]
        return [
            task.model_copy(
                update={
                    "task_id": f"followup-{depth}-{index}",
                    "question": f"Gap-closing follow-up: {task.question}",
                }
            )
            for index, task in enumerate(missing[: self._limits.max_followup_tasks], start=1)
        ]

    @classmethod
    def _compile_planned_task(
        cls, question: str, base_filters: SearchFilters, *, task_id: str
    ) -> ResearchTask:
        fields = {
            match.group(1).upper(): " ".join(match.group(2).split())
            for match in _TASK_FIELD_PATTERN.finditer(question)
        }
        source = fields.get("SOURCE", "").lower().replace(" ", "_")
        document_type = source if source in _SUPPORTED_DOCUMENT_TYPES else None
        search_query = fields.get("SEARCH") or cls._fallback_search_query(question, document_type)
        search_query = search_query[:300]
        identifiers = list(
            dict.fromkeys(
                match.group(0).upper() for match in _IDENTIFIER_PATTERN.finditer(search_query)
            )
        )
        years = [int(value) for value in _YEAR_PATTERN.findall(f"{search_query} {question}")]
        created_after = base_filters.created_after
        created_before = base_filters.created_before
        if years and not created_after and not created_before:
            year = min(years)
            created_after = date(year, 1, 1)
            created_before = date(max(years), 12, 31)
        task_filters = SearchFilters(
            department=base_filters.department,
            document_type=document_type or base_filters.document_type,
            created_after=created_after,
            created_before=created_before,
        )
        minimum_evidence = 1
        normalized = f"{search_query} {fields.get('VERIFY', '')}".lower()
        if document_type == "incident" and any(
            term in normalized for term in ("all ", "failures", "incidents", "timeline")
        ):
            minimum_evidence = 2
        return ResearchTask(
            task_id=task_id,
            question=question,
            search_query=search_query,
            filters=task_filters,
            expected_output=fields.get("VERIFY", "Evidence that resolves this research todo"),
            required_identifiers=identifiers,
            minimum_evidence=minimum_evidence,
        )

    @staticmethod
    def _fallback_search_query(query: str, document_type: str | None) -> str:
        compact = re.sub(
            r"\b(deliverable|for each|collect|capture|include|produce)\b.*$",
            "",
            " ".join(query.split()),
            flags=re.IGNORECASE,
        ).strip(" |")
        if document_type is None:
            return compact[:300]
        return f"{compact[:220]} {document_type.replace('_', ' ')}".strip()

    @classmethod
    def _task_has_coverage(cls, task: ResearchTask, evidence: Sequence[Evidence]) -> bool:
        matching = [item for item in evidence if cls._evidence_matches_task(task, item)]
        if len(matching) < task.minimum_evidence:
            return False
        if not task.required_identifiers:
            return True
        combined = " ".join(
            f"{item.title} {unwrap_evidence(item.content)}" for item in matching
        ).upper()
        return all(identifier in combined for identifier in task.required_identifiers)

    @staticmethod
    def _evidence_matches_task(task: ResearchTask, evidence: Evidence) -> bool:
        metadata = evidence.metadata
        if (
            task.filters.document_type
            and metadata.get("document_type") != task.filters.document_type
        ):
            return False
        raw_date = metadata.get("created_date")
        try:
            evidence_date = date.fromisoformat(str(raw_date)) if raw_date else None
        except ValueError:
            evidence_date = None
        if task.filters.created_after and (
            evidence_date is None or evidence_date < task.filters.created_after
        ):
            return False
        return not (
            task.filters.created_before
            and (evidence_date is None or evidence_date > task.filters.created_before)
        )

    @staticmethod
    def _coverage_warning(task: ResearchTask, evidence: Sequence[Evidence]) -> str:
        return (
            f"Evidence did not satisfy task {task.task_id}: expected at least "
            f"{task.minimum_evidence} matching record(s)"
            + (
                f" covering {', '.join(task.required_identifiers)}."
                if task.required_identifiers
                else "."
            )
        )

    @staticmethod
    def _plan_summary(tasks: Sequence[ResearchTask]) -> str:
        return "Research todos: " + "; ".join(
            f"{index}. {task.question}" for index, task in enumerate(tasks, start=1)
        )

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
    def _unresolved(state: ResearchState, covered_task_ids: set[str] | None = None) -> list[str]:
        covered = covered_task_ids or set()
        plan_tasks = state["plan"].tasks if state["plan"] else []
        return [
            f"Evidence coverage remains incomplete for task {task.task_id}: {task.expected_output}."
            for task in plan_tasks
            if task.task_id not in covered
        ]

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
        message: str | None = None,
    ) -> None:
        transition = AgentTransition(
            event_type=f"research_node_{status}",
            agent="research_subagent",
            node=node,
            status=status,
            message=message or f"Research node {node.replace('_', ' ')} {status}.",
            metadata=metadata or {},
        )
        with suppress(RuntimeError):
            get_stream_writer()(transition.model_dump(mode="json"))
        sink = runtime.context.transition_sink
        if sink is not None:
            await sink(transition)
