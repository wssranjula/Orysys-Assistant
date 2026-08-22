"""Human-in-the-loop graph for bounded, auditable administrative writes."""

import asyncio
import json
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Literal, TypedDict
from uuid import UUID, uuid4

import asyncpg  # type: ignore[import-untyped]
from langgraph.graph import END, START, StateGraph
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from orysys_assistant.domain.errors import AuthorizationError, InvalidRequestError
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
            record = record.model_copy(update={"status": ApprovalStatus.EXECUTED, "result": result})
        except Exception as exc:
            # Writes are never automatically retried: uncertain side effects stay contained.
            record = record.model_copy(
                update={"status": ApprovalStatus.FAILED, "failure_type": type(exc).__name__}
            )
        return {"record": record}

    async def run(self, record: ApprovalRecord, context: TrustedRequestContext) -> ApprovalRecord:
        final = await self.graph.ainvoke({"record": record, "request_context": context})
        return ApprovalRecord.model_validate(final["record"])


class ApprovalService:
    """Atomic owner of approval state and graph resumption."""

    def __init__(self, gateway: ToolGateway, database_url: str | None = None) -> None:
        self._workflow = ApprovalWorkflow(gateway)
        self._records: dict[UUID, ApprovalRecord] = {}
        self._lock = asyncio.Lock()
        self._database_url = (
            database_url.replace("postgresql+asyncpg://", "postgresql://")
            if database_url is not None
            else None
        )
        self._pool: asyncpg.Pool | None = None

    async def start(self) -> None:
        if self._database_url is None or self._pool is not None:
            return
        self._pool = await asyncpg.create_pool(self._database_url, min_size=1, max_size=5)
        async with self._pool.acquire() as connection:
            await connection.execute(
                """
                CREATE TABLE IF NOT EXISTS approval_records (
                    approval_id UUID PRIMARY KEY,
                    approval_record JSONB NOT NULL,
                    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                )
                """
            )

    async def close(self) -> None:
        if self._pool is not None:
            await self._pool.close()
            self._pool = None

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
            await self._save(record)
        return record.model_copy(deep=True)

    async def get(self, approval_id: UUID) -> ApprovalRecord:
        if self._pool is not None:
            async with self._pool.acquire() as connection:
                row = await connection.fetchrow(
                    "SELECT approval_record FROM approval_records WHERE approval_id = $1",
                    approval_id,
                )
            if row is None:
                raise InvalidRequestError("The approval request was not found.")
            return self._from_row(row)
        async with self._lock:
            record = self._records.get(approval_id)
            if record is None:
                raise InvalidRequestError("The approval request was not found.")
            return record.model_copy(deep=True)

    async def list(self, status: ApprovalStatus | None = None) -> list[ApprovalRecord]:
        if self._pool is not None:
            async with self._pool.acquire() as connection:
                rows = await connection.fetch(
                    "SELECT approval_record FROM approval_records ORDER BY updated_at DESC"
                )
            records = [self._from_row(row) for row in rows]
            return [record for record in records if status is None or record.status is status]
        async with self._lock:
            records = [
                record.model_copy(deep=True)
                for record in self._records.values()
                if status is None or record.status is status
            ]
        return sorted(records, key=lambda item: item.created_at, reverse=True)

    async def decide(
        self, approval_id: UUID, approved: bool, context: TrustedRequestContext
    ) -> ApprovalRecord:
        if self._pool is not None:
            async with self._pool.acquire() as connection, connection.transaction():
                row = await connection.fetchrow(
                    "SELECT approval_record FROM approval_records WHERE "
                    "approval_id = $1 FOR UPDATE",
                    approval_id,
                )
                if row is None:
                    raise InvalidRequestError("The approval request was not found.")
                record = self._from_row(row)
                final = await self._decide_record(record, approved, context)
                await self._save(final, connection)
                return final.model_copy(deep=True)
        async with self._lock:
            stored_record = self._records.get(approval_id)
            if stored_record is None:
                raise InvalidRequestError("The approval request was not found.")
            final = await self._decide_record(stored_record, approved, context)
            self._records[approval_id] = final
            return final.model_copy(deep=True)

    async def _decide_record(
        self, record: ApprovalRecord, approved: bool, context: TrustedRequestContext
    ) -> ApprovalRecord:
        if record.status is not ApprovalStatus.PENDING or record.approved is not None:
            raise InvalidRequestError("The approval request has already been decided.")
        if record.requester_id == context.identity.user_id:
            raise AuthorizationError("A different administrator must approve this request.")
        decided = record.model_copy(
            update={
                "approved": approved,
                "approver_id": context.identity.user_id,
                "decided_at": datetime.now(UTC),
            }
        )
        return await self._workflow.run(decided, context)

    async def _save(
        self, record: ApprovalRecord, connection: asyncpg.Connection | None = None
    ) -> None:
        if self._pool is None and connection is None:
            return
        query = """
            INSERT INTO approval_records (approval_id, approval_record)
            VALUES ($1, $2::jsonb)
            ON CONFLICT (approval_id) DO UPDATE
            SET approval_record = EXCLUDED.approval_record, updated_at = NOW()
        """
        payload = record.model_dump_json()
        if connection is not None:
            await connection.execute(query, record.approval_id, payload)
            return
        if self._pool is None:
            return
        async with self._pool.acquire() as acquired:
            await acquired.execute(query, record.approval_id, payload)

    @staticmethod
    def _from_row(row: Any) -> ApprovalRecord:
        value = row["approval_record"]
        if isinstance(value, str):
            value = json.loads(value)
        return ApprovalRecord.model_validate(value)
