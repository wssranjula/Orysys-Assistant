---
fixture_id: runbook-certificate-renewal-001
title: Service Certificate Renewal Runbook
document_type: runbook
department: technology
access_level: internal
created_date: 2025-02-20
---

# Service Certificate Renewal Runbook

## Purpose and scope

Renew service certificates at least thirty days before expiry.

## Operational detail

Inventory every listener and trust store, request a certificate through the approved PKI workflow, deploy to canary pods, and verify the complete chain.

## Controls and response

Monitor handshake failures and expiry metrics. Never disable certificate validation as a workaround.

## Related evidence

See incident PAY-1148 and Payment Platform Change Policy.
