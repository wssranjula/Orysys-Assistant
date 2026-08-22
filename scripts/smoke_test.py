"""Black-box delivery smoke test for the running Compose stack."""

import argparse
import json
from typing import Any

import httpx

UNSAFE_METADATA_KEYS = {
    "authorization",
    "document_content",
    "prompt",
    "raw_mcp_response",
    "system_prompt",
    "token",
}


def parse_sse(body: str) -> list[tuple[str, dict[str, Any]]]:
    events: list[tuple[str, dict[str, Any]]] = []
    event_name = "message"
    data_lines: list[str] = []
    for line in body.splitlines():
        if line.startswith("event:"):
            event_name = line.removeprefix("event:").strip()
        elif line.startswith("data:"):
            data_lines.append(line.removeprefix("data:").strip())
        elif not line and data_lines:
            events.append((event_name, json.loads("\n".join(data_lines))))
            event_name, data_lines = "message", []
    if data_lines:
        events.append((event_name, json.loads("\n".join(data_lines))))
    return events


def login(client: httpx.Client, username: str, password: str) -> str:
    response = client.post("/v1/auth/token", json={"username": username, "password": password})
    response.raise_for_status()
    return str(response.json()["access_token"])


def chat(client: httpx.Client, token: str, message: str) -> list[tuple[str, dict[str, Any]]]:
    response = client.post(
        "/v1/chat/stream",
        json={"message": message},
        headers={"Authorization": f"Bearer {token}"},
    )
    response.raise_for_status()
    if not response.headers.get("content-type", "").startswith("text/event-stream"):
        raise AssertionError("chat endpoint did not return an SSE stream")
    return parse_sse(response.text)


def assert_safe_stream(
    events: list[tuple[str, dict[str, Any]]], *, require_citations: bool
) -> dict[str, Any]:
    activity = [payload for name, payload in events if name == "activity"]
    final = next((payload for name, payload in reversed(events) if name == "final"), None)
    if final is None:
        raise AssertionError("chat stream did not contain a terminal final event")
    if final["status"] not in {"complete", "partial"}:
        raise AssertionError(f"unexpected final status: {final['status']}")
    if require_citations and not final["citations"]:
        raise AssertionError("grounded smoke response did not contain citations")
    if not any(item["event_type"] == "routing_completed" for item in activity):
        raise AssertionError("routing activity was not emitted")
    if not any(item["event_type"] == "validation_completed" for item in activity):
        raise AssertionError("validation completion activity was not emitted")
    if {item["request_id"] for item in activity} != {final["request_id"]}:
        raise AssertionError("activity and final response do not share one trace ID")
    for item in activity:
        leaked = UNSAFE_METADATA_KEYS & item.get("metadata", {}).keys()
        if leaked:
            raise AssertionError(f"unsafe activity metadata keys: {sorted(leaked)}")
    return final


def run(api_url: str, ui_url: str) -> None:
    with httpx.Client(base_url=api_url, timeout=httpx.Timeout(150, connect=5)) as client:
        live = client.get("/health/live")
        ready = client.get("/health/ready")
        live.raise_for_status()
        ready.raise_for_status()
        if live.json()["status"] != "ok" or ready.json()["status"] != "ready":
            raise AssertionError("API health contract is not ready")

        viewer = login(client, "viewer@commercialbank.test", "ViewerDemo!2026")
        viewer_final = assert_safe_stream(
            chat(client, viewer, "What does the remote-work policy allow?"),
            require_citations=True,
        )

        analyst = login(client, "analyst@commercialbank.test", "AnalystDemo!2026")
        enterprise_events = chat(client, analyst, "Who owns the Payments Gateway service?")
        enterprise_final = assert_safe_stream(enterprise_events, require_citations=False)
        if not any(
            payload.get("event_type") == "tool_completed"
            for name, payload in enterprise_events
            if name == "activity"
        ):
            raise AssertionError("MCP tool completion activity was not emitted")

    with httpx.Client(timeout=httpx.Timeout(10, connect=5)) as client:
        ui_health = client.get(f"{ui_url.rstrip('/')}/_stcore/health")
        ui_health.raise_for_status()
        if ui_health.text.strip().lower() != "ok":
            raise AssertionError("Streamlit health endpoint is not ready")

    print(
        json.dumps(
            {
                "status": "passed",
                "api": api_url,
                "ui": ui_url,
                "viewer_trace_id": viewer_final["request_id"],
                "viewer_citations": len(viewer_final["citations"]),
                "enterprise_trace_id": enterprise_final["request_id"],
            },
            indent=2,
        )
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--api-url", default="http://127.0.0.1:8000")
    parser.add_argument("--ui-url", default="http://127.0.0.1:8501")
    args = parser.parse_args()
    run(args.api_url.rstrip("/"), args.ui_url.rstrip("/"))


if __name__ == "__main__":
    main()
