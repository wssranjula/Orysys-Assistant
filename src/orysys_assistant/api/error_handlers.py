"""Map expected and unexpected failures to the one public error envelope."""

from typing import Any
from uuid import UUID, uuid4

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from orysys_assistant.domain.errors import ApplicationError
from orysys_assistant.domain.models import ApiError, ErrorEnvelope
from orysys_assistant.observability.logging import get_logger

logger = get_logger()


def _request_id(request: Request) -> UUID:
    value = getattr(request.state, "request_id", None)
    return value if isinstance(value, UUID) else uuid4()


def _response(
    request: Request,
    *,
    status_code: int,
    code: str,
    message: str,
    retryable: bool = False,
    details: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
) -> JSONResponse:
    envelope = ErrorEnvelope(
        error=ApiError(
            code=code,
            message=message,
            request_id=_request_id(request),
            retryable=retryable,
            details=details or {},
        )
    )
    return JSONResponse(
        status_code=status_code,
        content=envelope.model_dump(mode="json"),
        headers=headers,
    )


async def application_error_handler(request: Request, exc: ApplicationError) -> JSONResponse:
    logger.warning("request_failed", error_type=exc.code, result="rejected")
    headers = None
    if exc.code == "rate_limit_exceeded":
        retry_after = exc.details.get("retry_after_seconds", 1)
        headers = {"Retry-After": str(retry_after)}
    return _response(
        request,
        status_code=exc.status_code,
        code=exc.code,
        message=exc.message,
        retryable=exc.retryable,
        details=exc.details,
        headers=headers,
    )


async def validation_error_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
    fields = [
        {"location": ".".join(str(part) for part in error["loc"]), "type": error["type"]}
        for error in exc.errors()
    ]
    logger.warning("request_validation_failed", error_type="invalid_request", result="rejected")
    return _response(
        request,
        status_code=400,
        code="invalid_request",
        message="The request payload is invalid.",
        details={"fields": fields},
    )


async def unexpected_error_handler(request: Request, exc: Exception) -> JSONResponse:
    logger.exception("unexpected_request_failure", error_type=type(exc).__name__, result="failed")
    return _response(
        request,
        status_code=500,
        code="internal_error",
        message="The request could not be completed.",
        retryable=True,
    )


def register_error_handlers(app: FastAPI) -> None:
    app.add_exception_handler(ApplicationError, application_error_handler)  # type: ignore[arg-type]
    app.add_exception_handler(RequestValidationError, validation_error_handler)  # type: ignore[arg-type]
    app.add_exception_handler(Exception, unexpected_error_handler)
