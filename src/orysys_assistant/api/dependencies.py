"""FastAPI dependency aliases."""

from typing import Annotated, cast

import structlog
from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from redis.exceptions import RedisError

from orysys_assistant.agent.orchestrator import RootOrchestrator
from orysys_assistant.config import Settings
from orysys_assistant.domain.errors import (
    AuthenticationError,
    RateLimitError,
    RateLimitUnavailableError,
)
from orysys_assistant.guardrails.output import OutputValidator
from orysys_assistant.memory.repository import ConversationRepository
from orysys_assistant.memory.runtime import MemoryRuntime
from orysys_assistant.observability.logging import get_logger
from orysys_assistant.retrieval.runtime import AgentRuntimeManager
from orysys_assistant.security.access_scope import AccessScopeService
from orysys_assistant.security.authentication import AuthenticationService
from orysys_assistant.security.authorization import AuthorizationPolicy, Capability
from orysys_assistant.security.models import AccessScope, TrustedRequestContext, UserIdentity
from orysys_assistant.security.rate_limit import TokenBucketRateLimiter

logger = get_logger()
bearer_scheme = HTTPBearer(auto_error=False)


def get_request_settings(request: Request) -> Settings:
    return cast(Settings, request.app.state.settings)


SettingsDependency = Annotated[Settings, Depends(get_request_settings)]


def get_output_validator(request: Request) -> OutputValidator:
    return cast(OutputValidator, request.app.state.output_validator)


OutputValidatorDependency = Annotated[OutputValidator, Depends(get_output_validator)]


def get_authentication_service(request: Request) -> AuthenticationService:
    return cast(AuthenticationService, request.app.state.authentication)


def get_scope_service(request: Request) -> AccessScopeService:
    return cast(AccessScopeService, request.app.state.access_scope_service)


def get_authorization_policy(request: Request) -> AuthorizationPolicy:
    return cast(AuthorizationPolicy, request.app.state.authorization_policy)


def get_rate_limiter(request: Request) -> TokenBucketRateLimiter:
    return cast(TokenBucketRateLimiter, request.app.state.rate_limiter)


AuthenticationDependency = Annotated[AuthenticationService, Depends(get_authentication_service)]
ScopeServiceDependency = Annotated[AccessScopeService, Depends(get_scope_service)]
AuthorizationDependency = Annotated[AuthorizationPolicy, Depends(get_authorization_policy)]
RateLimiterDependency = Annotated[TokenBucketRateLimiter, Depends(get_rate_limiter)]


async def get_root_orchestrator(request: Request) -> RootOrchestrator:
    runtime = cast(AgentRuntimeManager, request.app.state.agent_runtime)
    return await runtime.get_orchestrator()


RootOrchestratorDependency = Annotated[RootOrchestrator, Depends(get_root_orchestrator)]


async def get_conversation_repository(request: Request) -> ConversationRepository:
    runtime = cast(MemoryRuntime, request.app.state.memory_runtime)
    await runtime.start()
    return runtime.repository


ConversationRepositoryDependency = Annotated[
    ConversationRepository, Depends(get_conversation_repository)
]


def get_current_identity(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer_scheme)],
    authentication: AuthenticationDependency,
) -> UserIdentity:
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise AuthenticationError("A valid bearer token is required.")
    identity = authentication.authenticate_token(credentials.credentials)
    structlog.contextvars.bind_contextvars(
        user_id=identity.user_id,
        role=identity.role.value,
    )
    return identity


IdentityDependency = Annotated[UserIdentity, Depends(get_current_identity)]


def get_access_scope(
    identity: IdentityDependency,
    service: ScopeServiceDependency,
) -> AccessScope:
    return service.build(identity)


AccessScopeDependency = Annotated[AccessScope, Depends(get_access_scope)]


async def get_trusted_chat_context(
    identity: IdentityDependency,
    access_scope: AccessScopeDependency,
    policy: AuthorizationDependency,
    limiter: RateLimiterDependency,
) -> TrustedRequestContext:
    policy.require(identity, Capability.CHAT)
    try:
        result = await limiter.consume(identity.user_id, identity.role)
    except (RedisError, OSError, TimeoutError) as exc:
        logger.error(
            "rate_limit_check_failed",
            user_id=identity.user_id,
            role=identity.role.value,
            error_type=type(exc).__name__,
            result="unavailable",
        )
        raise RateLimitUnavailableError("Rate-limit service is temporarily unavailable.") from exc

    logger.info(
        "rate_limit_checked",
        user_id=identity.user_id,
        role=identity.role.value,
        remaining=result.remaining,
        result="allowed" if result.allowed else "denied",
    )
    if not result.allowed:
        raise RateLimitError(
            "Request limit exceeded. Try again later.",
            details={"retry_after_seconds": result.retry_after_seconds},
        )
    return TrustedRequestContext(
        identity=identity,
        access_scope=access_scope,
        rate_limit_remaining=result.remaining,
    )


TrustedChatContextDependency = Annotated[TrustedRequestContext, Depends(get_trusted_chat_context)]
