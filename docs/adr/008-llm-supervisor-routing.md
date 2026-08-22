# ADR 008: LLM supervisor routing

- Status: Accepted
- Date: 2026-08-22

## Context

The outer LangGraph originally selected specialist branches with regular-expression keyword rules.
That was reproducible but brittle for ambiguous follow-ups, mixed intents, and requests whose wording
did not contain a known trigger.

## Decision

Use a tool-free LangChain supervisor agent for routing. It receives the current request plus bounded
thread context and must return a validated `RouteDecision` containing only one `AgentRoute`.
LangChain's tool strategy retries malformed structured responses. LangGraph maps the enum to
existing code-controlled branches and the application generates the route's user-safe plan summary.
The `out_of_scope` branch calls no tools and returns a fixed explanation of supported duties for
unrelated, casual, creative, or otherwise unsupported requests.

Production requires a configured model credential and has no deterministic keyword fallback. Tests
and offline contract checks may inject an `AgentRouter` implementation explicitly; dependency
injection is not selected automatically by runtime configuration.

One capability-compatibility invariant is enforced after model classification: an `enterprise`
choice is converted to `research` when the request explicitly names two or more document families.
The enterprise branch performs one system-of-record lookup and cannot satisfy a cross-document
investigation; allowing that choice would silently discard the requested evidence sources. This
guard narrows tool selection and does not grant permissions or provide a general routing fallback.

## Consequences

Routing handles semantic and contextual intent instead of relying on vocabulary. A model outage can
prevent routing, but cannot broaden permissions or invent graph branches. Supervisor calls and the
validated choice are traced, while hidden reasoning is neither requested nor exposed.
