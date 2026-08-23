# Recursive Research Specialist

The Research specialist answers questions that no single document settles. It is a Deep Agents
harness instance — `create_deep_agent` — rather than a hand-built graph of planner, worker, reducer,
and coverage nodes. Recursion is the agent re-planning against what it has already found, which is
what the recursive-language-model brief asks for and what a fixed-depth loop could only approximate.

```text
objective → write_todos (plan) → parallel knowledge_search calls → re-plan on results
          → summarize when context grows → report → grounding check → ResearchExecution
```

## What the harness provides

| Capability | Source | Replaces |
|---|---|---|
| Task decomposition and live plan state | `TodoListMiddleware` (`write_todos`) | `planner` and `followup_planner` nodes |
| Parallel retrieval | Native parallel tool calls | `Send` fan-out and the `workers` node |
| Context management | `SummarizationMiddleware` | Per-worker context isolation |
| Scratch space for long passages | Deep Agents virtual filesystem (in-state, never on disk) | — |
| Tool and model call ceilings | `ToolCallLimitMiddleware`, `ModelCallLimitMiddleware` | Hand-counted budgets |

Re-planning replaces the `coverage_check` → `followup_planner` edge. When a search returns nothing,
the agent widens it; when a result raises a new question, it adds a todo. It stops when the evidence
supports an answer or when further searching stops producing new evidence.

## What stays deterministic

The agent chooses queries, filters, and how many rounds to run. It does not choose the boundary.

- **Tool surface.** `ScopedToolbox` publishes only `knowledge_search`. Every call still crosses the
  central `ToolGateway`, which re-enforces registration, RBAC capability, typed input, deadlines,
  result size, and audit logging against the server-owned `TrustedRequestContext`.
- **Budgets.** Limits are middleware, not prompt guidance, so they hold regardless of what the model
  decides to do next. The overall deadline is an `asyncio.timeout` around the whole run.
- **Evidence.** Citations are rebuilt from the retrievals that actually executed, recorded by the
  `SpecialistCollector`. Nothing the model writes can add an evidence record.
- **Grounding.** Each reported finding must cite evidence identifiers that `knowledge_search`
  actually returned. A finding citing an identifier the model invented is dropped before it can
  become a citation.

## Enforced defaults

| Limit | Setting | Default |
|---|---|---:|
| Total tool calls | `RESEARCH_MAX_TOTAL_TOOL_CALLS` | 20 |
| Model calls | `RESEARCH_MAX_MODEL_CALLS` | 12 |
| Evidence records per search | `RESEARCH_MAX_CHUNKS_PER_WORKER` | 6 |
| Summarization trigger | `RESEARCH_SUMMARIZATION_TOKEN_TRIGGER` | 40,000 tokens |
| Overall timeout | `RESEARCH_OVERALL_TIMEOUT_SECONDS` | 90 seconds |

All limits are validated application settings. Prompts and model output cannot raise them.

## Observability and failure behavior

`write_todos` calls are intercepted by a `wrap_tool_call` middleware and republished as
`research_node_completed` activity events, so the plan the evaluator sees in the UI is the agent's
own live todo state rather than a separate narration that could describe work it never did. Each
retrieval emits `tool_started` and `tool_completed`, carrying the filters the model chose along with
candidate and selected evidence counts.

Failure handling is deliberately asymmetric at the tool boundary. An authorization denial is a
policy verdict and ends the turn rather than inviting the model to probe for a tool that will
answer. Every other failure is contained and returned as an ordinary degraded tool result, so one
broken dependency costs that call rather than the whole investigation and its sibling searches still
contribute their evidence. Reaching the overall deadline returns whatever was retrieved before the
cutoff as a partial result. Client cancellation propagates through the agent's tasks.

## Hard research corpus

The Project Orion storyline spans twelve records across incidents, meeting notes, runbooks,
architecture, policy, and specification evidence. It intentionally includes an initial incident
hypothesis later superseded by the post-incident finding, controls described as complete for only
some workload classes, and action items whose status changes over time.

Use the eight prompts in `data/hard_research_questions.json` to exercise planning, parallel
retrieval, evidence reduction, temporal reconciliation, and citation coverage. Re-planning is
evidence-driven: it runs when a pass leaves the objective unresolved, not merely because a prompt is
long.
