# ADR 003: Deterministic security controls outside agents

- Status: Accepted
- Date: 2026-08-22

## Context

Model instructions are probabilistic and retrieved documents may contain malicious text. Agent
prompts cannot be the authority for RBAC, document filters, or tool execution.

## Decision

Authentication, authorization, trusted retrieval filters, tool allowlists, parameter schemas,
rate limits, budgets, timeouts, conversation ownership, evidence ledgers, and output validation
are regular Python services. Every tool call uses one gateway. Retrieved text is treated as
untrusted data and cannot introduce instructions or tools.

## Consequences

Security rules can be unit-tested and cannot be weakened by prompt injection. The agent may
request an operation and receive a denial, but never receives policy-changing capabilities.
This requires explicit service boundaries and typed request context.

