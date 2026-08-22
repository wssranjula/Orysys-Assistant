---
fixture_id: runbook-queue-backlog-001
title: Payment Event Queue Backlog Runbook
document_type: runbook
department: payments
access_level: confidential
created_date: 2025-03-22
---

# Payment Event Queue Backlog Runbook

## Purpose and scope

Use this runbook when payment-events consumer lag exceeds five minutes.

## Operational detail

Identify the slow consumer group, compare processing latency and error rates, and confirm partition balance. Scale consumers only within downstream capacity.

## Controls and response

Pause nonessential replay jobs, isolate poison messages to the dead-letter topic, and preserve ordering for each payment key.

## Related evidence

See Enterprise Event Streaming Architecture and incident PAY-1170.
