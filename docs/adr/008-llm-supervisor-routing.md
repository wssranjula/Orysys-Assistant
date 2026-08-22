# ADR 008: LLM supervisor routing

- Status: Accepted
- Date: 2026-08-22

## Context

The outer LangGraph originally selected specialist branches with regular-expression keyword rules.
That was reproducible but brittle for ambiguous follow-ups, mixed intents, and requests whose wording
did not contain a known trigger.

## Decision

Use a tool-free LangChain supervisor agent for routing. It receives the current request plus bounded
thread context and must return a validated `RouteDecision` containing one `AgentRoute`, confidence,
and a short user-safe summary. LangGraph maps the enum to existing code-controlled branches. The
`out_of_scope` branch calls no tools and returns a fixed explanation of supported duties for
unrelated, casual, creative, or otherwise unsupported requests.

Production requires a configured model credential and has no deterministic keyword fallback. Tests
and offline contract checks may inject an `AgentRouter` implementation explicitly; dependency
injection is not selected automatically by runtime configuration.

## Consequences

Routing handles semantic and contextual intent instead of relying on vocabulary. A model outage can
prevent routing, but cannot broaden permissions or invent graph branches. Supervisor calls and the
validated choice are traced, while hidden reasoning is neither requested nor exposed.
