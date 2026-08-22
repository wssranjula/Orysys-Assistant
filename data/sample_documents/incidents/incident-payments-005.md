---
fixture_id: incident-payments-005
title: PAY-1170 Payment Event Consumer Backlog
document_type: incident
department: payments
access_level: confidential
created_date: 2025-09-03
---

# PAY-1170 Payment Event Consumer Backlog

## Purpose and scope

Payment confirmations were delayed by up to eighteen minutes although authorization remained available.

## Operational detail

A poison message caused repeated deserialization failures in one consumer partition. Retries blocked subsequent events for the same partition.

## Controls and response

The message was quarantined, the consumer was patched, and bounded retries with dead-letter routing were enabled.

## Related evidence

Root cause: unbounded retry of an incompatible event.
