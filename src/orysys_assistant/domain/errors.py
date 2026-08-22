"""Stable application error taxonomy; HTTP mapping belongs to the API layer."""


class ApplicationError(Exception):
    """Base class for expected, safely reportable application failures."""


class InvalidRequestError(ApplicationError):
    pass


class AuthenticationError(ApplicationError):
    pass


class AuthorizationError(ApplicationError):
    pass


class RateLimitError(ApplicationError):
    pass


class RetrievalUnavailableError(ApplicationError):
    pass


class ModelUnavailableError(ApplicationError):
    pass


class ToolTimeoutError(ApplicationError):
    pass


class CitationValidationError(ApplicationError):
    pass


class InsufficientEvidenceError(ApplicationError):
    pass
