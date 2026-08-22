# ADR 007: One production LangGraph

- Status: Accepted
- Date: 2026-08-22

## Context

The codebase contained a deterministic orchestrator used by the API and a separate Deep Agents
graph used only by compilation tests. Tool schemas, memory, streaming, and specialist definitions
could drift between the two implementations.

## Decision

Use one compiled outer LangGraph for production and tests. It routes to bounded direct, research,
analysis, and enterprise branches, then converges on a shared synthesis node. The research subgraph
uses LangGraph `Send` fan-out and reducer state. The root checkpointer owns execution-time message
history, and FastAPI consumes native graph custom/update streams.

Optional provider-backed prose synthesis uses a LangChain agent with structured output. The central
tool gateway, trusted request context, retrieval filtering, citation resolution, and output
validation remain deterministic application controls.

## Consequences

There is one observable runtime topology and one set of specialist contracts. Offline evaluation
remains deterministic, while configured deployments perform real model-backed synthesis. The
conversation repository remains a compact owner-isolated API read model alongside LangGraph's
execution checkpoints.
