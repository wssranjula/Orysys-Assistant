---
fixture_id: arch-payment-gateway-001
title: Payment Gateway Architecture
document_type: architecture
department: payments
access_level: confidential
created_date: 2025-01-20
---

# Payment Gateway Architecture

## Purpose and scope

The Payment Gateway validates, routes, and records card and account-to-account payment requests.

## Operational detail

Stateless API pods call the authorization service through service DNS, use a bounded PostgreSQL connection pool, and publish outcomes to the payment-events stream. Idempotency keys are retained for twenty-four hours.

## Controls and response

Each pod permits 40 database connections; autoscaling must respect the database-wide ceiling. Circuit breakers open after sustained downstream failure and retry only safe idempotent operations.

## Related evidence

See Payments Resilience Specification, Payment Outage Runbook, and incidents PAY-1042 and PAY-1077.
