"""POC credential exchange for the four hardcoded users."""

from fastapi import APIRouter
from redis.exceptions import RedisError

from orysys_assistant.api.dependencies import (
    AuthenticationDependency,
    RateLimiterDependency,
)
from orysys_assistant.domain.errors import RateLimitError, RateLimitUnavailableError
from orysys_assistant.domain.models import LoginRequest, Role, TokenResponse
from orysys_assistant.observability.logging import get_logger

router = APIRouter(prefix="/v1/auth", tags=["authentication"])
logger = get_logger()


@router.post("/token", response_model=TokenResponse)
async def issue_token(
    payload: LoginRequest,
    authentication: AuthenticationDependency,
    limiter: RateLimiterDependency,
) -> TokenResponse:
    login_key = f"login:{payload.username.lower()}"
    try:
        result = await limiter.consume(login_key, Role.VIEWER)
    except (RedisError, OSError, TimeoutError) as exc:
        logger.error(
            "login_rate_limit_check_failed",
            username=payload.username,
            error_type=type(exc).__name__,
        )
        raise RateLimitUnavailableError("Rate-limit service is temporarily unavailable.") from exc
    if not result.allowed:
        raise RateLimitError(
            "Too many login attempts. Try again later.",
            details={"retry_after_seconds": result.retry_after_seconds},
        )
    identity, token = authentication.authenticate_password(
        payload.username,
        payload.password.get_secret_value(),
    )
    return TokenResponse(
        access_token=token,
        user_id=identity.user_id,
        display_name=identity.display_name,
        role=identity.role,
    )
