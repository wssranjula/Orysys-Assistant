"""Run golden-style chat queries and summarize LangSmith trace trees."""

from __future__ import annotations

import asyncio
import json
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from uuid import uuid4

import httpx
from dotenv import load_dotenv
from langsmith import Client

ROOT = Path(__file__).resolve().parents[1]
load_dotenv(ROOT / ".env")

API_BASE = os.getenv("UI_API_BASE_URL", "http://localhost:8000")
LANGSMITH_PROJECT = os.getenv("LANGSMITH_PROJECT", "commercial-bank-assistant")

USE_CASES = [
    {
        "id": "GQ-001",
        "role": "viewer",
        "question": "What does Commercial Bank's remote-work policy allow?",
        "expect": "direct knowledge / policy citation",
    },
    {
        "id": "GQ-002",
        "role": "analyst",
        "question": (
            "Summarize payment-failure outages from the last year and identify "
            "recurring root causes."
        ),
        "expect": "research delegation with multi-search",
    },
    {
        "id": "GQ-003",
        "role": "viewer",
        "question": "Use the employee directory tool to give me Maya Fernando's record.",
        "expect": "enterprise tool denied for viewer",
    },
    {
        "id": "GQ-004",
        "role": "viewer",
        "question": "Summarize the restricted fraud investigation playbook.",
        "expect": "restricted evidence filtered",
    },
]

PASSWORDS = {
    "viewer": "ViewerDemo!2026",
    "analyst": "AnalystDemo!2026",
    "administrator": "AdminDemo!2026",
}

USERNAMES = {
    "viewer": "viewer@commercialbank.test",
    "analyst": "analyst@commercialbank.test",
    "administrator": "admin@commercialbank.test",
}


@dataclass(slots=True)
class ProbeResult:
    case_id: str
    request_id: str | None
    langsmith_run_id: str | None
    route: str | None
    status: str | None
    run_names: list[str]
    missing_metadata: list[str]

    def as_dict(self) -> dict[str, Any]:
        return {
            "case_id": self.case_id,
            "request_id": self.request_id,
            "langsmith_run_id": self.langsmith_run_id,
            "route": self.route,
            "status": self.status,
            "run_names": self.run_names,
            "missing_metadata": self.missing_metadata,
        }


async def login(client: httpx.AsyncClient, role: str) -> str:
    response = await client.post(
        f"{API_BASE}/v1/auth/token",
        json={"username": USERNAMES[role], "password": PASSWORDS[role]},
    )
    response.raise_for_status()
    return response.json()["access_token"]


async def stream_chat(client: httpx.AsyncClient, token: str, message: str) -> dict[str, Any]:
    request_id: str | None = None
    langsmith_run_id: str | None = None
    route: str | None = None
    status: str | None = None
    final: dict[str, Any] | None = None

    async with client.stream(
        "POST",
        f"{API_BASE}/v1/chat/stream",
        headers={"Authorization": f"Bearer {token}"},
        json={"message": message, "conversation_id": str(uuid4())},
        timeout=180,
    ) as response:
        response.raise_for_status()
        event_name = ""
        async for line in response.aiter_lines():
            if line.startswith("event:"):
                event_name = line.removeprefix("event:").strip()
                continue
            if not line.startswith("data:"):
                continue
            payload = json.loads(line.removeprefix("data:").strip())
            if event_name == "activity":
                request_id = request_id or payload.get("request_id")
                metadata = payload.get("metadata") or {}
                langsmith_run_id = langsmith_run_id or metadata.get("langsmith_run_id")
                if payload.get("event_type") == "routing_completed":
                    route = metadata.get("route")
                if payload.get("event_type") == "validation_completed":
                    status = metadata.get("validation_status") or payload.get("status")
            if event_name == "final":
                final = payload
                route = route or final.get("route")
                status = status or final.get("status")

    return {
        "request_id": request_id,
        "langsmith_run_id": langsmith_run_id,
        "route": route,
        "status": status,
        "final": final,
    }


def collect_run_names(client: Client, root_run_id: str) -> list[str]:
    names: list[str] = []
    try:
        for run in client.list_runs(project_name=LANGSMITH_PROJECT, trace_id=root_run_id):
            if run.name:
                names.append(run.name)
    except Exception as exc:
        names.append(f"error:{type(exc).__name__}")
    return sorted(set(names))


def inspect_run_metadata(client: Client, root_run_id: str) -> list[str]:
    missing: list[str] = []
    try:
        root = client.read_run(root_run_id)
    except Exception:
        return ["root_run_unreadable"]

    extra = root.extra or {}
    metadata = extra.get("metadata") or {}
    for key in ("request_id", "conversation_id", "role", "agent_name"):
        if key not in metadata:
            missing.append(f"root_missing:{key}")

    child_runs = list(client.list_runs(project_name=LANGSMITH_PROJECT, trace_id=root_run_id))
    if not child_runs:
        missing.append("no_child_runs")
        return missing

    named = {run.name for run in child_runs if run.name}
    expected = {
        "chat-request",
        "root-orchestrator",
        "hybrid-knowledge-retrieval",
        "tool-gateway-execution",
        "output-validation",
    }
    for name in expected:
        if name not in named and not any(name in item for item in named):
            missing.append(f"child_missing:{name}")

    tool_runs = [run for run in child_runs if run.run_type == "tool"]
    for tool_run in tool_runs:
        tool_meta = (tool_run.extra or {}).get("metadata") or {}
        if "tool_name" not in tool_meta and tool_run.name == "tool-gateway-execution":
            missing.append("tool_run_missing:tool_name")

    return missing


async def main() -> int:
    api_key = os.getenv("LANGSMITH_API_KEY")
    if not api_key:
        print("LANGSMITH_API_KEY is not configured.", file=sys.stderr)
        return 1

    client = Client(api_key=api_key, api_url=os.getenv("LANGSMITH_ENDPOINT"))
    results: list[ProbeResult] = []

    async with httpx.AsyncClient() as http:
        for case in USE_CASES:
            print(f"\n=== {case['id']}: {case['question'][:60]}...")
            token = await login(http, case["role"])
            outcome = await stream_chat(http, token, case["question"])
            run_id = outcome["langsmith_run_id"]
            run_names: list[str] = []
            missing: list[str] = []
            if run_id:
                await asyncio.sleep(2)
                run_names = collect_run_names(client, run_id)
                missing = inspect_run_metadata(client, run_id)
            else:
                missing.append("no_langsmith_run_id_in_sse")

            results.append(
                ProbeResult(
                    case_id=case["id"],
                    request_id=outcome["request_id"],
                    langsmith_run_id=run_id,
                    route=outcome["route"],
                    status=outcome["status"],
                    run_names=run_names,
                    missing_metadata=missing,
                )
            )
            print(
                json.dumps(
                    {
                        "case": case["id"],
                        "request_id": outcome["request_id"],
                        "langsmith_run_id": run_id,
                        "route": outcome["route"],
                        "status": outcome["status"],
                        "run_names": run_names,
                        "gaps": missing,
                    },
                    indent=2,
                )
            )

    print("\n=== SUMMARY ===")
    for item in results:
        print(
            f"{item.case_id}: run={item.langsmith_run_id} route={item.route} "
            f"gaps={item.missing_metadata}"
        )

    report_path = ROOT / "data" / "langsmith_probe_report.json"
    report_path.write_text(
        json.dumps([item.as_dict() for item in results], indent=2),
        encoding="utf-8",
    )
    print(f"\nWrote {report_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
