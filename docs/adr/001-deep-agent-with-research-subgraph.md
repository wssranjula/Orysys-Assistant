# ADR 001: Deep Agent with a bounded research subgraph

- Status: Superseded by ADR 007
- Date: 2026-08-22

## Context

The assessment requires specialized agents, LangGraph, and a recursive language-model pattern,
while enterprise controls must remain explainable and testable.

## Decision

Use a Deep Agent as the root planning/delegation harness with static Research, Analysis, and
Enterprise Tool subagents. Implement complex research as a separately compiled LangGraph
subgraph with explicit plan, retrieve, bounded fan-out, reduce, and coverage nodes. Code
enforces all recursion, concurrency, tool-call, and timeout budgets.

## Consequences

The topology and failure boundaries are observable and testable, and worker contexts stay
small. There is additional integration code, and the root agent cannot dynamically create
unrestricted agents or tools.
