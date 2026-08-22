---
fixture_id: incident-payments-010
title: PAY-1288 Salary-Day Connection Budget Bypass
document_type: incident
department: payments
access_level: confidential
created_date: 2026-06-30
---

# PAY-1288 Salary-Day Connection Budget Bypass

## Purpose and scope

Payment success fell to 88 percent for twenty-eight minutes during salary processing despite the global connection-budget control being reported complete.

## Operational detail

A legacy reconciliation scheduler bypassed the allocation controller and opened 180 connections. Simultaneous DNS retries increased API concurrency, exhausting the remaining database ceiling; neither factor alone reproduced the outage.

## Controls and response

Reconciliation was paused, DNS retries were bounded, and the scheduler was placed behind the controller. The control-completion evidence was reopened because batch workloads had not been tested.

## Related evidence

Root cause: incomplete connection-budget enforcement combined with retry amplification. The failure recurred from PAY-1042 and PAY-1198 through an ungoverned workload.
