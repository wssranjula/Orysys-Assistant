# Phase 10 bonus features

## Multi-agent collaboration and failure containment

Containment lives at the tool boundary rather than in a reducer node. A failing retrieval is caught
in the gateway tool wrapper, recorded as a degraded invocation with a warning, and returned to the
model as an ordinary result, so its parallel siblings still contribute their evidence and the run
continues. Only an authorization denial propagates, because a denial is a policy verdict rather than
a fault to work around.

Nothing a specialist writes mutates shared state. The per-request `SpecialistCollector` is the only
thing that merges evidence, warnings, and executed calls, and the orchestrator builds the typed
result from that record. When every retrieval in a run fails — the likely shared-dependency case —
the middleware budget and the overall deadline stop the loop and the turn finishes as an honest
partial answer with warnings instead of recursively generating more work against a broken backend.

## Human approval

`POST /v1/approvals` creates a pending request for the dummy `modify_incident` administrative tool.
No write occurs while pending. A different administrator explicitly resumes the approval graph with
`POST /v1/approvals/{approval_id}/decision`; self-approval is denied. Approval records are stored
in PostgreSQL in the Compose profile. Approval executes once; rejection has no side effect;
duplicate decisions are rejected. The write tool has schema validation, RBAC, audit logging, a
timeout, and zero automatic retries to avoid duplicating an uncertain side effect.

## Reranking

Authorized dense and sparse retrieval still creates at most 20 hybrid candidates. A provider-neutral
reranker then blends first-stage relevance with exact token and identifier coverage before returning
the final requested top K (normally six). Reranking cannot introduce a document outside the
authorization-filtered candidate ledger. The golden retrieval test compares reranked recall with the
first-stage baseline and rejects regressions.

## Long-term memory

`PUT /v1/memory/preferences/{key}` stores only an explicitly consented preference (`explicit: true`).
`GET` lists the current user's preferences and `DELETE` forgets one. Preferences are keyed by user,
are never shared between owners, and live in a separate in-memory map or PostgreSQL table from
conversation messages and LangGraph checkpoints. The root agent receives them as a distinct context
block on every turn.
