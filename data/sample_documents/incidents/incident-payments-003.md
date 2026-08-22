---
fixture_id: incident-payments-003
title: PAY-1103 Service DNS Resolution Failure
document_type: incident
department: payments
access_level: confidential
created_date: 2025-05-06
---

# PAY-1103 Service DNS Resolution Failure

## Purpose and scope

Payment authorization calls failed from two application zones for 19 minutes.

## Operational detail

An incomplete signed-zone deployment caused SERVFAIL responses for the authorization service name. Pods retried and amplified resolver load.

## Controls and response

Operations restored the prior zone and flushed caches gradually. Zone publication now requires cross-zone validation.

## Related evidence

Root cause: partial DNS zone deployment.
