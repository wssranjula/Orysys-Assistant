"""One bounded dummy administrative write tool for approval-flow demonstrations."""

import asyncio
from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from orysys_assistant.security.authorization import Capability
from orysys_assistant.security.models import TrustedRequestContext
from orysys_assistant.tools.gateway import ToolSpec


class ModifyIncidentInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    incident_id: str = Field(pattern=r"^INC-[0-9]{4}-[0-9]{3}$")
    status: str = Field(pattern=r"^(investigating|monitoring|resolved)$")
    reason: str = Field(min_length=5, max_length=500)


class DummyIncidentWriteStore:
    """Process-local audit store; production can replace this handler with an MCP write."""

    def __init__(self) -> None:
        self._updates: list[dict[str, Any]] = []
        self._lock = asyncio.Lock()

    async def modify(self, parameters: BaseModel, context: TrustedRequestContext) -> dict[str, Any]:
        request = ModifyIncidentInput.model_validate(parameters)
        update = {
            **request.model_dump(mode="json"),
            "modified_by": context.identity.user_id,
            "modified_at": datetime.now(UTC).isoformat(),
        }
        async with self._lock:
            self._updates.append(update)
        return {"applied": True, "incident": update}

    @property
    def updates(self) -> list[dict[str, Any]]:
        return [dict(item) for item in self._updates]


def modify_incident_spec(store: DummyIncidentWriteStore) -> ToolSpec:
    return ToolSpec(
        name="modify_incident",
        capability=Capability.ADMIN_TOOLS,
        input_model=ModifyIncidentInput,
        handler=store.modify,
        timeout_seconds=10,
        retry_attempts=0,
    )
