---
fixture_id: incident-payments-007
title: PAY-1224 Regional Failover Duplicate Authorizations
document_type: incident
department: payments
access_level: confidential
created_date: 2026-01-17
---

# PAY-1224 Regional Failover Duplicate Authorizations

## Purpose and scope

A primary-region network fault triggered failover; authorization recovered in four minutes, but 214 payment attempts produced duplicate pending authorizations.

## Operational detail

The initial incident update blamed downstream timeouts. The completed investigation found that the recovery edge rebuilt retry requests without the original idempotency header, while an unbounded gateway retry overlapped client retries. Settlement itself did not create the duplicates.

## Controls and response

Cross-region retries were disabled, affected records were reconciled, and gateway header-preservation tests were added. Action OR-44 to add an explicit runbook checkpoint remained open after closure.

## Related evidence

Root cause: loss of idempotency context during regional failover, amplified by overlapping retries. Supersedes the initial vendor-timeout hypothesis.
