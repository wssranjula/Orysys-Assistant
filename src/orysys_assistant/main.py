"""FastAPI application factory and development entry point."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING

from fastapi import FastAPI

from orysys_assistant.agent.approval_graph import ApprovalService
from orysys_assistant.api.error_handlers import register_error_handlers
from orysys_assistant.api.middleware import RequestContextMiddleware
from orysys_assistant.api.routes import (
    approvals,
    auth,
    chat,
    conversations,
    feedback,
    health,
    preferences,
)
from orysys_assistant.config import Settings, get_settings
from orysys_assistant.guardrails.input import InputGuard
from orysys_assistant.guardrails.output import OutputValidator
from orysys_assistant.memory.runtime import MemoryRuntime
from orysys_assistant.observability.logging import configure_logging, get_logger
from orysys_assistant.retrieval.runtime import AgentRuntimeManager
from orysys_assistant.security.access_scope import AccessScopeService
from orysys_assistant.security.authentication import AuthenticationService
from orysys_assistant.security.authorization import AuthorizationPolicy
from orysys_assistant.security.rate_limit import TokenBucketRateLimiter, build_rate_limiter
from orysys_assistant.tools.admin import DummyIncidentWriteStore, modify_incident_spec
from orysys_assistant.tools.gateway import ToolGateway

if TYPE_CHECKING:
    from orysys_assistant.agent.router import AgentRouter
    from orysys_assistant.tools.mcp_client import EnterpriseClient


def create_app(
    settings: Settings | None = None,
    rate_limiter: TokenBucketRateLimiter | None = None,
    enterprise_client: "EnterpriseClient | None" = None,
    agent_router: "AgentRouter | None" = None,
) -> FastAPI:
    resolved_settings = settings or get_settings()
    configure_logging(resolved_settings.log_level)
    logger = get_logger()
    authentication = AuthenticationService(resolved_settings)
    access_scope_service = AccessScopeService(resolved_settings)
    authorization_policy = AuthorizationPolicy()
    resolved_rate_limiter = rate_limiter or build_rate_limiter(resolved_settings)
    tool_gateway = ToolGateway(authorization_policy)
    incident_write_store = DummyIncidentWriteStore()
    tool_gateway.register(modify_incident_spec(incident_write_store))
    memory_runtime = MemoryRuntime(resolved_settings)
    approval_service = ApprovalService(
        tool_gateway,
        resolved_settings.database_url if resolved_settings.memory_backend == "postgres" else None,
    )
    agent_runtime = AgentRuntimeManager(
        resolved_settings,
        tool_gateway,
        memory_runtime,
        enterprise_client=enterprise_client,
        agent_router=agent_router,
    )

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        logger.info(
            "application_started",
            environment=resolved_settings.app_env,
            langsmith_tracing=resolved_settings.langsmith_enabled,
        )
        await approval_service.start()
        await agent_runtime.get_orchestrator()
        yield
        await agent_runtime.close()
        await approval_service.close()
        await memory_runtime.close()
        await resolved_rate_limiter.close()
        logger.info("application_stopped")

    app = FastAPI(
        title=resolved_settings.app_name,
        version="0.1.0",
        lifespan=lifespan,
    )
    app.state.settings = resolved_settings
    app.state.authentication = authentication
    app.state.access_scope_service = access_scope_service
    app.state.authorization_policy = authorization_policy
    app.state.rate_limiter = resolved_rate_limiter
    app.state.tool_gateway = tool_gateway
    app.state.agent_runtime = agent_runtime
    app.state.memory_runtime = memory_runtime
    app.state.approval_service = approval_service
    app.state.incident_write_store = incident_write_store
    app.state.input_guard = InputGuard()
    app.state.output_validator = OutputValidator()
    app.add_middleware(RequestContextMiddleware)
    register_error_handlers(app)
    app.include_router(health.router)
    app.include_router(auth.router)
    app.include_router(chat.router)
    app.include_router(conversations.router)
    app.include_router(feedback.router)
    app.include_router(preferences.router)
    app.include_router(approvals.router)
    return app


app = create_app()
