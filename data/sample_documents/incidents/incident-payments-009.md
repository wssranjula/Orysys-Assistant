---
fixture_id: incident-payments-009
title: PAY-1260 Recovery Drill False-Green Declaration
document_type: incident
department: payments
access_level: confidential
created_date: 2026-04-12
---

# PAY-1260 Recovery Drill False-Green Declaration

## Purpose and scope

A resilience drill declared recovery after six minutes even though settlement in the recovery region remained backlogged for fifty-four minutes.

## Operational detail

The command dashboard showed authorization probes from both regions but settlement lag and traces were collected only from the primary region. The runbook allowed authorization recovery to be mistaken for full platform recovery.

## Controls and response

The declaration was corrected, the backlog was drained under reconciliation controls, and recovery-region settlement telemetry became a release requirement.

## Related evidence

Root cause: incomplete recovery success criteria and a regional observability blind spot. This exposed an unresolved control from PAY-1224.
