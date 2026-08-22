"""Feedback contract placeholder until its persistence adapter is introduced."""

from fastapi import APIRouter, status

from orysys_assistant.domain.models import FeedbackAcknowledgement, FeedbackRequest

router = APIRouter(prefix="/v1/feedback", tags=["feedback"])


@router.post("", response_model=FeedbackAcknowledgement, status_code=status.HTTP_202_ACCEPTED)
async def submit_feedback(_: FeedbackRequest) -> FeedbackAcknowledgement:
    return FeedbackAcknowledgement(accepted=True)
