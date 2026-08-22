"""Central allowlist, RBAC, schema, timeout, and audit boundary for every tool."""

import asyncio
import json
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from typing import Any

from langsmith import traceable, tracing_context
from pydantic import BaseModel, ValidationError

from orysys_assistant.domain.errors import (
    InvalidRequestError,
    ToolTimeoutError,
)
from orysys_assistant.observability.logging import get_logger
from orysys_assistant.security.authorization import AuthorizationPolicy, Capability
from orysys_assistant.security.models import TrustedRequestContext

logger = get_logger()

FORBIDDEN_CONTEXT_KEYS = frozenset(
    {
        "role",
        "user_id",
        "access_level",
        "namespace",
        "organization_id",
        "conversation_owner",
    }
)
ToolHandler = Callable[[BaseModel, TrustedRequestContext], Awaitable[Any]]


@dataclass(frozen=True, slots=True)
class ToolSpec:
    name: str
    capability: Capability
    input_model: type[BaseModel]
    handler: ToolHandler
    timeout_seconds: float = 10
    max_result_bytes: int = 100_000
    retry_attempts: int = 0


class ToolGateway:
    def __init__(
        self,
        policy: AuthorizationPolicy,
        tools: Mapping[str, ToolSpec] | None = None,
    ) -> None:
        self._policy = policy
        self._tools = dict(tools or {})

    def register(self, spec: ToolSpec) -> None:
        if spec.name in self._tools:
            raise ValueError(f"Tool is already registered: {spec.name}")
        self._tools[spec.name] = spec

    @staticmethod
    def _forbidden_keys(value: Any) -> set[str]:
        found: set[str] = set()
        if isinstance(value, dict):
            for key, nested in value.items():
                normalized = str(key).lower()
                if normalized in FORBIDDEN_CONTEXT_KEYS:
                    found.add(normalized)
                found.update(ToolGateway._forbidden_keys(nested))
        elif isinstance(value, list):
            for nested in value:
                found.update(ToolGateway._forbidden_keys(nested))
        return found

    async def execute(
        self,
        tool_name: str,
        parameters: dict[str, Any],
        context: TrustedRequestContext,
    ) -> Any:
        spec = self._tools.get(tool_name)
        if spec is None:
            raise InvalidRequestError("The requested tool is not registered.")

        self._policy.require(context.identity, spec.capability)
        forbidden = self._forbidden_keys(parameters)
        if forbidden:
            logger.warning(
                "tool_parameters_rejected",
                user_id=context.identity.user_id,
                role=context.identity.role.value,
                tool=tool_name,
                result="reserved_context_field",
            )
            raise InvalidRequestError(
                "Tool parameters contain server-controlled fields.",
                details={"fields": sorted(forbidden)},
            )

        try:
            validated = spec.input_model.model_validate(parameters)
        except ValidationError as exc:
            fields = [".".join(str(part) for part in error["loc"]) for error in exc.errors()]
            raise InvalidRequestError(
                "Tool parameters are invalid.", details={"fields": fields}
            ) from exc

        logger.info(
            "tool_execution_started",
            user_id=context.identity.user_id,
            role=context.identity.role.value,
            tool=tool_name,
            result="started",
        )
        with tracing_context(
            metadata={
                "tool_name": tool_name,
                "role": context.identity.role.value,
                "agent_name": "tool_gateway",
            }
        ):
            result = await self._invoke(spec, validated, context)

        result_size = len(json.dumps(result, default=str).encode("utf-8"))
        if result_size > spec.max_result_bytes:
            raise InvalidRequestError("The tool result exceeded its allowed size.")
        logger.info(
            "tool_execution_completed",
            user_id=context.identity.user_id,
            role=context.identity.role.value,
            tool=tool_name,
            result="completed",
            result_size_bytes=result_size,
        )
        return result

    @staticmethod
    @traceable(
        name="tool-gateway-execution",
        run_type="tool",
        metadata={"control": "authorized_tool_gateway"},
    )
    async def _invoke(
        spec: ToolSpec,
        validated: BaseModel,
        context: TrustedRequestContext,
    ) -> Any:
        for attempt in range(spec.retry_attempts + 1):
            try:
                async with asyncio.timeout(spec.timeout_seconds):
                    return await spec.handler(validated, context)
            except TimeoutError as exc:
                if attempt < spec.retry_attempts:
                    await asyncio.sleep(0.25 * (2**attempt))
                    continue
                logger.warning(
                    "tool_execution_completed",
                    user_id=context.identity.user_id,
                    role=context.identity.role.value,
                    tool=spec.name,
                    result="timeout",
                )
                raise ToolTimeoutError("The tool did not respond before its deadline.") from exc
        raise AssertionError("tool retry loop must return or raise")
