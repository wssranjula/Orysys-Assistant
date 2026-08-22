# API, Event, Response, and Error Contracts

The canonical executable schemas live in `src/orysys_assistant/domain`. This document freezes
their public meaning for the POC.

## Authentication and identity

Clients send `Authorization: Bearer <token>`. Identity, role, namespace, department, and access
scope are resolved server-side. These fields are forbidden in chat and tool payloads.

## Endpoints planned for the Phase 1 surface

| Method | Path | Success | Purpose |
|---|---|---|---|
| `POST` | `/v1/chat/stream` | `200 text/event-stream` | stream activity, answer deltas, and final response |
| `GET` | `/v1/conversations/{id}` | `200 application/json` | load an owned conversation |
| `POST` | `/v1/feedback` | `202 application/json` | attach rating to an owned response |
| `GET` | `/health/live` | `200 application/json` | process liveness only |
| `GET` | `/health/ready` | `200` or `503` | required dependency readiness |

`POST /v1/chat/stream` accepts:

```json
{"message":"What is the remote-work policy?","conversation_id":"optional-uuid"}
```

`message` is trimmed, 1–8,000 characters, and must contain text. If `conversation_id` is absent,
the server creates one. The server creates `request_id`; clients cannot choose it.

## SSE stream

Each event has one of three names:

- `activity`: a safe operational event such as retrieval started, tool denied, or validation
  passed.
- `answer_delta`: an ordered text fragment with `sequence` and `text`.
- `final`: exactly one terminal `FinalResponse`, after which the server closes the stream.

On a request-level failure before streaming starts, use the HTTP error contract. After headers
are sent, emit a terminal `request_failed` activity followed by a `final` response with status
`failed`; do not switch the HTTP status mid-stream.

Activity metadata is allowlisted. It may include counts, durations, evidence IDs, tool names,
and degradation flags, but not prompts, hidden reasoning, secrets, or full sensitive content.

## Final response

```json
{
  "request_id": "uuid",
  "conversation_id": "uuid",
  "status": "complete",
  "answer": "The policy allows ... [1]",
  "citations": [{
    "citation_id": "1",
    "evidence_id": "ev_123",
    "document_id": "policy-001",
    "title": "Flexible Work Policy",
    "chunk_id": "policy-001#3",
    "source_path": "policies/flexible-work.md"
  }],
  "warnings": [],
  "degraded": false
}
```

Allowed status values are `complete`, `partial`, `insufficient_evidence`, and `failed`. Every
citation must reference evidence retrieved within this request and authorized for this user.
The UI renders citations by `citation_id`; unresolvable citations are forbidden.

## HTTP error envelope

```json
{
  "error": {
    "code": "rate_limit_exceeded",
    "message": "Request limit exceeded. Try again later.",
    "request_id": "uuid",
    "retryable": true,
    "details": {}
  }
}
```

| Condition | HTTP | Stable code | Retryable |
|---|---:|---|---:|
| invalid request | 400 | `invalid_request` | no |
| missing/invalid token | 401 | `authentication_failed` | no |
| forbidden resource/tool | 403 | `authorization_denied` | no |
| unknown owned resource | 404 | `not_found` | no |
| rate limit exhausted | 429 | `rate_limit_exceeded` | yes |
| retrieval unavailable | 503 | `retrieval_unavailable` | yes |
| model unavailable | 503 | `model_unavailable` | yes |
| request/tool deadline | 504 | `execution_timeout` | yes |
| unexpected internal error | 500 | `internal_error` | maybe |

HTTP `429` includes `Retry-After`. Details are allowlisted validation fields only. Stack traces,
provider payloads, credentials, document content, and policy internals never leave the API.

## Graceful degradation

| Failure | Contracted behavior |
|---|---|
| sparse retrieval fails | use dense-only; set `degraded` and warning |
| Pinecone unavailable | no answer fabrication; retrieval error or insufficient evidence |
| one research worker fails | return safe partial evidence and warning if useful |
| MCP timeout | continue without it when possible; warn |
| invalid citation | repair once, then return insufficient evidence |
| unauthorized tool | emit denied activity; continue only if a safe path exists |
| client disconnect | cancel graph, retrieval, and tool tasks |

