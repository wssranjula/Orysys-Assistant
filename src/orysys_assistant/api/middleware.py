"""Request correlation and completion logging middleware."""

from time import perf_counter
from uuid import uuid4

import structlog
from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.responses import Response

from orysys_assistant.observability.logging import get_logger

logger = get_logger()


class RequestContextMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        request_id = uuid4()
        request.state.request_id = request_id
        structlog.contextvars.clear_contextvars()
        structlog.contextvars.bind_contextvars(
            request_id=str(request_id),
            method=request.method,
            path=request.url.path,
        )
        started = perf_counter()
        try:
            response = await call_next(request)
        finally:
            duration_ms = round((perf_counter() - started) * 1_000, 2)

        response.headers["X-Request-ID"] = str(request_id)
        logger.info(
            "http_request_completed",
            duration_ms=duration_ms,
            result=response.status_code,
        )
        structlog.contextvars.clear_contextvars()
        return response
