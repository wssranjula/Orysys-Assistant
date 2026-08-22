---
fixture_id: spec-instant-payment-continuity-001
title: Instant Payment Continuity Acceptance Specification
document_type: product_specification
department: payments
access_level: confidential
created_date: 2026-01-09
---

# Instant Payment Continuity Acceptance Specification

## Purpose and scope

Regional continuity acceptance requires authorization restoration within five minutes, settlement recovery within fifteen minutes, and zero duplicate financial outcomes.

## Operational detail

Tests must preserve one idempotency key through client, gateway, and service retries; exercise producer and consumer version skew; include API, settlement, reconciliation, and replay connection demand; and validate telemetry from the recovery region.

## Controls and response

A test passes only when ledger reconciliation balances, lag remains below five minutes for thirty minutes, and no critical alert is muted or sourced from one region only.

## Related evidence

See Instant Payments Multi-Region Continuity Architecture and incidents PAY-1224, PAY-1241, PAY-1260, and PAY-1288.
