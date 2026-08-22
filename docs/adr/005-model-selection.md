# ADR 005: Provider adapters with OpenAI as the initial provider

- Status: Accepted
- Date: 2026-08-22

## Context

The POC needs capable tool use, streaming, structured output, embeddings, and LangSmith tracing,
without coupling domain logic to a single SDK.

## Decision

Use OpenAI initially for chat and embeddings. Configure model names through the environment and
hide provider calls behind chat and embedding adapters. Use a stronger configurable model for
root/reducer work and permit a smaller model for classification or workers after correctness is
measured. Do not implement provider failover in the POC.

## Consequences

The fastest implementation path retains a later migration seam. Provider-specific behavior
still needs adapter contract tests, and a provider outage produces the documented clean failure
instead of silent model substitution.

