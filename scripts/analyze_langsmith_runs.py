"""Summarize LangSmith trace trees for recent probe request IDs."""

from __future__ import annotations

import os
import sys
from pathlib import Path

from dotenv import load_dotenv
from langsmith import Client

from orysys_assistant.observability.agent_tracing import collapse_run_names, is_middleware_run_name

ROOT = Path(__file__).resolve().parents[1]
load_dotenv(ROOT / ".env")


def main() -> None:
    request_ids = sys.argv[1:] or [
        "a82ba81f-94d6-47e3-b61c-f8f38de240ae",
        "50175271-d7bb-4941-a8e1-e290760155cc",
        "dbea6a04-18df-4452-8e25-d3db40f8fb48",
        "074be297-2fc2-4798-9fee-b177ab457d65",
    ]
    client = Client(
        api_key=os.environ["LANGSMITH_API_KEY"],
        api_url=os.getenv("LANGSMITH_ENDPOINT", "https://api.smith.langchain.com"),
    )
    project = os.getenv("LANGSMITH_PROJECT", "commercial-bank-assistant")

    for request_id in request_ids:
        runs = list(
            client.list_runs(
                project_name=project,
                filter=(
                    f'and(eq(metadata_key, "request_id"), eq(metadata_value, "{request_id}"))'
                ),
                limit=100,
            )
        )
        names = [run.name for run in runs if run.name]
        grouped = collapse_run_names(names)
        roots = [run for run in runs if run.parent_run_id is None]
        middleware = sum(1 for name in names if is_middleware_run_name(name))

        print(f"\nREQUEST {request_id}")
        print(
            f" total_runs={len(runs)} middleware_runs={middleware} "
            f"app_runs={len(grouped['app'])}"
        )
        print(f" roots={[(run.name, str(run.id)) for run in roots]}")
        print(f" app_spans={grouped['app']}")
        middleware_preview = grouped["middleware"][:8]
        if len(grouped["middleware"]) > 8:
            middleware_preview = [*middleware_preview, "..."]
        print(f" middleware_spans={middleware_preview}")
        if grouped["other"]:
            other_preview = grouped["other"][:8]
            if len(grouped["other"]) > 8:
                other_preview = [*other_preview, "..."]
            print(f" other_spans={other_preview}")


if __name__ == "__main__":
    main()
