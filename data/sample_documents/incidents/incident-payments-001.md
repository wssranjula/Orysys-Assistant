---
fixture_id: incident-payments-001
title: PAY-1042 Payment API Connection Pool Exhaustion
document_type: incident
department: payments
access_level: confidential
created_date: 2025-01-28
---

# PAY-1042 Payment API Connection Pool Exhaustion

## Purpose and scope

Payment success fell to 82 percent for 37 minutes during salary processing.

## Operational detail

Autoscaling added API pods whose aggregate database connections exceeded the database ceiling. Requests queued and timed out while database CPU remained moderate.

## Controls and response

The team rolled back the autoscaling change, drained excess pods, and capped total pool allocation. A capacity guard and pool-saturation alert were added.

## Related evidence

Root cause: uncoordinated connection-pool capacity. Related to PAY-1077.
