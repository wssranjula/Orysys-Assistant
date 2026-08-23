"""Check whether output-validation and chat-request outputs exist for one request."""

import os
import sys

from dotenv import load_dotenv
from langsmith import Client

load_dotenv()
request_id = sys.argv[1] if len(sys.argv) > 1 else ""
client = Client(api_key=os.environ["LANGSMITH_API_KEY"])
runs = list(
    client.list_runs(
        project_name=os.getenv("LANGSMITH_PROJECT", "commercial-bank-assistant"),
        filter=f'and(eq(metadata_key, "request_id"), eq(metadata_value, "{request_id}"))',
        limit=100,
    )
)
names = sorted({run.name for run in runs})
print("has output-validation:", "output-validation" in names)
for run in runs:
    if run.name == "chat-request":
        print("chat-request metadata:", (run.extra or {}).get("metadata"))
        print("chat-request outputs:", run.outputs)
        print("chat-request end_time:", run.end_time)
        print("chat-request status:", run.status)
