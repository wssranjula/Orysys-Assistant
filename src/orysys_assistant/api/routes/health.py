"""Liveness and readiness endpoints."""

from fastapi import APIRouter

from orysys_assistant.domain.models import HealthResponse

router = APIRouter(tags=["health"])


@router.get("/health/live", response_model=HealthResponse)
async def live() -> HealthResponse:
    return HealthResponse(status="ok")


@router.get("/health/ready", response_model=HealthResponse)
async def ready() -> HealthResponse:
    # Phase 1 has no external runtime dependency; later phases add component probes here.
    return HealthResponse(status="ready", components={"mock_agent": "ready"})
