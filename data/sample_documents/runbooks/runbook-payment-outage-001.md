---
fixture_id: runbook-payment-outage-001
title: Payment Failure Outage Runbook
document_type: runbook
department: payments
access_level: confidential
created_date: 2025-01-22
---

# Payment Failure Outage Runbook

## Purpose and scope

Use this runbook when payment success falls below 97 percent for five minutes or latency exceeds two seconds.

## Operational detail

Check database pool saturation, service DNS errors, certificate validity, downstream authorization health, and payment-events lag in that order. Compare failures by pod and deployment version.

## Controls and response

Reduce traffic or roll back before increasing connection limits. Drain unhealthy pods gradually. Declare severity one when customer failures exceed ten percent.

## Related evidence

See Payment Gateway Architecture and incidents PAY-1042, PAY-1077, PAY-1103, PAY-1148, and PAY-1170.
