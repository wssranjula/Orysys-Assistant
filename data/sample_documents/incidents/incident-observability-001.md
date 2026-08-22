---
fixture_id: incident-observability-001
title: OBS-410 Telemetry Collector Backpressure
document_type: incident
department: technology
access_level: internal
created_date: 2025-06-02
---

# OBS-410 Telemetry Collector Backpressure

## Purpose and scope

Application logs arrived up to twenty-five minutes late during a regional incident.

## Operational detail

Collectors buffered unbounded batches while the export endpoint throttled traffic.

## Controls and response

Bounded queues, sampling for low-value debug logs, and collector saturation alerts were introduced.

## Related evidence

Root cause: unbounded telemetry buffering.
