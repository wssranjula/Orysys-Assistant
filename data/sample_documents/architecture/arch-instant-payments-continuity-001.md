---
fixture_id: arch-instant-payments-continuity-001
title: Instant Payments Multi-Region Continuity Architecture
document_type: architecture
department: payments
access_level: confidential
created_date: 2026-01-08
---

# Instant Payments Multi-Region Continuity Architecture

## Purpose and scope

Project Orion provides active-passive continuity for instant-payment authorization and settlement services across two regions.

## Operational detail

The edge gateway must preserve the original idempotency key and payment correlation ID during regional failover. Authorization may resume before asynchronous settlement, but the settlement consumer must drain its recovery-region backlog before the platform is declared fully recovered.

## Controls and response

Recovery objectives are five minutes for authorization and fifteen minutes for settlement. Synthetic probes, duplicate-payment counters, consumer lag, and recovery-region traces are mandatory release signals.

## Related evidence

See Instant Payment Continuity Specification, Regional Payment Failover Runbook, and incidents PAY-1224 and PAY-1260.
