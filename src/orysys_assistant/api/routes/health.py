"""Liveness and readiness endpoints."""

from typing import cast

from fastapi import APIRouter, Request, Response, status

from orysys_assistant.domain.models import HealthResponse
from orysys_assistant.security.rate_limit import TokenBucketRateLimiter

router = APIRouter(tags=["health"])


@router.get("/health/live", response_model=HealthResponse)
async def live() -> HealthResponse:
    return HealthResponse(status="ok")


@router.get("/health/ready", response_model=HealthResponse)
async def ready(request: Request, response: Response) -> HealthResponse:
    limiter = cast(TokenBucketRateLimiter, request.app.state.rate_limiter)
    try:
        limiter_ready = await limiter.ping()
    except Exception:
        limiter_ready = False
    if not limiter_ready:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
        return HealthResponse(
            status="not_ready",
            components={"mock_agent": "ready", "rate_limiter": "unavailable"},
        )
    return HealthResponse(
        status="ready",
        components={"mock_agent": "ready", "rate_limiter": "ready"},
    )
