# Bounded Research Graph (Phase 5)

The Research specialist executes complex, multi-document questions through one reusable compiled
LangGraph. It does not create autonomous agents or expose new tools. Every retrieval worker uses the
Research specialist's `knowledge_search` toolbox, which then crosses the central Tool Gateway and its
trusted access-scope enforcement.

```text
START → normalize_scope → planner → workers → Send(worker × N) → reducer → coverage_check
                                                                         ├─ sufficient/limited → finalize → END
                                                                         └─ gap + budget → followup_planner → workers
```

## Nodes

- `normalize_scope` removes redundant whitespace and derives only explicit safe filters.
- `planner` creates at most four independent tasks. Annual incident questions are partitioned by
  quarter; other questions are partitioned by evidence type.
- `workers` emits LangGraph `Send` commands for native map/reduce fan-out. Each worker has its own
  timeout and returns a strict reducer update, including a safe failure warning when retrieval fails.
- `reducer` deduplicates evidence by evidence ID and findings by normalized claim, preserving all
  supporting evidence IDs.
- `coverage_check` assesses unique evidence and successful task coverage.
- `followup_planner` creates no more than two targeted corroboration tasks per round.
- `finalize` marks incomplete coverage as partial and retains unresolved questions and warnings.

## Enforced defaults

| Limit | Default |
|---|---:|
| Initial tasks | 4 |
| Follow-up tasks per round | 2 |
| Follow-up recursion depth | 2 |
| Concurrent workers | 3 |
| Total retrieval calls | 20 |
| Evidence records per worker | 6 |
| Worker timeout | 25 seconds |
| Overall timeout | 90 seconds |

All limits are validated application settings and can be lowered through the corresponding
`RESEARCH_*` environment variables. Prompts and model output cannot raise them.

## Observability and failure behavior

The API streams `research_node_started` and `research_node_completed` activity events for planners,
workers, reduction, coverage, follow-up, and finalization. LangGraph traces every graph node, and each
worker also has explicit LangSmith trace metadata. Client cancellation propagates through graph
fan-out. One failed worker does not cancel successful siblings; overall deadline or exhausted budgets
return a structured partial result rather than starting unbounded work.
