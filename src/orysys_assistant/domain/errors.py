"""Stable application error taxonomy and safe public attributes."""

from typing import Any


class ApplicationError(Exception):
    """Base class for expected, safely reportable application failures."""

    code = "application_error"
    status_code = 500
    retryable = False

    def __init__(self, message: str, *, details: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.details = details or {}


class InvalidRequestError(ApplicationError):
    code = "invalid_request"
    status_code = 400


class AuthenticationError(ApplicationError):
    code = "authentication_failed"
    status_code = 401


class AuthorizationError(ApplicationError):
    code = "authorization_denied"
    status_code = 403


class RateLimitError(ApplicationError):
    code = "rate_limit_exceeded"
    status_code = 429
    retryable = True


class RetrievalUnavailableError(ApplicationError):
    code = "retrieval_unavailable"
    status_code = 503
    retryable = True


class ModelUnavailableError(ApplicationError):
    code = "model_unavailable"
    status_code = 503
    retryable = True


class ToolTimeoutError(ApplicationError):
    code = "execution_timeout"
    status_code = 504
    retryable = True


class CitationValidationError(ApplicationError):
    code = "citation_validation_failed"
    status_code = 422


class InsufficientEvidenceError(ApplicationError):
    code = "insufficient_evidence"
    status_code = 422
