---
fixture_id: incident-payments-006
title: PAY-1198 Database Connection Saturation
document_type: incident
department: payments
access_level: confidential
created_date: 2025-11-14
---

# PAY-1198 Database Connection Saturation

## Purpose and scope

A promotional campaign produced elevated payment timeouts for 42 minutes.

## Operational detail

Load testing modeled average traffic but not the burst. API pools again consumed the shared database ceiling, matching the capacity pattern in PAY-1042.

## Controls and response

Traffic shaping and a global connection budget were deployed. Burst profiles were added to resilience tests.

## Related evidence

Root cause: connection-pool saturation under unmodeled burst load.
