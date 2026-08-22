---
fixture_id: arch-event-streaming-001
title: Enterprise Event Streaming Architecture
document_type: architecture
department: technology
access_level: internal
created_date: 2025-02-14
---

# Enterprise Event Streaming Architecture

## Purpose and scope

Kafka carries payment, notification, audit, and fraud events between bounded services.

## Operational detail

Topics use twelve partitions by default, schema-registry compatibility checks, and consumer groups per workload. Dead-letter topics retain failed messages for fourteen days.

## Controls and response

Lag alerts fire at 50,000 messages or five minutes. Producers use idempotence and consumers commit offsets only after durable processing.

## Related evidence

See Queue Backlog Runbook and incident PAY-1170.
