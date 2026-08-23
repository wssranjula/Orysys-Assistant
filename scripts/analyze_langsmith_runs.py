"""Summarize LangSmith trace trees for recent probe request IDs."""

from __future__ import annotations

import os
from collections import Counter
from pathlib import Path

from dotenv import load_dotenv
from langsmith import Client

ROOT = Path(__file__).resolve().parents[1]
load_dotenv(ROOT / ".env")

REQUEST_IDS = [
    "a82ba81f-94d6-47e3-b61c-f8f38de240ae",
    "50175271-d7bb-4941-a8e1-e290760155cc",
    "dbea6a04-18df-4452-8e25-d3db40f8fb48",
    "074be297-2fc2-4798-9fee-b177ab457d65",
]

CUSTOM_MARKERS = (
    "chat-request",
    "root",
    "delegate",
    "hybrid",
    "gateway",
    "validation",
    "output-validation",
    "authorization",
    "knowledge",
    "research",
    "analysis",
    "enterprise",
)


def main() -> None:
    client = Client(
        api_key=os.environ["LANGSMITH_API_KEY"],
        api_url=os.getenv("LANGSMITH_ENDPOINT", "https://api.smith.langchain.com"),
    )
    project = os.getenv("LANGSMITH_PROJECT", "commercial-bank-assistant")

    for request_id in REQUEST_IDS:
        runs = list(
            client.list_runs(
                project_name=project,
                filter=(
                    f'and(eq(metadata_key, "request_id"), eq(metadata_value, "{request_id}"))'
                ),
                limit=100,
            )
        )
        names = Counter(run.name for run in runs)
        roots = [run for run in runs if run.parent_run_id is None]
        custom = {
            name: count
            for name, count in sorted(names.items())
            if name and any(marker in name for marker in CUSTOM_MARKERS)
        }
        middleware = sum(count for name, count in names.items() if name and "Middleware" in name)

        print(f"\nREQUEST {request_id}")
        print(f" total_runs={len(runs)} roots={[(run.name, str(run.id)) for run in roots]}")
        print(f" custom={custom}")
        print(f" middleware_runs={middleware}")


if __name__ == "__main__":
    main()
