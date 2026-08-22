# ADR 004: User-owned session memory in PostgreSQL

- Status: Accepted
- Date: 2026-08-22

## Context

Multi-turn questions require conversation context, while cross-user leakage and unbounded prompt
growth are unacceptable.

## Decision

Persist LangGraph-compatible checkpoints and conversation records in PostgreSQL, keyed by
trusted user ID and conversation ID. Retain recent messages, compact summaries, evidence
references, and safe execution metadata. Verify ownership on every access. Keep rate-limit
state in Redis, not memory records. Exclude long-term personalization from the initial POC.

## Consequences

Sessions survive API restarts and can scale across instances. Summarization introduces possible
information loss and requires tests. Data retention and deletion policy remain production
follow-ups; hidden reasoning and secrets are never persisted.

