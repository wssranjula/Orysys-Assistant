"""Typed gateway registrations for six read-only enterprise MCP tools."""

from typing import Any, cast

from pydantic import BaseModel, ConfigDict, Field

from orysys_assistant.security.authorization import Capability
from orysys_assistant.security.models import TrustedRequestContext
from orysys_assistant.tools.gateway import ToolSpec
from orysys_assistant.tools.mcp_client import EnterpriseClient


class EnterpriseInput(BaseModel):
    model_config = ConfigDict(extra="forbid")


class GetEmployeeInput(EnterpriseInput):
    employee_id: str = Field(pattern=r"^EMP-[0-9]{3}$")


class SearchEmployeesInput(EnterpriseInput):
    name: str = Field(min_length=1, max_length=200)


class GetServiceInput(EnterpriseInput):
    service_id: str = Field(pattern=r"^SVC-[A-Z]+-[0-9]{3}$")


class SearchServicesInput(EnterpriseInput):
    query: str = Field(min_length=1, max_length=500)


class GetIncidentInput(EnterpriseInput):
    incident_id: str = Field(pattern=r"^INC-[0-9]{4}-[0-9]{3}$")


class SearchIncidentsInput(EnterpriseInput):
    query: str = Field(min_length=1, max_length=500)
    date_range: str | None = Field(
        default=None, pattern=r"^[0-9]{4}-[0-9]{2}-[0-9]{2}/[0-9]{4}-[0-9]{2}-[0-9]{2}$"
    )


class EnterpriseToolHandler:
    def __init__(self, client: EnterpriseClient, tool_name: str) -> None:
        self._client = client
        self._tool_name = tool_name

    async def __call__(
        self, parameters: BaseModel, context: TrustedRequestContext
    ) -> dict[str, Any]:
        request = cast(EnterpriseInput, parameters)
        return await self._client.call(
            self._tool_name, request.model_dump(mode="json", exclude_none=True)
        )


def enterprise_tool_specs(
    client: EnterpriseClient,
    timeout_seconds: float,
    max_result_bytes: int,
    retry_attempts: int = 1,
) -> list[ToolSpec]:
    schemas: tuple[tuple[str, type[BaseModel]], ...] = (
        ("get_employee", GetEmployeeInput),
        ("search_employees", SearchEmployeesInput),
        ("get_service", GetServiceInput),
        ("search_services", SearchServicesInput),
        ("get_incident", GetIncidentInput),
        ("search_incidents", SearchIncidentsInput),
    )
    return [
        ToolSpec(
            name=name,
            capability=Capability.MCP_READ,
            input_model=input_model,
            handler=EnterpriseToolHandler(client, name),
            timeout_seconds=timeout_seconds,
            max_result_bytes=max_result_bytes,
            retry_attempts=retry_attempts,
        )
        for name, input_model in schemas
    ]
