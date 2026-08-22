---
fixture_id: incident-identity-001
title: IAM-301 Delayed Access Revocation
document_type: incident
department: security
access_level: restricted
created_date: 2025-04-09
---

# IAM-301 Delayed Access Revocation

## Purpose and scope

A terminated vendor account remained active for six hours after HR status changed.

## Operational detail

A reconciliation job failed silently after a schema change. No unauthorized activity was observed.

## Controls and response

Security revoked the account, repaired the job, and added a missing-run alert plus token revocation checks.

## Related evidence

Root cause: identity reconciliation monitoring gap.
