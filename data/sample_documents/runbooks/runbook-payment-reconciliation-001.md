---
fixture_id: runbook-payment-reconciliation-001
title: Duplicate Payment and Settlement Reconciliation Runbook
document_type: runbook
department: payments
access_level: confidential
created_date: 2026-03-04
---

# Duplicate Payment and Settlement Reconciliation Runbook

## Purpose and scope

Use this runbook when duplicate authorizations, missing settlement events, or recovery-region backlog is detected.

## Operational detail

Fence automated replay, group records by original idempotency key, compare authorization and settlement ledgers, quarantine incompatible events, and replay only records with a proven missing terminal state. Customer adjustments require dual approval.

## Controls and response

Recovery is complete only when duplicate exposure is zero, consumer lag remains below five minutes for thirty minutes, ledger reconciliation balances, and customer-notification tasks are queued.

## Related evidence

See PAY-1224, PAY-1241, PAY-1260, and the Instant Payment Continuity Specification.
