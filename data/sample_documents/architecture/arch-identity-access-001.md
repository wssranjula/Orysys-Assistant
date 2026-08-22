---
fixture_id: arch-identity-access-001
title: Identity and Access Architecture
document_type: architecture
department: security
access_level: confidential
created_date: 2025-02-25
---

# Identity and Access Architecture

## Purpose and scope

Workforce identity uses centralized SSO, MFA, and role-based entitlements.

## Operational detail

Service identities use short-lived workload credentials. Human production access flows through a managed jump host and is reconciled against HR status daily.

## Controls and response

Authorization context is derived by trusted services; clients cannot submit roles, departments, or access classifications.

## Related evidence

See Vendor Access Policy and incident IAM-301.
