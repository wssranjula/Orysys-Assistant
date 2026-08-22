# ADR 006: Expand the Phase 3 corpus to six document types

- Status: Accepted
- Date: 2026-08-22

## Context

Phase 0 initially froze four document types, while the approved Phase 3 plan explicitly requires
policies, architecture, runbooks, incidents, product specifications, and meeting notes. The latter
two are important for cross-document reasoning and evaluation.

## Decision

Expand the supported POC corpus types with `product_specification` and `meeting_note`. Preserve
the existing access-level model, trusted filters, namespace, and tool permissions. All six types
use the same validated metadata, deterministic IDs, chunking, attribution, and retrieval path.

## Consequences

The corpus can test relationships between requirements, operational records, and decisions
without adding a new security boundary. Retrieval filters and evaluation datasets must recognize
the two additional enum-like values.

