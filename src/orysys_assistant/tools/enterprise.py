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


ENTERPRISE_TOOL_DESCRIPTIONS: dict[str, str] = {
    "get_employee": (
        "Fetch one employee record from the directory by exact identifier, formatted "
        "EMP-000. Returns name, role, department, and contact details. Use "
        "search_employees first when you only have a name."
    ),
    "search_employees": (
        "Search the employee directory by name or partial name and return matching "
        "records with their EMP identifiers."
    ),
    "get_service": (
        "Fetch one service-catalog entry by exact identifier, formatted SVC-NAME-000. "
        "Returns the owning team, on-call rotation, and tier. Use search_services when "
        "you only have a service name."
    ),
    "search_services": (
        "Search the service catalog by name, capability, or owning team. Use this to "
        "resolve questions about who owns or operates a service."
    ),
    "get_incident": (
        "Fetch one incident record from the incident system by exact identifier, "
        "formatted INC-0000-000. Returns status, severity, and the affected service."
    ),
    "search_incidents": (
        "Search incident-system records by keyword, optionally narrowed to a date range "
        "formatted YYYY-MM-DD/YYYY-MM-DD. This reads the incident system of record, not "
        "the document corpus; use knowledge_search for written incident reports."
    ),
}


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
            description=ENTERPRISE_TOOL_DESCRIPTIONS[name],
        )
        for name, input_model in schemas
    ]
