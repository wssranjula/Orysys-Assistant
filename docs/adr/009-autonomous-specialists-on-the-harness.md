# ADR 009: Autonomous specialists on the agent harness

- Status: Accepted; extended by ADR 010
- Date: 2026-08-22
- Supersedes the research-subgraph half of ADR 001 and the deterministic-router fallback in ADR 008

## Context

Specialists were deterministic procedures wearing agent names. The knowledge path issued one
hard-coded `knowledge_search` with code-derived filters, the analysis path picked its aggregation by
keyword, the enterprise path selected its tool by regular expression, and research ran a hand-built
LangGraph of `normalize_scope`, `planner`, `workers`, `reducer`, `coverage_check`, and
`followup_planner` nodes with `Send` fan-out.

That worked, but the adaptive behaviour the assignment asks for lived in our control flow rather
than in the agents, and roughly nine hundred lines reimplemented planning, fan-out, reduction, and
context management that the harness already ships. Recursion in particular was a fixed-depth
follow-up loop rather than an agent re-planning against what it had found.

## Decision

Make each specialist an autonomous tool-calling loop and delete the machinery the harness replaces.

Knowledge, analysis, and enterprise use `create_agent`. Research uses `create_deep_agent` with
`TodoListMiddleware` for planning, `SummarizationMiddleware` for context, the virtual filesystem for
scratch space, and native parallel tool calls for fan-out. `research_graph.py` and
`research_planner.py` are removed.

The model gains genuine latitude: which tool, which arguments, which filters, how many searches in
parallel, and when to stop. The boundary stays in code and is unchanged in strength:

- Tools are published from `ScopedToolbox` and still execute through the central `ToolGateway`.
- `TrustedRequestContext` is injected via LangGraph runtime context, so identity and access scope can
  never be model-authored arguments.
- Budgets are `ToolCallLimitMiddleware` and `ModelCallLimitMiddleware`, not prompt instructions.
- A `SpecialistCollector` records executed calls, and evidence, citations, and analysis figures are
  rebuilt from that record rather than from model prose.
- Research findings citing evidence identifiers that were never retrieved are dropped before
  citation resolution.
- Authorization denials end the turn; every other tool fault degrades that one call.

Because every specialist is now model-driven, the keyword-based `DeterministicIntentRouter` and the
credential-free local profile are removed. `build_root_orchestrator` raises `InvalidRequestError`
when no model is configured, and tests and evaluation inject a scripted chat model explicitly.

## Consequences

Adaptive planning, parallelism, re-planning, and context management come from a maintained harness
instead of from our orchestration code, and the recursive-language-model pattern is now recursion in
the agent rather than a loop counter in a graph. Behaviour is less predictable per run, so tests
script the model's turns to keep assertions deterministic while still exercising the real loop, real
middleware, and real gateway.

The cost is a hard dependency on a chat model for every route, including offline evaluation, and one
weaker guarantee at the edges: a model that writes prose citing only some of the evidence it was
shown now has the remaining sources appended by the output validator's single repair pass instead of
being emitted by a deterministic summary. Failing closed on an unresolvable citation is unchanged.

ADR 010 applies the same treatment to the root, replacing the classifier, the hand-off table, and the
synthesis node with a delegation-tool loop. The specialist contracts described here are unchanged
except that all four now return one `SpecialistOutcome` — report, evidence, warnings, and whether the
consultation was grounded — instead of four separate execution models.
