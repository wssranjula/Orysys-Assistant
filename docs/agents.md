# Agent Orchestration

The production runtime is one agent whose entire tool surface is delegation. The root model decides
which specialists a request needs, what to ask each one, whether the first answer was enough, and how
to write the final response. Each specialist is itself an autonomous tool-calling loop that decides
which approved tool to call and with what arguments. Every decision is visible in SSE activity events
and LangSmith traces.

| Delegation tool | Specialist | Available tools | Purpose |
|---|---|---|---|
| `consult_knowledge_specialist` | Knowledge specialist | `knowledge_search` | Focused questions answered from authorized evidence |
| `consult_research_specialist` | Research specialist | `knowledge_search`, `write_todos`, virtual filesystem | Multi-document investigation and grounded findings |
| `consult_analysis_specialist` | Analysis specialist | `knowledge_search`, `structured_analysis` | Bounded aggregation over retrieved evidence |
| `consult_enterprise_specialist` | Enterprise-tool specialist | Six approved read-only MCP tools | Ownership, directory, and incident-record lookups |

The root also holds `write_todos` for task decomposition. It holds nothing else: no retrieval, no
records, no filesystem, no shell. A request that the root answers without consulting anyone is
returned as the fixed capabilities response, because a claim drawn from the model's own parameters
has no evidence ledger behind it and nothing downstream could catch it.

## Autonomy inside a fixed boundary

Specialists are tool-calling loops built on the harness: `create_agent` for knowledge, analysis, and
enterprise, and `create_deep_agent` for research. The root is `create_agent` with delegation tools
and `TodoListMiddleware`. Models choose the query, the filters, how many searches to run in parallel,
which specialist to consult, and when they have enough. They do not choose the boundary.

- **Identity is injected, never authored.** The `TrustedRequestContext` reaches tools through
  LangGraph runtime context, so role and access scope cannot appear as model-written arguments.
- **Tool visibility** comes from each agent's `ScopedToolbox`; execution then passes through the
  single `ToolGateway`, which independently enforces registration, RBAC capability, typed inputs,
  deadlines, result limits, and audit events. A prompt cannot grant an agent another tool or broaden
  retrieval scope. The root's own autonomy is bounded by *which specialist it consults*, since it has
  no capability of its own.
- **Budgets are middleware.** `ToolCallLimitMiddleware` and `ModelCallLimitMiddleware` cap each run.
  A per-tool limit lets each specialist be consulted at most once per turn, so a confused root costs
  one blocked call rather than its whole budget. A budget stated in a prompt is a request; a budget
  stated here is a fact.
- **Results are rebuilt from observed traffic.** A `SpecialistCollector` records what actually
  executed, and a `DelegationLedger` accumulates it across consultations. Evidence, citations, route,
  and status come from the tool results rather than from model prose. The model narrates; it does not
  supply the numbers, the sources, or its own success report.

Failure handling at the tool boundary is asymmetric on purpose. An authorization denial ends the
turn. Any other fault is contained and returned to the model as a degraded tool result with a
warning, so one broken dependency costs a single call instead of the whole request, and the
specialist can try a different approach.

## Routing, hand-off, and reported provenance

There is no classifier. Routing is the root choosing a delegation tool, which means the choice is
made with the full question and conversation in context rather than from wording alone, and it can be
revised after seeing what a specialist returned.

The reported `route` is derived from the delegations that actually ran, never from model prose:

- no consultation at all becomes `out_of_scope`;
- otherwise the first consultation that contributed evidence names the route;
- if none contributed evidence, the last consultation names it.

Preferring a consultation that produced evidence is what keeps grounding validation reachable: a turn
holding document evidence can never report `enterprise` or `out_of_scope`, the two routes the output
validator allows to skip the citation-ledger check.

Status follows the same rule. A turn where no consultation was grounded is `insufficient_evidence`; a
turn carrying warnings or a consultation that came back empty is `partial`; only a turn where every
consulted specialist delivered is `complete`. Consulting two specialists is therefore not penalised
when both succeed, which is the case a fixed hand-off table could not express.

The root emits `agent_started`, then `routing_completed` for each delegation. A consultation that
follows an empty one is reported as `handoff_completed` with the originating route, which is the
recovery the old graph encoded as a fixed edge. Delegated work adds `subagent_started` and
`subagent_completed`, each tool call adds `tool_started` and `tool_completed` or `tool_denied`, and
both the root and the research specialist republish their todo lists as `research_node_completed`.

## Production agent and answer streaming

`build_root_orchestrator` builds the only agent runtime used by the API, compiled with the configured
checkpointer so message state is isolated by the server-derived user/conversation thread ID.

The root writes the final answer itself, so there is no separate synthesis pass. Answer tokens are
streamed as they are generated, filtered to the root's own model node — a specialist loop runs inside
a delegation tool, so its intermediate prose is on the same message stream and would otherwise be
shown to the user. Streamed tokens are marked provisional; citation resolution and output validation
remain deterministic application controls and the validated response stays authoritative.

Citation markers are assigned by the ledger. Each specialist reply lists the markers its evidence
earned, so the number the root is told to write is the number the returned citation carries, and a
second consultation cannot renumber the first one's evidence.

Every loop is model-driven, so `OPENAI_API_KEY` is required; construction fails with a clear error
rather than silently falling back to a keyword classifier that would answer with a different system
than the one the deployment is configured for. Tests and evaluation runs inject a chat model directly
through `AgentDependencies.model`.

Graph `custom` updates are streamed directly to SSE; callback-based transitions remain only as a
compatibility surface for tests and external adapters.
