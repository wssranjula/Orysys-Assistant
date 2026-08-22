"""Human-in-the-loop graph for bounded, auditable administrative writes."""

import asyncio
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Literal, TypedDict
from uuid import UUID, uuid4

from langgraph.graph import END, START, StateGraph
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from orysys_assistant.domain.errors import InvalidRequestError
from orysys_assistant.security.models import TrustedRequestContext
from orysys_assistant.tools.admin import ModifyIncidentInput
from orysys_assistant.tools.gateway import ToolGateway


class ApprovalStatus(StrEnum):
    PENDING = "pending"
    REJECTED = "rejected"
    EXECUTED = "executed"
    FAILED = "failed"


class ApprovalRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    approval_id: UUID = Field(default_factory=uuid4)
    action: Literal["modify_incident"]
    parameters: dict[str, Any]
    reason: str = Field(min_length=5, max_length=500)
    requester_id: str
    approver_id: str | None = None
    approved: bool | None = None
    status: ApprovalStatus = ApprovalStatus.PENDING
    result: dict[str, Any] | None = None
    failure_type: str | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    decided_at: datetime | None = None


class ApprovalState(TypedDict):
    record: ApprovalRecord
    request_context: TrustedRequestContext


class ApprovalWorkflow:
    """Keep a risky action pending until an explicit human decision resumes the graph."""

    def __init__(self, gateway: ToolGateway) -> None:
        self._gateway = gateway
        self.graph = self._compile()

    def _compile(self) -> Any:
        builder = StateGraph(ApprovalState)
        builder.add_node("human_approval", self._human_approval)
        builder.add_node("execute_approved_action", self._execute)
        builder.add_edge(START, "human_approval")
        builder.add_conditional_edges(
            "human_approval",
            self._after_approval,
            {"execute": "execute_approved_action", "end": END},
        )
        builder.add_edge("execute_approved_action", END)
        return builder.compile()

    @staticmethod
    def _human_approval(state: ApprovalState) -> dict[str, ApprovalRecord]:
        record = state["record"]
        if record.approved is False and record.status is ApprovalStatus.PENDING:
            record = record.model_copy(update={"status": ApprovalStatus.REJECTED})
        return {"record": record}

    @staticmethod
    def _after_approval(state: ApprovalState) -> Literal["execute", "end"]:
        record = state["record"]
        return (
            "execute"
            if record.approved is True and record.status is ApprovalStatus.PENDING
            else "end"
        )

    async def _execute(self, state: ApprovalState) -> dict[str, ApprovalRecord]:
        record = state["record"]
        try:
            result = await self._gateway.execute(
                record.action, record.parameters, state["request_context"]
            )
            record = record.model_copy(
                update={"status": ApprovalStatus.EXECUTED, "result": result}
            )
        except Exception as exc:
            # Writes are never automatically retried: uncertain side effects stay contained.
            record = record.model_copy(
                update={"status": ApprovalStatus.FAILED, "failure_type": type(exc).__name__}
            )
        return {"record": record}

    async def run(
        self, record: ApprovalRecord, context: TrustedRequestContext
    ) -> ApprovalRecord:
        final = await self.graph.ainvoke({"record": record, "request_context": context})
        return ApprovalRecord.model_validate(final["record"])


class ApprovalService:
    """Atomic owner of approval state and graph resumption."""

    def __init__(self, gateway: ToolGateway) -> None:
        self._workflow = ApprovalWorkflow(gateway)
        self._records: dict[UUID, ApprovalRecord] = {}
        self._lock = asyncio.Lock()

    async def create(
        self,
        action: Literal["modify_incident"],
        parameters: dict[str, Any],
        reason: str,
        context: TrustedRequestContext,
    ) -> ApprovalRecord:
        try:
            parameters = ModifyIncidentInput.model_validate(parameters).model_dump(mode="json")
        except ValidationError as exc:
            raise InvalidRequestError("Administrative action parameters are invalid.") from exc
        record = ApprovalRecord(
            action=action,
            parameters=parameters,
            reason=reason,
            requester_id=context.identity.user_id,
        )
        record = await self._workflow.run(record, context)
        async with self._lock:
            self._records[record.approval_id] = record
        return record.model_copy(deep=True)

    async def get(self, approval_id: UUID) -> ApprovalRecord:
        async with self._lock:
            record = self._records.get(approval_id)
            if record is None:
                raise InvalidRequestError("The approval request was not found.")
            return record.model_copy(deep=True)

    async def decide(
        self, approval_id: UUID, approved: bool, context: TrustedRequestContext
    ) -> ApprovalRecord:
        async with self._lock:
            record = self._records.get(approval_id)
            if record is None:
                raise InvalidRequestError("The approval request was not found.")
            if record.status is not ApprovalStatus.PENDING or record.approved is not None:
                raise InvalidRequestError("The approval request has already been decided.")
            decided = record.model_copy(
                update={
                    "approved": approved,
                    "approver_id": context.identity.user_id,
                    "decided_at": datetime.now(UTC),
                }
            )
            final = await self._workflow.run(decided, context)
            self._records[approval_id] = final
            return final.model_copy(deep=True)
