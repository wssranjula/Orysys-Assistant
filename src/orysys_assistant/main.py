"""FastAPI application factory and development entry point."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from orysys_assistant.api.error_handlers import register_error_handlers
from orysys_assistant.api.middleware import RequestContextMiddleware
from orysys_assistant.api.routes import chat, conversations, feedback, health
from orysys_assistant.config import Settings, get_settings
from orysys_assistant.observability.logging import configure_logging, get_logger


def create_app(settings: Settings | None = None) -> FastAPI:
    resolved_settings = settings or get_settings()
    configure_logging(resolved_settings.log_level)
    logger = get_logger()

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        logger.info(
            "application_started",
            environment=resolved_settings.app_env,
            langsmith_tracing=resolved_settings.langsmith_enabled,
        )
        yield
        logger.info("application_stopped")

    app = FastAPI(
        title=resolved_settings.app_name,
        version="0.1.0",
        lifespan=lifespan,
    )
    app.state.settings = resolved_settings
    app.add_middleware(RequestContextMiddleware)
    register_error_handlers(app)
    app.include_router(health.router)
    app.include_router(chat.router)
    app.include_router(conversations.router)
    app.include_router(feedback.router)
    return app


app = create_app()
