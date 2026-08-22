"""Human approval endpoints for the bounded administrative write flow."""

from uuid import UUID

from fastapi import APIRouter, status

from orysys_assistant.agent.approval_graph import ApprovalRecord
from orysys_assistant.api.dependencies import (
    ApprovalServiceDependency,
    AuthorizationDependency,
    IdentityDependency,
    TrustedChatContextDependency,
)
from orysys_assistant.domain.models import (
    ApprovalCreateRequest,
    ApprovalDecisionRequest,
    ApprovalResponse,
)
from orysys_assistant.security.authorization import Capability

router = APIRouter(prefix="/v1/approvals", tags=["approvals"])


def _response(record: ApprovalRecord) -> ApprovalResponse:
    return ApprovalResponse.model_validate(
        record.model_dump(mode="python", include=set(ApprovalResponse.model_fields))
    )


@router.post("", response_model=ApprovalResponse, status_code=status.HTTP_202_ACCEPTED)
async def request_approval(
    payload: ApprovalCreateRequest,
    context: TrustedChatContextDependency,
    policy: AuthorizationDependency,
    service: ApprovalServiceDependency,
) -> ApprovalResponse:
    policy.require(context.identity, Capability.ADMIN_TOOLS)
    record = await service.create(payload.action, payload.parameters, payload.reason, context)
    return _response(record)


@router.get("/{approval_id}", response_model=ApprovalResponse)
async def get_approval(
    approval_id: UUID,
    identity: IdentityDependency,
    policy: AuthorizationDependency,
    service: ApprovalServiceDependency,
) -> ApprovalResponse:
    policy.require(identity, Capability.ADMIN_TOOLS)
    return _response(await service.get(approval_id))


@router.post("/{approval_id}/decision", response_model=ApprovalResponse)
async def decide_approval(
    approval_id: UUID,
    payload: ApprovalDecisionRequest,
    context: TrustedChatContextDependency,
    policy: AuthorizationDependency,
    service: ApprovalServiceDependency,
) -> ApprovalResponse:
    policy.require(context.identity, Capability.ADMIN_TOOLS)
    return _response(await service.decide(approval_id, payload.approved, context))
