# Agent Orchestration (Phase 4)

Phase 4 introduces one controlled root orchestrator and exactly three static specialists. The API
uses deterministic intent classification so routing is reproducible, testable, and visible in SSE
activity events and LangSmith traces.

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

## Deep Agents harness

`build_deep_agent_graph` provides the provider-backed Deep Agents integration. Its OpenAI harness
profile excludes built-in filesystem, search, edit, and shell execution tools, disables the default
general-purpose subagent, exposes only gateway-backed tools, and mounts the four focused skills:

- knowledge retrieval
- incident analysis
- enterprise tools
- grounded response

The factory compiles without making a model call. Production invocation still remains behind the
same deterministic platform controls and trusted request context.
