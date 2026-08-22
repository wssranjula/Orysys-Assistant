"""Per-agent tool visibility wrapper over the one deterministic gateway."""

from typing import Any

from orysys_assistant.domain.errors import AuthorizationError
from orysys_assistant.security.models import TrustedRequestContext
from orysys_assistant.tools.gateway import ToolGateway


class ScopedToolbox:
    def __init__(self, gateway: ToolGateway, allowed_tools: frozenset[str]) -> None:
        self._gateway = gateway
        self._allowed_tools = allowed_tools

    @property
    def allowed_tools(self) -> frozenset[str]:
        return self._allowed_tools

    async def execute(
        self,
        tool_name: str,
        parameters: dict[str, Any],
        context: TrustedRequestContext,
    ) -> Any:
        if tool_name not in self._allowed_tools:
            raise AuthorizationError("This agent is not permitted to use the requested tool.")
        return await self._gateway.execute(tool_name, parameters, context)

