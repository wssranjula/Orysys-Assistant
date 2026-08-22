# Phase 1 Walking Skeleton

## Implemented request path

```text
Streamlit chat
  -> POST /v1/chat/stream
  -> request correlation middleware
  -> temporary traceable mock agent
  -> activity + answer_delta SSE events
  -> validated terminal response
  -> Streamlit answer and activity panels
```

The mock agent deliberately performs no retrieval or tool call. Its warning in every final
response makes that limitation visible rather than implying grounded behavior prematurely.

## SSE lifecycle

A successful request emits request accepted, agent started, and answer streaming activity;
ordered answer deltas; validation and completion activity; then exactly one final response.
Events share the request and conversation IDs. The UI can render activity without parsing answer
text or exposing hidden reasoning.

If failure occurs after streaming headers, the stream emits safe failure activity and a failed
terminal response. Payload validation failures before streaming use HTTP 400 with the common
error envelope. Starlette cancellation plus explicit disconnect checks stop the iterator when
the browser closes the connection.

## Observability

The API logs JSON records with bound request/conversation context, status, duration, and safe
error type fields. It never logs authorization headers or request bodies. With LangSmith enabled,
the mock agent call creates a `phase1-mock-agent` trace tagged `phase-1` and
`walking-skeleton`, with request and conversation IDs in trace metadata.

## Deferred by design

Authentication, real persistence, retrieval, agents, tools, and external dependency readiness
are later-phase work. The conversation and feedback endpoints return typed placeholders that
state persistence is unavailable; they do not pretend data was stored.

