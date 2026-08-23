# Agent Orchestration

The production runtime is one compiled LangGraph with a model-backed supervisor, five bounded
branches, and a shared synthesis node. Every supervisor decision is schema-validated and visible in
SSE activity events and LangSmith traces.

| Route | Agent | Available tools | Purpose |
|---|---|---|---|
| `direct_knowledge` | Root | `knowledge_search` | Focused questions answered from authorized evidence |
| `research` | Research specialist | `knowledge_search` | Multi-document investigation and grounded findings |
| `analysis` | Analysis specialist | `knowledge_search`, `structured_analysis` | Bounded aggregation over retrieved evidence |
| `enterprise` | Enterprise-tool specialist | Six approved read-only MCP tools | Ownership, directory, and incident-record lookups |
| `out_of_scope` | Root | None | Explain the assistant's approved capabilities and duties |

Tool visibility is enforced by each agent's `ScopedToolbox`. Execution then passes through the
single `ToolGateway`, which independently enforces registration, RBAC capability, server-owned
context, typed inputs, deadlines, result limits, and audit events. A prompt therefore cannot grant
an agent another tool or broaden retrieval scope.

## Routing and outputs

The supervisor is a LangChain agent with no tools and a strict `RouteDecision` response schema. It
uses the current request and bounded conversation context to select `direct_knowledge`, `research`,
`analysis`, `enterprise`, or `out_of_scope`. The response schema contains only that enum and uses
LangChain's retry-capable tool strategy, avoiding unnecessary model-generated routing metadata.
The graph maps the enum to code-defined conditional edges and generates a safe plan summary for the
selected route; the model cannot create a route, tool, permission, or execution budget. When no
model credential is configured, the local profile uses `DeterministicIntentRouter`, a conservative
keyword-based classifier documented in ADR 008. Hosted deployments use `LLMIntentRouter` instead.
The router emits an `agent_started` event and a `routing_completed` event. Delegated routes
additionally
emit `subagent_started` and `subagent_completed`; direct retrieval emits retrieval start/completion.
All specialist results validate through strict Pydantic contracts before the root converts them to
the frozen API response and citation contracts.

The Phase 6 Analysis specialist uses the separately gateway-enforced controlled analysis tool. It
cannot execute arbitrary Python. The Enterprise specialist similarly reaches only six registered
read-only MCP operations.

The Research specialist owns the compiled, recursive, budgeted Phase 5 LangGraph described in
[research-graph.md](research-graph.md). The root still delegates through the same small static agent
surface; recursion occurs only inside that code-controlled specialist workflow. Its planning node
explicitly attaches the harness `TodoListMiddleware` to a planner whose sole tool is `write_todos`.
The generated todos become bounded `ResearchTask` records; they cannot add tools, filters, budgets,
or permissions.

## Production graph and synthesis

`build_root_orchestrator` builds the only agent runtime used by the API. The outer graph contains
`route`, `direct_knowledge`, `research`, `analysis`, `enterprise`, `out_of_scope`, and `synthesize`
nodes. It is
compiled with the configured checkpointer, so message state is isolated by the server-derived
user/conversation thread ID.

With `OPENAI_API_KEY`, supervisor routing and optional synthesis use provider-backed structured
output. Without it, the local deterministic profile uses a conservative router so Compose can run
without cloud credentials. The model receives only the bounded specialist result and authorized
evidence. Citation resolution and output validation remain deterministic application controls.

Graph `custom` updates are streamed directly to SSE; callback-based transitions remain only as a
compatibility surface for tests and external adapters.
