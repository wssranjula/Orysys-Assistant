"""Fictional stateless read-only enterprise MCP server."""

from typing import Any

from mcp.server.fastmcp import FastMCP

from orysys_assistant.tools.enterprise_data import (
    EMPLOYEES,
    INCIDENTS,
    SERVICES,
    get_record,
    search_records,
)

mcp = FastMCP(
    "Commercial Bank Enterprise Records",
    host="0.0.0.0",
    port=8100,
    streamable_http_path="/mcp",
    stateless_http=True,
    json_response=True,
)


@mcp.tool()
def get_employee(employee_id: str) -> dict[str, Any]:
    return {"employee": get_record(EMPLOYEES, "employee_id", employee_id)}


@mcp.tool()
def search_employees(name: str) -> dict[str, Any]:
    return {"employees": search_records(EMPLOYEES, name)}


@mcp.tool()
def get_service(service_id: str) -> dict[str, Any]:
    return {"service": get_record(SERVICES, "service_id", service_id)}


@mcp.tool()
def search_services(query: str) -> dict[str, Any]:
    return {"services": search_records(SERVICES, query)}


@mcp.tool()
def get_incident(incident_id: str) -> dict[str, Any]:
    return {"incident": get_record(INCIDENTS, "incident_id", incident_id)}


@mcp.tool()
def search_incidents(query: str, date_range: str | None = None) -> dict[str, Any]:
    records = search_records(INCIDENTS, query)
    if date_range:
        start, _, end = date_range.partition("/")
        records = [item for item in records if start <= item["date"] <= end]
    return {"incidents": records}


if __name__ == "__main__":
    mcp.run(transport="streamable-http")
