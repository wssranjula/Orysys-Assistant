# Phase 1 Walking Skeleton (Historical)

> This document records the initial milestone and is not the current runtime description. The mock
> agent and placeholder persistence described below were removed in later phases. For the current
> implementation, see [architecture.md](architecture.md), [contracts.md](contracts.md), and
> [agents.md](agents.md).

## Implemented request path

```text
Streamlit chat
  -> POST /v1/chat/stream
  -> request correlation middleware
  -> deterministic or model-backed root agent
  -> activity + answer_delta SSE events
  -> validated terminal response
  -> Streamlit answer and activity panels
```

At Phase 1 this path used a temporary mock agent with no retrieval or tool calls. The current path
uses authorized retrieval, scoped tools, output validation, and persisted conversation state.

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

The current API logs JSON records with bound request/conversation context, status, duration, and
safe error type fields. It never logs authorization headers or request bodies. LangSmith tracing is
optional and traces the current router, tools, retrieval, and graph nodes rather than a mock agent.

## Deferred by design

Authentication, durable conversation state, retrieval, scoped tools, approvals, and readiness were
delivered in later phases. Feedback remains the only intentionally acknowledged-but-unpersisted API
surface.
