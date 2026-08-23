"""Answer-quality feedback submitted by authenticated users."""

from fastapi import APIRouter, status

from orysys_assistant.api.dependencies import FeedbackRepositoryDependency, IdentityDependency
from orysys_assistant.domain.models import FeedbackAcknowledgement, FeedbackRequest

router = APIRouter(prefix="/v1/feedback", tags=["feedback"])


@router.post("", response_model=FeedbackAcknowledgement, status_code=status.HTTP_202_ACCEPTED)
async def submit_feedback(
    payload: FeedbackRequest,
    identity: IdentityDependency,
    repository: FeedbackRepositoryDependency,
) -> FeedbackAcknowledgement:
    await repository.record(identity.user_id, payload)
    return FeedbackAcknowledgement(accepted=True, persistence=repository.persistence_name)
