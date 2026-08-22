---
fixture_id: runbook-dns-recovery-001
title: Internal DNS Recovery Runbook
document_type: runbook
department: technology
access_level: internal
created_date: 2025-03-07
---

# Internal DNS Recovery Runbook

## Purpose and scope

Use this procedure for elevated SERVFAIL, NXDOMAIN, or resolver latency affecting bank services.

## Operational detail

Compare authoritative records, resolver cache state, and recent zone changes. Restore the last signed zone and flush caches in controlled waves.

## Controls and response

Do not hardcode service IP addresses. Validate both primary and recovery resolvers before closing the incident.

## Related evidence

See incident PAY-1103 and Payment Gateway Architecture.
