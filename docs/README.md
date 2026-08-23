# Documentation index

This folder is the authoritative reference for the Commercial Bank AI Assistant POC. The
[README](../README.md) is the entry point for setup, credentials, and verification commands.

## Start here

| Document | Purpose |
|---|---|
| [architecture.md](architecture.md) | System context, trust boundaries, runtime ownership |
| [scope-and-assumptions.md](scope-and-assumptions.md) | Frozen POC boundaries, roles, tools, and corpus rules |
| [contracts.md](contracts.md) | HTTP, SSE, response, citation, and error contracts |
| [deployment.md](deployment.md) | Compose startup, Pinecone mode, operations, and delivery checks |

## Feature areas

| Document | Purpose |
|---|---|
| [agents.md](agents.md) | Root LangGraph, supervisor routing, and specialist delegation |
| [research-graph.md](research-graph.md) | Bounded recursive research workflow |
| [retrieval.md](retrieval.md) | Corpus, ingestion, hybrid dense + BM25 retrieval, reranking |
| [memory-and-tools.md](memory-and-tools.md) | Conversation memory, preferences, analysis, and MCP tools |
| [security.md](security.md) | Identity, authorization, tool gateway, and rate limiting |
| [guardrails-and-degradation.md](guardrails-and-degradation.md) | Input/output validation, evidence ledger, and failure handling |
| [observability-and-evaluation.md](observability-and-evaluation.md) | Activity panel, trace correlation, and golden evaluation |
| [phase-10-bonus-features.md](phase-10-bonus-features.md) | Reranking, preferences, failure circuit breaking, and approvals |

## Assessment and delivery

| Document | Purpose |
|---|---|
| [demo-script.md](demo-script.md) | 15–20 minute evaluator walkthrough |
| [role-capability-question-set.md](role-capability-question-set.md) | Manual role, capability, and failure test matrix |
| [assumptions-and-tradeoffs.md](assumptions-and-tradeoffs.md) | Known limitations and production follow-ups |

## Architecture decisions

| ADR | Topic |
|---|---|
| [001](adr/001-deep-agent-with-research-subgraph.md) | Deep agent with research subgraph |
| [002](adr/002-hybrid-retrieval-strategy.md) | Hybrid retrieval strategy |
| [003](adr/003-security-outside-agent.md) | Security outside the agent |
| [004](adr/004-session-memory-design.md) | Session memory design |
| [005](adr/005-model-selection.md) | Model selection |
| [006](adr/006-expand-phase-3-corpus-types.md) | Six document types in the corpus |
| [007](adr/007-single-production-langgraph.md) | Single production LangGraph |
| [008](adr/008-llm-supervisor-routing.md) | LLM supervisor routing |

## Historical note

[phase-1-walking-skeleton.md](phase-1-walking-skeleton.md) records the original walking skeleton
only. It is not a description of the current runtime.
