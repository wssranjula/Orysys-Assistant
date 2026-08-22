# Guardrails, Citation Validation, and Graceful Degradation

Phase 7 adds deterministic safety controls around the agent runtime. Prompts still help agents
behave well, but identity, scope, tool permissions, evidence trust, deadlines, and response
validation remain code-enforced boundaries.

## Request and tool boundary

`ChatRequest` rejects extra client fields, including attempted role, namespace, or access-level
injection. Pydantic enforces message size and UUID shape; `InputGuard` additionally rejects
unsupported control characters. These checks run before conversation creation and agent execution.

Every tool call still passes through the central gateway. Authorization and parameter validation
are performed once and are never retried. Read-only MCP calls receive one bounded retry only after
a timeout; knowledge retrieval receives two bounded retries for temporary dependency failures.
The request stream has a 120-second overall deadline.

## Untrusted retrieved content

`RetrievedContentGuard` wraps every retrieved chunk in `<retrieved_evidence>` delimiters and labels
it as evidence only. Common instruction-like fragments are replaced before the content reaches an
agent. Metadata records both the untrusted evidence boundary and whether a suspicious pattern was
found. The Tool Gateway remains authoritative even when detection misses a novel attack.

## Evidence ledger and output validation

Knowledge, research, and analysis results carry a request-local `Evidence` ledger internally.
Before memory persistence or token streaming, `OutputValidator` verifies that:

- the answer is non-empty and does not expose protected reasoning or instruction text;
- every evidence reference exists in the current ledger;
- every ledger entry is authorized for the request's access scope;
- citation metadata exactly matches its evidence record;
- citation identifiers are unique and every inline marker resolves;
- all grounded evidence has a citation.

The validator permits one repair attempt. It can deterministically add omitted citation markers.
Unknown evidence IDs or metadata mismatches cannot be repaired locally, so revalidation fails
closed to `insufficient_evidence`, removes citations and evidence references, and never streams the
unsupported draft.

## Brand and response policy

Responses use professional language, state uncertainty, and must not invent company policy or
claim an enterprise action without a successful tool result. Account-specific actions and
financial decisions remain out of scope. Restricted evidence, confidential employee data outside
role permissions, system prompts, hidden reasoning, credentials, and raw tool payloads must not be
exposed.

## Failure behavior

| Failure | Result |
|---|---|
| invalid input | HTTP 400 before agent invocation |
| sparse retrieval failure | dense-only evidence, explicit warning, `partial` |
| retrieval unavailable | no fabricated answer; `insufficient_evidence` |
| MCP timeout | retry once, then authorized-document fallback and warning |
| research worker failure | successful findings with `partial` status |
| invalid citation | one repair/revalidation, then `insufficient_evidence` |
| overall timeout | tasks cancelled; safe terminal degraded response |
| client disconnect | graph, subagents, and tools cancelled; no terminal event |

Validation activity emits started/completed or failed/degraded events. Metadata contains only safe
status and counts; it never includes prompts, document text, secrets, or hidden reasoning.
