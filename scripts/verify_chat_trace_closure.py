"""Send one chat request and verify the LangSmith chat-request run closes with outputs."""

from __future__ import annotations

import asyncio
import json
import os
import sys
import time
from uuid import uuid4

import httpx
from dotenv import load_dotenv
from langsmith import Client

load_dotenv()
API_BASE = os.getenv("UI_API_BASE_URL", "http://localhost:8000")


async def main() -> int:
    async with httpx.AsyncClient(timeout=180) as http:
        login = await http.post(
            f"{API_BASE}/v1/auth/token",
            json={
                "username": "viewer@commercialbank.test",
                "password": "ViewerDemo!2026",
            },
        )
        login.raise_for_status()
        token = login.json()["access_token"]
        request_id = None
        langsmith_run_id = None
        async with http.stream(
            "POST",
            f"{API_BASE}/v1/chat/stream",
            headers={"Authorization": f"Bearer {token}"},
            json={
                "message": "What does Commercial Bank's remote-work policy allow?",
                "conversation_id": str(uuid4()),
            },
        ) as response:
            response.raise_for_status()
            event = ""
            async for line in response.aiter_lines():
                if line.startswith("event:"):
                    event = line.removeprefix("event:").strip()
                elif line.startswith("data:"):
                    payload = json.loads(line.removeprefix("data:").strip())
                    request_id = request_id or payload.get("request_id")
                    if event == "activity":
                        langsmith_run_id = langsmith_run_id or (payload.get("metadata") or {}).get(
                            "langsmith_run_id"
                        )
                    if event == "final":
                        break

    if not request_id:
        print("missing request_id", file=sys.stderr)
        return 1

    client = Client(api_key=os.environ["LANGSMITH_API_KEY"])
    for _ in range(10):
        time.sleep(2)
        runs = list(
            client.list_runs(
                project_name=os.getenv("LANGSMITH_PROJECT", "commercial-bank-assistant"),
                filter=f'and(eq(metadata_key, "request_id"), eq(metadata_value, "{request_id}"))',
                limit=100,
            )
        )
        chat = next((run for run in runs if run.name == "chat-request"), None)
        if chat and chat.outputs:
            print(
                json.dumps(
                    {
                        "request_id": request_id,
                        "langsmith_run_id": langsmith_run_id,
                        "outputs": chat.outputs,
                        "metadata": (chat.extra or {}).get("metadata"),
                    },
                    indent=2,
                )
            )
            return 0

    print("chat-request outputs not persisted", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
