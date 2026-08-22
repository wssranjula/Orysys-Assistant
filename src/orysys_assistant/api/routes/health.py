"""Liveness and readiness endpoints."""

from typing import cast

from fastapi import APIRouter, Request, Response, status

from orysys_assistant.domain.models import HealthResponse
from orysys_assistant.retrieval.runtime import AgentRuntimeManager
from orysys_assistant.security.rate_limit import TokenBucketRateLimiter

router = APIRouter(tags=["health"])


@router.get("/health/live", response_model=HealthResponse)
async def live() -> HealthResponse:
    return HealthResponse(status="ok")


@router.get("/health/ready", response_model=HealthResponse)
async def ready(request: Request, response: Response) -> HealthResponse:
    limiter = cast(TokenBucketRateLimiter, request.app.state.rate_limiter)
    agent_runtime = cast(AgentRuntimeManager, request.app.state.agent_runtime)
    try:
        limiter_ready = await limiter.ping()
    except Exception:
        limiter_ready = False
    try:
        await agent_runtime.get_orchestrator()
        agent_ready = True
    except Exception:
        agent_ready = False
    if not limiter_ready or not agent_ready:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
        return HealthResponse(
            status="not_ready",
            components={
                "root_agent": "ready" if agent_ready else "unavailable",
                "rate_limiter": "ready" if limiter_ready else "unavailable",
            },
        )
    return HealthResponse(
        status="ready",
        components={"root_agent": "ready", "rate_limiter": "ready"},
    )
