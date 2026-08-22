# Agent Orchestration (Phase 4)

The production runtime is one compiled LangGraph with deterministic routing, four bounded branches,
and a shared synthesis node. Routing remains reproducible and visible in SSE activity events and
LangSmith traces.

| Route | Agent | Available tools | Purpose |
|---|---|---|---|
| `direct_knowledge` | Root | `knowledge_search` | Focused questions answered from authorized evidence |
| `research` | Research specialist | `knowledge_search` | Multi-document investigation and grounded findings |
| `analysis` | Analysis specialist | `knowledge_search`, `structured_analysis` | Bounded aggregation over retrieved evidence |
| `enterprise` | Enterprise-tool specialist | Six approved read-only MCP tools | Ownership, directory, and incident-record lookups |

Tool visibility is enforced by each agent's `ScopedToolbox`. Execution then passes through the
single `ToolGateway`, which independently enforces registration, RBAC capability, server-owned
context, typed inputs, deadlines, result limits, and audit events. A prompt therefore cannot grant
an agent another tool or broaden retrieval scope.

## Routing and outputs

Enterprise intent has precedence over analysis, followed by research and direct knowledge. The
router emits an `agent_started` event and a `routing_completed` event. Delegated routes additionally
emit `subagent_started` and `subagent_completed`; direct retrieval emits retrieval start/completion.
All specialist results validate through strict Pydantic contracts before the root converts them to
the frozen API response and citation contracts.

The Phase 6 Analysis specialist uses the separately gateway-enforced controlled analysis tool. It
cannot execute arbitrary Python. The Enterprise specialist similarly reaches only six registered
read-only MCP operations.

The Research specialist owns the compiled, recursive, budgeted Phase 5 LangGraph described in
[research-graph.md](research-graph.md). The root still delegates through the same small static agent
surface; recursion occurs only inside that code-controlled specialist workflow.

## Production graph and synthesis

`build_root_orchestrator` builds the only agent runtime used by the API. The outer graph contains
`route`, `direct_knowledge`, `research`, `analysis`, `enterprise`, and `synthesize` nodes. It is
compiled with the configured checkpointer, so message state is isolated by the server-derived
user/conversation thread ID.

When `AGENT_SYNTHESIS_ENABLED=true` and `OPENAI_API_KEY` is configured, the synthesis node uses a
LangChain agent with provider-backed structured output. The model receives only the bounded
specialist result and authorized evidence. Citation resolution and output validation remain
deterministic application controls. Without a key, the same graph uses the deterministic draft,
which keeps offline development and evaluation reproducible.

Graph `custom` updates are streamed directly to SSE; callback-based transitions remain only as a
compatibility surface for tests and external adapters.
