---
fixture_id: spec-mobile-banking-001
title: Mobile Banking Session Specification
document_type: product_specification
department: digital-banking
access_level: internal
created_date: 2025-03-15
---

# Mobile Banking Session Specification

## Purpose and scope

Mobile sessions use short-lived access tokens and device-bound refresh credentials.

## Operational detail

High-risk actions require step-up authentication. Session recovery must not reveal whether an account exists.

## Controls and response

Security telemetry records outcome codes and correlation IDs without credentials or full customer identifiers.

## Related evidence

See Identity and Access Architecture.
