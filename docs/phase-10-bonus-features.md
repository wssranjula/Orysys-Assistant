# Phase 10 bonus features

## Multi-agent collaboration and failure containment

The research supervisor owns the canonical state. Workers receive isolated task snapshots and
return typed `ResearchTaskResult` values; they do not mutate shared state. The reducer is the only
node that merges evidence, findings, warnings, and tool-call counts. One failed worker becomes a
contained result while successful siblings continue. If every worker in a round fails, the graph
opens a failure circuit and finalizes a partial response instead of recursively creating more work
against a likely shared dependency failure.

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
conversation messages and LangGraph checkpoints. The root supervisor receives them as a distinct
context block on every turn.
