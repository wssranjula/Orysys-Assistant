# Activity, Trace Correlation, and Golden Evaluation

Phase 8 makes the assistant's operation inspectable without exposing internal prompts, hidden
reasoning, credentials, raw enterprise payloads, or restricted document text.

## Real-time activity panel

The Streamlit panel consumes the same named SSE events as other clients and projects them into:

- one request/trace ID;
- current agent and LangGraph node;
- a short route-level plan summary;
- active tool and execution status;
- retrieval mode and safe business filters;
- candidate-document and selected-evidence counts;
- memory and output-validation status;
- explicit partial/degraded state;
- a bounded operational timeline.

The chat surface renders citation source details in an evidence drawer. It shows citation ID, title,
document ID, chunk ID, and repository source path, but never the full retrieved chunk.

`sanitize_activity_metadata` is the API-side display boundary. Only documented scalar fields and
four retrieval-filter keys can enter SSE metadata. Unknown keys—including prompts, authorization
headers, namespaces, raw MCP output, and document bodies—are discarded even if an agent transition
accidentally supplies them.

## Trace tree

The API establishes a LangSmith tracing context before creating the root-agent task. Dynamic
metadata includes request ID, conversation ID, role, and root agent. The context propagates through
the existing traceable router, root orchestration, delegated subagents, research graph workers,
authorization decisions, Tool Gateway, hybrid retrieval, MCP adapter, and output validator.

Tool invocations add tool name and role metadata. Static trace metadata identifies agent,
subagent, graph node, transport, and deterministic security controls. Structured JSON logs use the
same request-scoped identifiers, allowing local correlation when LangSmith is disabled.

## Golden evaluation

Run the frozen ten-scenario suite offline:

```powershell
uv run python scripts/run_golden_evaluation.py
```

The runner uses the real FastAPI SSE surface, root orchestrator, memory retrieval, authorization,
Tool Gateway, citation validator, and conversation memory. It injects the declared MCP timeout,
retrieval outage, and fabricated-citation faults at deterministic seams. The rate-limit case uses a
fresh ten-token bucket, and the follow-up case performs two requests in one owned conversation.

The machine-readable result is stored in
`data/golden_evaluation_report.json`. The current report contains:

| Metric | Result |
|---|---:|
| Golden cases | 10 |
| Route accuracy | 100% |
| Citation validity | 100% |
| Unauthorized-evidence rate | 0% |
| Groundedness | 100% |
| Permission accuracy | 100% |
| Expected completion/status | 100% |
| Partial/degraded answer clarity | 100% |

Latency values are recorded for comparison, not treated as stable correctness assertions in the
offline test adapter. Production latency should be evaluated against the deployed Pinecone, MCP,
PostgreSQL, Redis, and model-provider topology.

## Test pyramid

- Unit tests cover contracts, routing, security, retrieval, graph budgets, guardrails, metadata
  sanitization, activity projection, and evaluation scoring.
- Graph tests use controlled tools for recursion, concurrency, worker failure, and partial results.
- Integration tests cover authentication, SSE, rate limiting, owner-isolated memory, validation,
  trace consistency, and confidential-metadata suppression.
- End-to-end API tests exercise Viewer, Analyst, and Administrator through agent routing,
  retrieval/tools, output validation, and citations.
- Browser verification covers sign-in, responsive two-column layout, a live grounded request,
  trace/activity updates, and the evidence drawer.
