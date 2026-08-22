---
fixture_id: incident-payments-002
title: PAY-1077 Payment API Pool Regression
document_type: incident
department: payments
access_level: confidential
created_date: 2025-03-11
---

# PAY-1077 Payment API Pool Regression

## Purpose and scope

Intermittent payment timeouts affected 9 percent of requests for 24 minutes.

## Operational detail

A library upgrade changed idle connection recycling. Stale connections accumulated and replacement bursts exhausted the same database-wide connection budget seen in PAY-1042.

## Controls and response

The upgrade was reverted and pool lifecycle tests were added to the release gate.

## Related evidence

Root cause: connection-pool lifecycle regression; recurring theme with PAY-1042.
