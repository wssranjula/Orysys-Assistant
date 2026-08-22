---
fixture_id: incident-payments-004
title: PAY-1148 Expired Authorization Certificate
document_type: incident
department: payments
access_level: confidential
created_date: 2025-07-19
---

# PAY-1148 Expired Authorization Certificate

## Purpose and scope

Card authorization handshakes failed for 31 minutes after a service certificate expired.

## Operational detail

The certificate inventory omitted a legacy listener and expiry alerts covered only the primary endpoint.

## Controls and response

The certificate was renewed, all listeners were inventoried, and centralized thirty-day expiry alerts became mandatory.

## Related evidence

Root cause: incomplete certificate inventory and monitoring.
