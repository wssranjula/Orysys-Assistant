from uuid import uuid4

import pytest
from pydantic import ValidationError

from orysys_assistant.domain.models import (
    ApiError,
    ChatRequest,
    Citation,
    ErrorEnvelope,
    FinalResponse,
    ResponseStatus,
)


def test_chat_request_rejects_identity_injection() -> None:
    with pytest.raises(ValidationError):
        ChatRequest.model_validate(
            {"message": "show policy", "role": "administrator", "user_id": "attacker"}
        )


def test_final_response_contract_is_serializable() -> None:
    response = FinalResponse(
        request_id=uuid4(),
        conversation_id=uuid4(),
        status=ResponseStatus.COMPLETE,
        answer="Remote work is allowed under the cited conditions [1].",
        citations=[
            Citation(
                citation_id="1",
                evidence_id="ev_001",
                document_id="policy-remote-work-001",
                title="Remote Work Policy",
                chunk_id="policy-remote-work-001#2",
                source_path="policies/remote-work.md",
            )
        ],
    )

    assert response.model_dump(mode="json")["status"] == "complete"


def test_error_envelope_has_stable_shape() -> None:
    request_id = uuid4()
    envelope = ErrorEnvelope(
        error=ApiError(
            code="rate_limit_exceeded",
            message="Request limit exceeded. Try again later.",
            request_id=request_id,
            retryable=True,
        )
    )

    payload = envelope.model_dump(mode="json")
    assert payload == {
        "error": {
            "code": "rate_limit_exceeded",
            "message": "Request limit exceeded. Try again later.",
            "request_id": str(request_id),
            "retryable": True,
            "details": {},
        }
    }
