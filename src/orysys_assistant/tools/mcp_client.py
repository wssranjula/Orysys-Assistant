"""Read-only MCP client seam with in-memory and streamable-HTTP adapters."""

import json
from datetime import timedelta
from typing import Any, Protocol

from langsmith import traceable
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

from orysys_assistant.domain.errors import InvalidRequestError
from orysys_assistant.tools.enterprise_data import (
    EMPLOYEES,
    INCIDENTS,
    SERVICES,
    get_record,
    search_records,
)


class EnterpriseClient(Protocol):
    async def call(self, tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]: ...


class InMemoryEnterpriseClient:
    async def call(self, tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        if tool_name == "get_employee":
            return {"employee": get_record(EMPLOYEES, "employee_id", arguments["employee_id"])}
        if tool_name == "search_employees":
            return {"employees": search_records(EMPLOYEES, arguments["name"])}
        if tool_name == "get_service":
            return {"service": get_record(SERVICES, "service_id", arguments["service_id"])}
        if tool_name == "search_services":
            return {"services": search_records(SERVICES, arguments["query"])}
        if tool_name == "get_incident":
            return {"incident": get_record(INCIDENTS, "incident_id", arguments["incident_id"])}
        if tool_name == "search_incidents":
            records = search_records(INCIDENTS, arguments["query"])
            date_range = arguments.get("date_range")
            if date_range:
                start, _, end = str(date_range).partition("/")
                records = [item for item in records if start <= item["date"] <= end]
            return {"incidents": records}
        raise InvalidRequestError("The requested MCP tool is not supported.")


class MCPClientAdapter:
    def __init__(self, url: str, timeout_seconds: float) -> None:
        self._url = url
        self._timeout = timedelta(seconds=timeout_seconds)

    @traceable(
        name="enterprise-mcp-call",
        run_type="tool",
        metadata={"transport": "streamable_http", "read_only": True},
    )
    async def call(self, tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        async with (
            streamable_http_client(self._url) as (read_stream, write_stream, _),
            ClientSession(
                read_stream,
                write_stream,
                read_timeout_seconds=self._timeout,
            ) as session,
        ):
            await session.initialize()
            result = await session.call_tool(tool_name, arguments)
        if result.isError:
            raise InvalidRequestError("The enterprise MCP tool returned an error.")
        structured = result.structuredContent
        if isinstance(structured, dict):
            value = structured.get("result", structured)
            return value if isinstance(value, dict) else {"result": value}
        for content in result.content:
            text = getattr(content, "text", None)
            if text:
                parsed = json.loads(text)
                return parsed if isinstance(parsed, dict) else {"result": parsed}
        return {}
