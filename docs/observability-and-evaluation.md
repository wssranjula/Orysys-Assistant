# Activity, Trace Correlation, and Golden Evaluation

The activity panel and golden runner make the assistant's operation inspectable without exposing
internal prompts, hidden reasoning, credentials, raw enterprise payloads, or restricted document
text.

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

The API publishes a top-level `chat-request` LangSmith run for every streamed request.
That run carries request ID, conversation ID, role, route, validation outcome, evidence count,
and duration in metadata and tags. A nested tracing context propagates the same identifiers
through the root agent loop, each delegation tool, delegated specialists, authorization decisions,
Tool Gateway, hybrid retrieval, MCP adapter, and output validator.

Tool invocations add tool name and role metadata plus searchable tags. Static trace metadata
identifies agent, subagent, graph node, transport, and deterministic security controls.
Structured JSON logs use the same request-scoped identifiers, allowing local correlation when
LangSmith is disabled.

LangGraph middleware spans (todo lists, call limits) also appear in LangSmith. Filter by
the `app-span` tag or run name to focus on application-level spans:

- LangSmith filter: `has(tags, "app-span")`
- CLI summary: `uv run python scripts/analyze_langsmith_runs.py <request_id>`

When `LANGSMITH_QUIET_MIDDLEWARE_TRACES=true` (default), middleware spans omit bulky
state payloads and the root orchestrator uses one consolidated delegation limiter instead
of four separate middleware nodes.

Tool invocations are labeled with their registered tool name in LangSmith (for example
`knowledge_search`, `consult_research_specialist`, `get_incident`) rather than generic
`tools` or `tool-gateway-execution` spans. Filter with `has(tags, "app-span")` or search
by run name: `eq(name, "knowledge_search")`.

## Golden evaluation

Run the frozen ten-scenario suite:

```powershell
uv run python scripts/run_golden_evaluation.py
```

The runner uses the real FastAPI SSE surface, root orchestrator, memory retrieval, authorization,
Tool Gateway, citation validator, and conversation memory. It injects the declared MCP timeout,
retrieval outage, and fabricated-citation faults at deterministic seams. The rate-limit case uses a
fresh ten-token bucket, and the follow-up case performs two requests in one owned conversation.

`OPENAI_API_KEY` is required. Every specialist is a model-driven loop, so the report measures live
agent behaviour rather than a credential-free deterministic profile, and the runner exits with that
message instead of scoring a system nobody deploys. Run-to-run figures therefore vary; the stored
report is a record of one measured run, not a fixture the test suite asserts against.

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

- Unit tests cover contracts, root delegation, security, retrieval, agent budgets, guardrails,
  metadata sanitization, activity projection, and evaluation scoring.
- Specialist tests script the model's turns against controlled tools, so the real agent loop,
  middleware, and gateway run while assertions about planning, parallel retrieval, budget
  enforcement, tool failure, timeouts, and partial results stay deterministic.
- Integration tests cover authentication, SSE, rate limiting, owner-isolated memory, validation,
  trace consistency, and confidential-metadata suppression.
- End-to-end API tests exercise Viewer, Analyst, and Administrator through root delegation,
  retrieval/tools, output validation, and citations.
- Browser verification covers sign-in, responsive two-column layout, a live grounded request,
  trace/activity updates, and the evidence drawer.
